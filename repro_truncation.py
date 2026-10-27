#!/usr/bin/env python3
"""Reproduce MGA's output truncation: no predictions for reference steps 5-11.

⚠️  HISTORIC AS OF 2026-08-11 — READ THIS FIRST
================================================================================
The defect this script reproduces HAS BEEN FIXED, and the blueprints it targets
no longer behave as described. Kept because the committed baselines are the
evidence for the finding, and because the offline path still works.

  WHAT STILL WORKS
    --analyse on a committed reference file. No API key, no GPU, no network.
    That is the majority of this script's value and it is unaffected.

  WHAT NO LONGER HOLDS
    * max_new_tokens IS now wired on the canonical blueprint and defaults to
      2048, not 256. The central claim below — that setting it does nothing —
      was true of the blueprint of the day and is false now.
    * The comparison blueprint blp_76kyqm4vjp9pt8tvfz8tks7x6t is UNRUNNABLE. It
      wires generation parameters into its own fusion node that the node schema
      no longer accepts, so any run against it dies one second after the pod
      starts with `resolving blueprint: invalid config for 1 node(s)`.
    * A live run therefore reproduces nothing: against the current blueprint the
      symptoms are absent, and against the old one the job fails outright.

  WHAT REPLACED IT
    That was followed by the same class of defect one parameter over: `prompt`
    was hardcoded to "…10 steps or less", capping the manual at 10 steps by
    wording rather than content and bundling 3.2 actions into the average step
    against 1.7 with a caller prompt. BOTH ARE FIXED — `prompt` is settable again
    as of 2026-08-12 (PLDEV-1730), so this script no longer reproduces either
    defect. See reference/COMPARISON-prompt-honoured-vs-hardcoded.md.

  DO NOT "FIX" THIS SCRIPT BY UPDATING THE SYMPTOMS
    It is a record of a specific defect against specific committed outputs. Its
    wired-value preflight reads the blueprint document rather than hardcoding key
    names, so it already reports max_new_tokens as honoured rather than NOT WIRED
    with no code change — and it will do the same for prompt when that lands.
================================================================================

Single file, stdlib only, self-contained — the reference step intervals are
embedded, so no CrossTask download is needed to check the symptoms.

    ATAI_API_KEY / ATAI_API_ENDPOINT in .env next to this file

    python3 repro_truncation.py --analyse out.json       # offline, no API calls
    python3 repro_truncation.py --download-only          # just fetch the video
    python3 repro_truncation.py                          # fetch if needed, run, analyse
    python3 repro_truncation.py --max-new-tokens 1024    # show the value does nothing

WHAT YOU SHOULD SEE

MGA emits 6 steps covering 16.0-85.0 s of a 173.2 s video. Reference steps 5-11
(89.0-132.2 s) get no prediction at all, and the 6th step's text ends without
terminal punctuation while all five before it end with a full stop — the
signature of hitting an output-token cap mid-generation, rather than the model
judging itself finished.

The script prints each symptom as a CONFIRMED / NOT REPRODUCED line, so a
colleague can tell at a glance whether they see the same thing.

WHY --max-new-tokens 1024 WAS WORTH RUNNING  (no longer true — see the banner)

At the time, `max_new_tokens` was the parameter that would raise the cap, and it
was accepted by the API while having no effect: the `mga` blueprint's fusion node
config was only `{model: ...}` and never referenced `${values.max_new_tokens}`, so
the value was stored on the bundle and wired to nothing.

The fusion node now reads `max_new_tokens: ${values.max_new_tokens}`, so this
demonstration no longer demonstrates anything. The preflight will say so.

You do not need two runs to show this. Generation is deterministic and the
baseline output is committed to this repo, so a single run with the value set,
producing the committed baseline digest, proves the value changed nothing.

Costs ~15 min per run: ~7.5 min of model download/load on every run (nothing is
cached), then ~2.5x realtime processing. Run these one at a time — dev does
not reliably serialize them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

VIDEO_URL = "https://www.youtube.com/watch?v=i2JWkDyg26A"
VIDEO_PATH = "data/clips/tire_i2JWkDyg26A.mp4"  # gitignored: videos are not ours to redistribute
# The ACTIVE canonical blueprint (is_canonical: true, is_active: true) and the
# newest of the four mga versions. This is the default: it is what ships.
BLUEPRINT_ACTIVE = "blp_0zmce3qnjp8659dp1pszdqbtx4"
# A superseded version (is_active: false) that still accepts bundles and runs. It
# exposes max_new_tokens, prompt, temperature, top_k, top_p, repetition_penalty,
# seed and stop. DIAGNOSIS ONLY — nothing built on an inactive blueprint can ship.
BLUEPRINT_WITH_PARAMS = "blp_76kyqm4vjp9pt8tvfz8tks7x6t"
DURATION_S = 173.2
# md5 of the output this repo files, at max_frames=64 with no max_new_tokens.
REFERENCE_MD5 = "080e551a88f0bdcd857e48557366ae60"
# md5 of the video itself. The output digest is only meaningful if the input bytes
# match, so this is checked before any comparison is trusted. yt-dlp has been
# reproducible here (formats 396+140, 5,678,742 bytes, 640x360, 25 fps, 4,329
# frames), but format selection can drift as YouTube changes what it offers.
VIDEO_MD5 = "5771f46d637314fdad86d6c4c934dbb5"

# CrossTask task 40567 "Change a Tire", video i2JWkDyg26A. Intervals are merged
# across the two independent annotators (earliest start, latest end).
# Steps 9 and 10 legitimately overlap — the procedure repeats steps.
REFERENCE_STEPS = [
    (1,  "brake on",        14.8,  28.9),
    (2,  "get things out",  35.7,  41.8),
    (3,  "start loose",     46.5,  63.9),
    (4,  "jack up",         73.8,  88.9),
    (5,  "unscrew wheel",   89.0,  91.7),
    (6,  "withdraw wheel",  91.8,  94.4),
    (7,  "put wheel",       95.8,  99.7),
    (8,  "screw wheel",    100.4, 104.4),
    (9,  "jack down",      104.5, 117.9),
    (10, "tight wheel",    108.6, 132.2),
    (11, "put things back",120.9, 125.6),
]
# Last annotated reference step ends here. Coverage is judged against this, not
# against DURATION_S: the tail of the video is outro with no steps in it, so
# "uncovered" measured to 173.2 s penalises output that is actually complete.
REFERENCE_SPAN_END = 132.2

TERMINAL_EVENTS = ("pod.terminated", "job.completed", "job.failed", "job.canceled")


def load_dotenv(path: str = ".env") -> None:
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def env() -> tuple[str, str]:
    load_dotenv()
    key, ep = os.environ.get("ATAI_API_KEY"), (os.environ.get("ATAI_API_ENDPOINT") or "").rstrip("/")
    if not key or not ep:
        sys.exit("set ATAI_API_KEY and ATAI_API_ENDPOINT in .env")
    return key, ep


def api(method: str, url: str, body=None, raw: bool = False):
    key, _ = env()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
            return payload if raw else json.loads(payload or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')}")


def upload(path: str) -> str:
    key, ep = env()
    b = uuid.uuid4().hex
    body = b"".join([
        f"--{b}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'.encode(),
        b"Content-Type: video/mp4\r\n\r\n", open(path, "rb").read(),
        f"\r\n--{b}--\r\n".encode(),
    ])
    req = urllib.request.Request(f"{ep if ep.endswith('/api') else ep + '/v0.5'}/files", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={b}")
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())["file_id"]


def wiring(bp: dict) -> dict:
    """Which value keys does this blueprint actually consume?

    A value is only honoured if it appears in `values` AND some node or connector
    config references it as ${values.<key>}. The API accepts arbitrary keys in a
    bundle's `values` and silently ignores unreferenced ones, which is how
    `max_new_tokens: 1024` can return HTTP 201 and change nothing. Reading the
    blueprint document is the only way to tell in advance.
    """
    doc = bp["document"]
    blob = json.dumps({"nodes": doc.get("nodes"),
                       "connectors": doc.get("connectors")})
    out = {}
    for k in (doc.get("values") or {}):
        ref = "${values." + k + "}"
        out[k] = blob.find(ref) >= 0
    return out


def print_config(values: dict, video: str, bp: dict) -> None:
    """Show what is being sent AND whether the blueprint will honour it."""
    wired = wiring(bp)
    doc = bp["document"]
    print(f"\n{'='*78}\nCONFIGURATION\n{'='*78}")
    print(f"  video           {video}")
    state = "ACTIVE — this is what ships" if bp["is_active"] else \
            "INACTIVE (superseded) — DIAGNOSIS ONLY, results cannot ship"
    print(f"  blueprint       {bp['id']}\n"
          f"                  key={bp['blueprint_key']}  canonical={bp['is_canonical']}  {state}")

    for k, v in values.items():
        if k not in doc.get("values", {}):
            note = "NOT A VALUE on this blueprint — accepted by the API, ignored"
        elif not wired.get(k):
            note = "declared but NOT WIRED to any node — accepted, ignored"
        else:
            note = "honoured"
        shown = json.dumps(v)
        if len(shown) > 46:
            shown = shown[:43] + '..."'
        print(f"  {k:<15} {shown:<48} -> {note}")

    # The instruction actually in force, resolved the same way the platform does.
    src = doc["connectors"]["source"]["config"]
    dt = src.get("default_text", "")
    if dt.startswith("${values."):
        key = dt[len("${values."):-1]
        eff = values.get(key, doc["values"].get(key))
        origin = "from your --prompt" if key in values else f"blueprint default for values.{key}"
    else:
        eff = dt
        origin = "HARDCODED in the blueprint — not settable from a bundle"
    print(f"\n  instruction in force ({origin}):")
    print(f"    {json.dumps(eff)[:300]}")
    print(f"\n  bundle values sent: {json.dumps(values)[:200]}")

    inert = [k for k in values if k in doc.get("values", {}) and not wired.get(k)] + \
            [k for k in values if k not in doc.get("values", {})]
    if inert:
        print(f"\n  WARNING: {inert} will have NO effect on this blueprint.")
        if not bp["is_active"]:
            pass
        elif any(k in ("max_new_tokens", "prompt") for k in inert):
            print(f"  The superseded {BLUEPRINT_WITH_PARAMS} does wire these —\n"
                  f"  pass --blueprint {BLUEPRINT_WITH_PARAMS} to try them (diagnosis only).")


def run_once(file_id: str, values: dict, label: str, out_path: str,
             blueprint: str) -> str | None:
    """One full MGA run. Note: /agents/bundles is PLURAL, and /run returns 202."""
    _, ep = env()
    print(f"\n=== {label}: values={json.dumps(values)} ===")
    bundle = api("POST", f"{ep}/agents/bundles",
                 body={"blueprint": blueprint, "name": f"MGA repro {label}",
                       "values": values})
    print(f"  bundle={bundle['id']} status={bundle.get('status')}")
    agent = api("POST", f"{ep}/agents/bundles/{bundle['id']}/run",
                body={"connectors": {"source": [{"type": "file", "id": file_id, "format": "mp4"}]}})
    agent_id = agent["id"]
    print(f"  agent={agent_id}  (watching /logs; ~7.5 min of model loading first)")

    seen: set[str] = set()
    deadline = time.time() + 3600
    verdict = "timeout"
    while time.time() < deadline:
        rows = sorted(api("GET", f"{ep}/agents/instances/{agent_id}/logs?limit=500").get("data", []),
                      key=lambda r: str(r.get("created_at")))
        for r in rows:
            m = f"{r.get('created_at')}|{r.get('event_type')}|{r.get('message')}"
            if m not in seen:
                seen.add(m)
                print(f"    {str(r.get('created_at'))[11:19]} {str(r.get('level')):7} {r.get('message')}")
        if rows and str(rows[-1].get("event_type")) in TERMINAL_EVENTS:
            verdict = "failed" if any(str(r.get("level")) == "ERROR" for r in rows) else "completed"
            break
        time.sleep(20)
    print(f"  run {verdict}")
    if verdict != "completed":
        return None

    results = api("GET", f"{ep}/agents/instances/{agent_id}/results").get("data") or []
    if not results:
        print("  no results")
        return None
    ref = (results[0].get("data") or {}).get("ref")
    payload = api("GET", f"{ep if ep.endswith('/api') else ep + '/v0.5'}{ref}", raw=True)
    open(out_path, "wb").write(payload)
    print(f"  saved {out_path}  ({len(payload)} B, md5 {hashlib.md5(payload).hexdigest()})")
    return out_path


def analyse(path: str) -> int:
    """Print each symptom as CONFIRMED / NOT REPRODUCED. Returns problems found."""
    raw = open(path, "rb").read()
    rec = json.loads(raw.decode().strip().splitlines()[0])
    res = rec["results"]
    digest = hashlib.md5(raw).hexdigest()

    print(f"\n{'='*78}\nANALYSIS of {path}\n{'='*78}")
    print(f"  md5 {digest}"
          + ("  (identical to the filed reference run)" if digest == REFERENCE_MD5 else ""))
    print(f"\n  {len(res)} steps emitted:")
    for r in res:
        print(f"    [{r['timestamp_start']:>6.1f} - {r['timestamp_end']:>6.1f}s]  {r['instruction']}")

    found = 0
    print(f"\n  {'-'*74}\n  SYMPTOM 1 — coverage stops before the procedure ends\n  {'-'*74}")
    last = max(r["timestamp_end"] for r in res)
    print(f"    last prediction ends     : {last:.1f}s")
    print(f"    last reference step ends : {REFERENCE_SPAN_END:.1f}s")
    print(f"    video duration           : {DURATION_S:.1f}s  (the tail is outro, no steps in it)")
    shortfall = REFERENCE_SPAN_END - last
    if shortfall > 5:
        print(f"    procedure left uncovered : {shortfall:.1f}s "
              f"({100*shortfall/REFERENCE_SPAN_END:.0f}% of the annotated procedure)")
        print("    => CONFIRMED"); found += 1
    else:
        print(f"    => NOT REPRODUCED — output reaches the end of the annotated procedure")

    print(f"\n  {'-'*74}\n  SYMPTOM 2 — reference steps 5-11 get no prediction\n  {'-'*74}")
    missed = []
    for n, name, a, b in REFERENCE_STEPS:
        ov = max(0.0, min(b, max(r["timestamp_end"] for r in res)) - a)
        hit = any(min(b, r["timestamp_end"]) - max(a, r["timestamp_start"]) > 0 for r in res)
        if not hit:
            missed.append(n)
        print(f"    step {n:>2} {name:<16} {a:>6.1f}-{b:<6.1f} "
              f"{'covered' if hit else 'NO PREDICTION'}")
    print(f"    missed: {missed}  ({len(missed)}/{len(REFERENCE_STEPS)} reference steps)")
    if missed == [5, 6, 7, 8, 9, 10, 11]:
        print("    => CONFIRMED (exactly steps 5-11, as filed)"); found += 1
    elif len(missed) >= 4:
        print(f"    => LARGELY REPRODUCED (missed {missed}; filed report says 5-11)"); found += 1
    elif missed:
        print(f"    => LARGELY RESOLVED — only {missed} missed, vs 5-11 in the filed report.")
        print("       Not counted as reproducing the defect.")
    else:
        print("    => NOT REPRODUCED — every reference step covered")

    print(f"\n  {'-'*74}\n  SYMPTOM 3 — final step is cut mid-clause (the token-cap signature)\n  {'-'*74}")
    for r in res:
        t = r["instruction"]
        print(f"    step {r['step']}: ends {t[-1]!r}  {'<-- no terminal punctuation' if t[-1] not in '.!?' else ''}")
    tail = res[-1]["instruction"]
    others_ok = all(r["instruction"][-1] in ".!?" for r in res[:-1])
    if tail[-1] not in ".!?" and others_ok:
        print(f"    last step: {tail!r}")
        print("    => CONFIRMED (all earlier steps terminate cleanly; the last does not)"); found += 1
    else:
        print("    => NOT REPRODUCED")

    chars = sum(len(r["instruction"]) for r in res)
    words = sum(len(r["instruction"].split()) for r in res)
    truncated = res[-1]["instruction"][-1] not in ".!?"
    print(f"\n  generated volume: {len(res)} steps, {words} words, {chars} chars")
    if truncated:
        print("  The final step is unterminated, so generation was cut off. On the active"
              "\n  blueprint the cap is a hardcoded max_new_tokens: 256.")
    else:
        print("  Generation terminated cleanly — the output budget was not the limit here.")
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", default=VIDEO_PATH)
    ap.add_argument("--download", action="store_true",
                    help="(no longer needed — the video is fetched automatically "
                         "when missing; kept so older invocations still work)")
    ap.add_argument("--download-only", action="store_true",
                    help="fetch the video into data/clips/ and stop — no API calls")
    ap.add_argument("--max-frames", type=int, default=64)
    ap.add_argument("--blueprint", default=BLUEPRINT_ACTIVE,
                    help=f"blueprint id or key. Default {BLUEPRINT_ACTIVE} — the "
                         f"ACTIVE canonical mga, i.e. what ships. Pass "
                         f"{BLUEPRINT_WITH_PARAMS} to reach a superseded version "
                         f"that wires max_new_tokens and prompt (diagnosis only).")
    ap.add_argument("--prompt", default=None,
                    help="set values.prompt. Only exists on a blueprint that "
                         "declares it — the active one hardcodes its instruction.")
    ap.add_argument("--max-new-tokens", type=int, default=None,
                    help="add max_new_tokens to the bundle values. Omitted by "
                         "default, which reproduces the as-shipped behaviour. Set "
                         "it to 1024 to see that the value is accepted and has no "
                         "effect — the output is unchanged.")
    ap.add_argument("--analyse", metavar="JSONL", help="offline: analyse an existing output")
    args = ap.parse_args()

    if args.analyse:
        n = analyse(args.analyse)
        print(f"\n{n}/3 symptoms confirmed.")
        return

    # A live run costs ~15 minutes and reproduces nothing any more. Say so BEFORE
    # spending it, not in a docstring nobody opened.
    print(
        "\n  ⚠️  This reproduces a defect that has since been FIXED.\n"
        "     max_new_tokens is now wired on the canonical blueprint (default 2048),\n"
        "     so the symptoms below will not appear. The comparison blueprint\n"
        "     blp_76kyqm4vjp9pt8tvfz8tks7x6t no longer resolves at all.\n"
        "     Offline analysis of the committed baselines still works and is free:\n"
        f"       python3 {os.path.basename(__file__)} --analyse "
        "reference/mga-output-i2JWkDyg26A-mf64.json\n"
        "     Continuing in 5s — Ctrl-C to stop.\n"
    )
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        sys.exit("  stopped.")

    bp = api("GET", f"{env()[1]}/agents/blueprints/{args.blueprint}")
    values = {"max_frames": args.max_frames}
    if args.max_new_tokens is not None:
        values["max_new_tokens"] = args.max_new_tokens
    if args.prompt is not None:
        values["prompt"] = args.prompt
    print_config(values, args.video, bp)

    # The video is a single fixed 5.7 MB asset whose URL is baked in, so fetch it
    # automatically when it is missing rather than making the caller pass a flag.
    # data/ is gitignored, so the clip cannot be committed by accident.
    if not os.path.exists(args.video):
        if not any(os.access(os.path.join(d, "yt-dlp"), os.X_OK)
                   for d in os.environ.get("PATH", "").split(os.pathsep)):
            sys.exit(f"no video at {args.video}, and yt-dlp is not installed.\n"
                     f"  Install yt-dlp, or download it manually and save it there:\n"
                     f"  {VIDEO_URL}")
        os.makedirs(os.path.dirname(args.video) or ".", exist_ok=True)
        print(f"fetching the video (5.7 MB) -> {args.video} ...")
        try:
            subprocess.run(["yt-dlp", "--no-warnings", "-f",
                            "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                            "--merge-output-format", "mp4", "-o", args.video,
                            VIDEO_URL], check=True)
        except subprocess.CalledProcessError:
            # YouTube rate-limits repeated pulls and answers 403 for a while.
            # Nothing about the platform is wrong here, so say so plainly rather
            # than surfacing a traceback.
            sys.exit(f"\nyt-dlp could not fetch the video.\n"
                     f"  A 403 here is usually YouTube rate-limiting — wait a few "
                     f"minutes and retry,\n  or try `yt-dlp -U` to update.\n"
                     f"  You can also download it by hand and save it as "
                     f"{args.video}:\n    {VIDEO_URL}\n"
                     f"  Meanwhile the analysis needs no video at all:\n"
                     f"    python3 {os.path.basename(__file__)} --analyse "
                     f"reference/mga-output-i2JWkDyg26A-mf64.json")
        if not os.path.exists(args.video):
            sys.exit(f"yt-dlp reported success but {args.video} is missing")
    else:
        print(f"using {args.video} ({os.path.getsize(args.video) / 1e6:.1f} MB)")

    video_md5 = hashlib.md5(open(args.video, "rb").read()).hexdigest()
    if video_md5 == VIDEO_MD5:
        print(f"  video md5 {video_md5}  (matches the reference input)")
    else:
        print(f"  video md5 {video_md5}\n"
              f"  NOTE: differs from the reference input ({VIDEO_MD5}). The three\n"
              f"  symptoms should still reproduce, but the OUTPUT digest will not\n"
              f"  match, and that is expected rather than a failed reproduction.")

    if args.download_only:
        return

    print(f"uploading {args.video} ...")
    file_id = upload(args.video)
    print(f"  file_id={file_id}")

    out = run_once(file_id, values, "run", "data/repro.json", bp["id"])
    if not out:
        sys.exit("the run did not complete — see the log output above")
    n = analyse(out)
    print(f"\n{n}/3 symptoms confirmed.")

    # One run is enough to show max_new_tokens is inert, because generation is
    # deterministic and the baseline output is committed to this repo. If the
    # digest still matches the baseline that omitted the value entirely, the
    # value changed nothing. Only meaningful at max_frames=64, which is what the
    # committed baseline was produced with.
    if args.max_new_tokens is not None and bp["id"] == BLUEPRINT_ACTIVE \
            and args.prompt is None:
        digest = hashlib.md5(open(out, "rb").read()).hexdigest()
        print(f"\n{'='*78}\nDid max_new_tokens={args.max_new_tokens} change anything?"
              f"\n{'='*78}")
        if args.max_frames != 64:
            print(f"  cannot tell: the committed baseline is max_frames=64, you ran "
                  f"{args.max_frames}.\n  Re-run at --max-frames 64 to compare.")
        elif digest == REFERENCE_MD5:
            print(f"  this run   md5 {digest}")
            print(f"  baseline   md5 {REFERENCE_MD5}   "
                  f"(reference/mga-output-i2JWkDyg26A-mf64.json, no max_new_tokens)")
            print("  => IDENTICAL. max_new_tokens was accepted by the API (HTTP 201,\n"
                  "     echoed back in the bundle values) and had NO effect. The mga\n"
                  "     blueprint's fusion node config is only {model: ...} and never\n"
                  "     references ${values.max_new_tokens}, so it is wired to nothing.")
        else:
            print(f"  this run   md5 {digest}")
            print(f"  baseline   md5 {REFERENCE_MD5}")
            print("  => OUTPUTS DIFFER. max_new_tokens now has an effect — the\n"
                  "     blueprint has presumably been changed to wire it through.")


if __name__ == "__main__":
    main()
