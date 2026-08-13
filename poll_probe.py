#!/usr/bin/env python3
"""Attach to already-launched probe runs, then download and compare their output.

Runs launched without a client polling them still complete — outputs do not
expire — so this recovers them by agent id. It also does the actual comparison
the probe exists for: whether `max_frames` was honoured, and whether the emitted
`frame_start`/`frame_end` index the *sampled* frames or the *source* frames.

Usage:
  python3 poll_probe.py 16=agt_... 64=agt_... 512=agt_... -1=agt_...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from mga_api import agents_base, download_results, request, require_key

TERMINAL = ("completed", "failed", "canceled")
SOURCE_FRAMES = 16885   # ffprobe nb_frames for tire_zlyrv-h97z8.mp4
DURATION_S = 563.455


def summarize(path: str, max_frames: int) -> None:
    """Report what the output says about frame indexing."""
    raw = open(path, "rb").read()
    print(f"\n  ----- max_frames={max_frames}: {path} ({len(raw)} bytes) -----")
    print(raw[:2500].decode(errors="replace"))

    records = []
    for line in raw.decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not records:
        print("  (not JSONL, or empty)")
        return

    def walk(obj, out):
        """Collect any frame_start/frame_end pairs at any nesting depth."""
        if isinstance(obj, dict):
            if "frame_start" in obj or "frame_end" in obj:
                out.append((obj.get("frame_start"), obj.get("frame_end"),
                            obj.get("step") or obj.get("description")
                            or obj.get("text")))
            for v in obj.values():
                walk(v, out)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, out)
        return out

    pairs = walk(records, [])
    print(f"\n  {len(records)} JSONL record(s), {len(pairs)} frame-indexed step(s)")
    idx = [x for p in pairs for x in p[:2] if isinstance(x, (int, float))]
    if not idx:
        print("  no numeric frame indices found")
        return
    lo, hi = min(idx), max(idx)
    print(f"  frame index range observed: {lo} .. {hi}")
    print(f"    vs sampled-frame space  (0..{max_frames - 1 if max_frames > 0 else '?'})")
    print(f"    vs source-frame space   (0..{SOURCE_FRAMES - 1})")
    if max_frames > 0 and hi <= max_frames:
        scale = DURATION_S / max_frames
        verdict = f"SAMPLED-frame indices -> multiply by {scale:.2f}s to get seconds"
    elif hi > max_frames > 0:
        scale = DURATION_S / SOURCE_FRAMES
        verdict = f"SOURCE-frame indices -> multiply by {scale:.5f}s to get seconds"
    else:
        verdict = "ambiguous"
    print(f"  => {verdict}")
    for a, b, label in pairs[:15]:
        print(f"     [{a}, {b}]  {str(label)[:90]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("specs", nargs="+", help="max_frames=agent_id pairs")
    ap.add_argument("--outdir", default="discovery/probe")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=90 * 60)
    args = ap.parse_args()

    require_key()
    jobs = {}
    for spec in args.specs:
        mf, _, agent = spec.partition("=")
        if not agent:
            sys.exit(f"bad spec {spec!r}; want max_frames=agent_id")
        jobs[int(mf)] = agent

    os.makedirs(args.outdir, exist_ok=True)
    pending = dict(jobs)
    final = {}
    deadline = time.time() + args.timeout
    seen_events: dict[str, set] = {a: set() for a in jobs.values()}

    while pending and time.time() < deadline:
        for mf, agent_id in list(pending.items()):
            info = request("GET", f"{agents_base()}/instances/{agent_id}")
            status = info.get("status")
            for ev in request("GET", f"{agents_base()}/instances/{agent_id}/events").get("data", []):
                marker = f"{ev.get('created_at')}{ev.get('message')}"
                if marker not in seen_events[agent_id]:
                    seen_events[agent_id].add(marker)
                    print(f"  [mf={mf}] {ev.get('created_at', '')}  "
                          f"{ev.get('message', '')}")
            if status in TERMINAL:
                started = info.get("started_at")
                done = info.get("completed_at")
                print(f"  [mf={mf}] TERMINAL status={status}  "
                      f"{started} -> {done}")
                final[mf] = {"agent_id": agent_id, "status": status,
                             "started_at": started, "completed_at": done}
                pending.pop(mf)
        if pending:
            print(f"  {time.strftime('%H:%M:%S')} waiting on "
                  f"{ {k: v for k, v in pending.items()} }")
            time.sleep(args.interval)

    print("\n=== outputs ===")
    for mf, meta in sorted(final.items()):
        if meta["status"] != "completed":
            print(f"\n  max_frames={mf}: {meta['status']} — checking for results anyway")
        out = os.path.join(args.outdir, f"mga-mf{mf}.json")
        saved = download_results(meta["agent_id"], out)
        if saved:
            summarize(saved, mf)

    with open(os.path.join(args.outdir, "summary.json"), "w") as f:
        json.dump(final, f, indent=2)
    print(f"\nwrote {args.outdir}/summary.json")
    for mf, meta in sorted(final.items()):
        print(f"  max_frames={mf:<6} {meta['status']:<10} {meta['agent_id']}")


if __name__ == "__main__":
    main()
