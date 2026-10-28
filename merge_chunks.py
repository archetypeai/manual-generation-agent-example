#!/usr/bin/env python3
"""Stitch sweep_serve.py --chunk answers into one manual per clip, per rule, and score the tire clip.

    python3 merge_chunks.py eval_serving_c90o15
    python3 merge_chunks.py eval_serving_c90o15 --rules dedupe

Every chunk's steps come back in chunk time, and a step inside an overlap comes
back from both chunks. The merge follows manual-gen-with-manual-generation-agent
(src/lib/server/merge.ts), where it was tuned on hour-long recordings:

  1. A chunk is dropped whole when it loops (6+ steps, under 34% of them
     distinct), or when every step has zero length: its clock is lost, and its
     steps would pile up at the chunk's start. merge.ts's floor of 12 steps
     missed a 20 s tail that said "Press the 'Enter' key." 11 times.
  2. A step whose chunk-local time runs past the chunk (+5 s) is dropped, not
     clamped: the model has lost track of the video.
  3. The rest move to clip time. Frames are recomputed at the source clip's rate,
     since a re-encoded chunk has its own.
  4. The overlap's duplicates go, per rule:

  dedupe  a step is the same as a kept one only if it came from a DIFFERENT chunk,
          starts within 20 s of it, overlaps it in time, and shares 35%+ of its
          words (Jaccard over words of 3+ letters). The copy with the longer
          instruction stays, then the longer span. Text alone would merge real repeats ("Click OK" x3)
  cut     each chunk owns its span up to the middle of its overlap with the next;
          a step is kept only from the chunk that owns the step's midpoint

Steps are renumbered from 0. Each rule's manuals go to
<dir>/merged/mga-output-<clip>-<rule>.json in batch's format, then evaluate.py
scores the tire clip. Empty chunks and gaps over 20 s are reported: both leave a
well-formed manual with a hole in it. Offline: no API key, no network.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

RULES = ("dedupe", "cut")
SCORED = "tire_i2JWkDyg26A"
NEAR_SEC = 20
SIMILAR = 0.35
TIMESTAMP_SLACK_S = 5
DEGENERATE_MIN_STEPS = 6
DEGENERATE_MAX_UNIQUE_RATIO = 0.34
GAP_S = 20


def words(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9\s]", " ", text.lower()).split() if len(w) > 2}


def similarity(a: str, b: str) -> float:
    x, y = words(a), words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def is_degenerate(rows: list[dict]) -> bool:
    if len(rows) < DEGENERATE_MIN_STEPS:
        return False
    unique = {r.get("instruction", "").strip().lower() for r in rows}
    return len(unique) / len(rows) < DEGENERATE_MAX_UNIQUE_RATIO


def no_clock(rows: list[dict]) -> bool:
    return bool(rows) and all((r.get("timestamp_end") or 0) <= (r.get("timestamp_start") or 0) for r in rows)


def out_of_range(row: dict, duration: float) -> bool:
    limit = duration + TIMESTAMP_SLACK_S
    return (row.get("timestamp_start") or 0) > limit or (row.get("timestamp_end") or 0) > limit


def to_clip_time(row: dict, chunk: dict, fps: float) -> dict:
    row = dict(row)
    for t, frame in (("timestamp_start", "frame_start"), ("timestamp_end", "frame_end")):
        if row.get(t) is not None:
            row[t] = round(row[t] + chunk["offset"], 3)
            row[frame] = float(round(row[t] * fps))
    return row


def midpoint(row: dict) -> float:
    return ((row.get("timestamp_start") or 0.0) + (row.get("timestamp_end") or 0.0)) / 2


def span(row: dict) -> float:
    return row["timestamp_end"] - row["timestamp_start"]


def dedupe(rows: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for r in rows:
        dup = None
        for i in range(len(kept) - 1, -1, -1):
            k = kept[i]
            if r["timestamp_start"] - k["timestamp_start"] > NEAR_SEC:
                break
            if k["chunk"] == r["chunk"]:
                continue
            if min(k["timestamp_end"], r["timestamp_end"]) - max(k["timestamp_start"], r["timestamp_start"]) <= 0:
                continue
            if similarity(k.get("instruction", ""), r.get("instruction", "")) >= SIMILAR:
                dup = i
                break
        if dup is None:
            kept.append(r)
        elif (len(r.get("instruction", "").strip()), span(r)) > (len(kept[dup].get("instruction", "").strip()), span(kept[dup])):
            kept[dup] = r        # the chunk that saw the whole step says more, or covers more of it
    return kept


def merge(plan: dict, rule: str) -> tuple[list[dict], dict]:
    chunks = plan["chunks"]
    stats = {"degenerate": [], "no_clock": [], "out_of_range": 0, "empty": []}
    rows = []
    for i, c in enumerate(chunks):
        got = c.get("rows")
        if got is None:
            continue
        if not got:
            stats["empty"].append(i)
        if is_degenerate(got):
            stats["degenerate"].append(i)
            continue
        if no_clock(got):
            stats["no_clock"].append(i)
            continue
        usable = [r for r in got if not out_of_range(r, c["duration"])]
        stats["out_of_range"] += len(got) - len(usable)
        rows += [{**to_clip_time(r, c, plan["fps"]), "chunk": i} for r in usable]
    rows.sort(key=lambda r: (r.get("timestamp_start") or 0.0, r["chunk"]))
    if rule == "cut":
        bounds = [(c["offset"] + c["duration"] + n["offset"]) / 2 for c, n in zip(chunks, chunks[1:])]
        lo, hi = [0.0] + bounds, bounds + [float("inf")]
        rows = [r for r in rows if lo[r["chunk"]] <= midpoint(r) < hi[r["chunk"]]]
    else:
        rows = dedupe(rows)
    for n, r in enumerate(rows):
        r["step"] = n
    edges = [{"timestamp_end": 0.0}] + rows      # a dropped first chunk leaves a hole at the start
    stats["gaps"] = [(a["timestamp_end"], b["timestamp_start"]) for a, b in zip(edges, edges[1:])
                     if b["timestamp_start"] - a["timestamp_end"] > GAP_S]
    return rows, stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="a sweep_serve.py --chunk output dir, e.g. eval_serving_c90o15")
    ap.add_argument("--rules", nargs="+", choices=RULES, default=list(RULES))
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "chunks", "*.json")))
    if not files:
        sys.exit(f"no chunk answers in {args.dir}/chunks/")
    scored = []
    for path in files:
        with open(path) as f:
            plan = json.load(f)
        stem = plan["clip"]
        unanswered = sum(c.get("rows") is None for c in plan["chunks"])
        print(f"{stem}  {len(plan['chunks'])} chunks of {plan['seconds']:g} s, overlap {plan['overlap']:g} s"
              + (f"  ({unanswered} unanswered: their span has no steps)" if unanswered else ""))
        for c in plan["chunks"]:
            rows = c.get("rows")
            print(f"    {c['offset']:6.1f}-{c['offset'] + c['duration']:6.1f} s  "
                  + ("no answer" if rows is None else f"{len(rows)} steps"))
        for rule in args.rules:
            rows, stats = merge(plan, rule)
            out = os.path.join(args.dir, "merged", f"mga-output-{stem}-{rule}.json")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w") as f:
                f.write(json.dumps({"id": stem, "results": rows}, separators=(",", ":")) + "\n")
            notes = [f"{k.replace('_', ' ')} {v}" for k, v in stats.items() if v]
            print(f"  {rule:7} {len(rows)} steps" + (f"  ({'; '.join(notes)})" if notes else ""))
            if stem == SCORED:
                scored.append(out)
    if scored:
        print(flush=True)
        subprocess.run([sys.executable, "evaluate.py", "--compare", *scored])


if __name__ == "__main__":
    main()
