#!/usr/bin/env python3
"""Reproduce MGA's output truncation: no predictions for reference steps 5-11.

Single file, stdlib only, self-contained — the reference step intervals are
embedded, so no CrossTask download is needed to check the symptoms.

    ATAI_API_KEY / ATAI_API_ENDPOINT in .env next to this file

    python3 repro_truncation.py --download        # fetch video, run, analyse
    python3 repro_truncation.py                   # video already on disk
    python3 repro_truncation.py --ab              # ALSO prove max_new_tokens is inert
    python3 repro_truncation.py --analyse out.jsonl   # offline, no API calls

WHAT YOU SHOULD SEE

MGA emits 6 steps covering 16.0-85.0 s of a 173.2 s video. Reference steps 5-11
(89.0-132.2 s) get no prediction at all, and the 6th step's text ends without
terminal punctuation while all five before it end with a full stop — the
signature of hitting an output-token cap mid-generation, rather than the model
judging itself finished.

The script prints each symptom as a CONFIRMED / NOT REPRODUCED line, so a
colleague can tell at a glance whether they see the same thing.

WHY --ab MATTERS

`max_new_tokens` is the parameter that would raise the cap, and it is accepted by
the API while having no effect: the `mga` blueprint's fusion node config is only
`{model: ...}` and never references `${values.max_new_tokens}`, so the value is
stored on the bundle and wired to nothing. `--ab` runs the same video twice,
with and without it, and compares digests. Expect them to be IDENTICAL.

Costs ~15 min per run (~7.5 min of model download/load every run, nothing
cached), so --ab is ~30 min. Dev runs these one at a time.
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


def run_once(file_id: str, values: dict, label: str, out_path: str) -> str | None:
    """One full MGA run. Note: /agents/bundles is PLURAL, and /run returns 202."""
    _, ep = env()
    print(f"\n=== {label}: values={values} ===")
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
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--max-frames", type=int, default=64)
    ap.add_argument("--ab", action="store_true",
                    help="also run with max_new_tokens=1024 and compare digests")
    ap.add_argument("--analyse", metavar="JSONL", help="offline: analyse an existing output")
    args = ap.parse_args()

    if args.analyse:
        n = analyse(args.analyse)
        print(f"\n{n}/3 symptoms confirmed.")
        return

    if args.download and not os.path.exists(args.video):
        # data/ is gitignored, so the clip cannot be committed by accident.
        os.makedirs(os.path.dirname(args.video) or ".", exist_ok=True)
        print(f"downloading {VIDEO_URL} -> {args.video} ...")
        subprocess.run(["yt-dlp", "--no-warnings", "-f",
                        "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                        "--merge-output-format", "mp4", "-o", args.video, VIDEO_URL], check=True)
    if not os.path.exists(args.video):
        sys.exit(f"no video at {args.video} — pass --download to fetch it")

    print(f"uploading {args.video} ...")
    file_id = upload(args.video)
    print(f"  file_id={file_id}")

    base = run_once(file_id, {"max_frames": args.max_frames}, "A (as shipped)",
                    "data/repro-A.jsonl")
    if not base:
        sys.exit("run A did not complete — see the log output above")
    n = analyse(base)
    print(f"\n{n}/3 symptoms confirmed.")

    if args.ab:
        alt = run_once(file_id,
                       {"max_frames": args.max_frames, "max_new_tokens": 1024},
                       "B (+ max_new_tokens=1024)", "data/repro-B.jsonl")
        if alt:
            da = hashlib.md5(open(base, "rb").read()).hexdigest()
            db = hashlib.md5(open(alt, "rb").read()).hexdigest()
            print(f"\n{'='*78}\nA/B on max_new_tokens\n{'='*78}")
            print(f"  A  max_frames={args.max_frames}                     md5 {da}")
            print(f"  B  max_frames={args.max_frames}, max_new_tokens=1024  md5 {db}")
            print("  => IDENTICAL: max_new_tokens is accepted by the API and has no effect"
                  if da == db else
                  "  => OUTPUTS DIFFER: max_new_tokens now has an effect (blueprint changed?)")


if __name__ == "__main__":
    main()
