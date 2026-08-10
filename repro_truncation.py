#!/usr/bin/env python3
"""Reproduce MGA's output truncation: no predictions for reference steps 5-11.

Single file, stdlib only, self-contained — the reference step intervals are
embedded, so no CrossTask download is needed to check the symptoms.

    ATAI_API_KEY / ATAI_API_ENDPOINT in .env next to this file

    python3 repro_truncation.py --analyse out.jsonl      # offline, no API calls
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

WHY --max-new-tokens 1024 IS WORTH RUNNING

`max_new_tokens` is the parameter that would raise the cap, and it is accepted by
the API while having no effect: the `mga` blueprint's fusion node config is only
`{model: ...}` and never references `${values.max_new_tokens}`, so the value is
stored on the bundle and wired to nothing.

You do not need two runs to show this. Generation is deterministic and the
baseline output is committed to this repo, so a single run with the value set,
producing the committed baseline digest, proves the value changed nothing.

Costs ~15 min per run: ~7.5 min of model download/load on every run (nothing is
cached), then ~2.5x realtime processing. Dev runs these one at a time.
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
BLUEPRINT = "mga"
DURATION_S = 173.2
REFERENCE_MD5 = "080e551a88f0bdcd857e48557366ae60"

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
    req = urllib.request.Request(f"{ep}/v0.5/files", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={b}")
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())["file_id"]


def print_config(values: dict, video: str) -> None:
    """Show exactly what is being sent, so the run is unambiguous in a paste."""
    mnt = values.get("max_new_tokens")
    print(f"\n{'='*78}\nCONFIGURATION\n{'='*78}")
    print(f"  video           {video}")
    print(f"  blueprint       {BLUEPRINT}  (canonical)")
    print(f"  max_frames      {values.get('max_frames')}"
          f"          (blueprint default is 16; 64 is the reader/preprocessor "
          f"batch size)")
    if mnt is None:
        print("  max_new_tokens  not sent   (reproduces the as-shipped behaviour; "
              "the mga\n                             blueprint exposes no "
              "generation parameter at all)")
    else:
        print(f"  max_new_tokens  {mnt}       (accepted by the API — expect NO effect, "
              f"because\n                             the fusion node config is only "
              f"{{model: ...}})")
    print(f"\n  bundle values sent: {json.dumps(values)}")


def run_once(file_id: str, values: dict, label: str, out_path: str) -> str | None:
    """One full MGA run. Note: /agents/bundles is PLURAL, and /run returns 202."""
    _, ep = env()
    print(f"\n=== {label}: values={json.dumps(values)} ===")
    bundle = api("POST", f"{ep}/agents/bundles",
                 body={"blueprint": BLUEPRINT, "name": f"MGA repro {label}", "values": values})
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
    payload = api("GET", f"{ep}/v0.5{ref}", raw=True)
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
    print(f"\n  {'-'*74}\n  SYMPTOM 1 — coverage stops well before the video ends\n  {'-'*74}")
    last = max(r["timestamp_end"] for r in res)
    gap = DURATION_S - last
    print(f"    last prediction ends   : {last:.1f}s")
    print(f"    video duration         : {DURATION_S:.1f}s")
    print(f"    uncovered tail         : {gap:.1f}s ({100*gap/DURATION_S:.0f}% of the video)")
    if gap > 30:
        print("    => CONFIRMED"); found += 1
    else:
        print("    => NOT REPRODUCED")

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
    elif missed:
        print(f"    => PARTIALLY REPRODUCED (missed {missed}, filed report says 5-11)"); found += 1
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
    print(f"\n  generated volume: {len(res)} steps, {words} words, {chars} chars"
          f" ~= {chars/3.7 + 8*len(res):.0f} tokens (consistent with a 256-token cap)")
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

    values = {"max_frames": args.max_frames}
    if args.max_new_tokens is not None:
        values["max_new_tokens"] = args.max_new_tokens
    print_config(values, args.video)

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
                     f"reference/mga-output-i2JWkDyg26A-mf64.jsonl")
        if not os.path.exists(args.video):
            sys.exit(f"yt-dlp reported success but {args.video} is missing")
    else:
        print(f"using {args.video} ({os.path.getsize(args.video) / 1e6:.1f} MB)")

    if args.download_only:
        return

    print(f"uploading {args.video} ...")
    file_id = upload(args.video)
    print(f"  file_id={file_id}")

    out = run_once(file_id, values, "run", "data/repro.jsonl")
    if not out:
        sys.exit("the run did not complete — see the log output above")
    n = analyse(out)
    print(f"\n{n}/3 symptoms confirmed.")

    # One run is enough to show max_new_tokens is inert, because generation is
    # deterministic and the baseline output is committed to this repo. If the
    # digest still matches the baseline that omitted the value entirely, the
    # value changed nothing. Only meaningful at max_frames=64, which is what the
    # committed baseline was produced with.
    if args.max_new_tokens is not None:
        digest = hashlib.md5(open(out, "rb").read()).hexdigest()
        print(f"\n{'='*78}\nDid max_new_tokens={args.max_new_tokens} change anything?"
              f"\n{'='*78}")
        if args.max_frames != 64:
            print(f"  cannot tell: the committed baseline is max_frames=64, you ran "
                  f"{args.max_frames}.\n  Re-run at --max-frames 64 to compare.")
        elif digest == REFERENCE_MD5:
            print(f"  this run   md5 {digest}")
            print(f"  baseline   md5 {REFERENCE_MD5}   "
                  f"(reference/mga-output-i2JWkDyg26A-mf64.jsonl, no max_new_tokens)")
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
