#!/usr/bin/env python3
"""Score MGA output against CrossTask reference annotations. Offline, stdlib only.

    python3 evaluate.py reference/mga-output-i2JWkDyg26A-mf64.json
    python3 evaluate.py --compare reference/*             # the whole ladder
    python3 evaluate.py out.json --video i2JWkDyg26A --task 40567

MGA writes ONE JSON DOCUMENT per run. Both .json and the older .jsonl files in
reference/ are read.

No API key, no network, no GPU: it reads output files you already have. Reference
intervals for the example video are embedded, so it works on a fresh clone; point
`--annotations` at a CrossTask release to score other videos.

WHAT IS SCORED, AND WHY IT IS NOT JUST PRECISION AND RECALL

MGA emits prose steps over a tiled timeline; CrossTask provides a fixed reference
procedure. The two are not the same kind of object, so a naive P/R is misleading
in three specific ways this script tries to avoid.

  1. REFERENCE RECALL is meaningful. "Did the agent find each step of the
     documented procedure, and how well localized was it?" is a fair question, so
     that is the headline: reference-step recall plus temporal IoU, reported at
     several IoU thresholds the way temporal action localization normally is.

  2. PRECISION AGAINST THIS REFERENCE IS NOT MEANINGFUL. CrossTask's step list is
     declared per TASK and derived from a wikiHow article, so it is a reference
     procedure and not an inventory of the video. Real actions wikiHow omits —
     "remove the hubcap", "switch off the ignition" — cannot appear in it. Marking
     those false positives would penalise correct output, so unmatched predictions
     are counted and shown, never scored.

  3. SPOKEN CAUTIONS ARE NOT ACTIONS. Because MGA's output tiles the timeline with
     no gaps, a warning spoken during a pause has nowhere to go but its own step.
     Those are reported in their own bucket rather than treated as errors; there is
     no `kind` field in the schema to separate them properly.

Two traps this script exists to get right:

  * MGA's `step` field is 0-BASED; CrossTask reference steps are 1-BASED. Joining
    them naively is off by one and produces plausible-looking wrong alignments
    rather than an error.
  * The two annotators disagree, sometimes wildly (17x on step 1 of the example
    video). Merging min-start/max-end inflates reference durations and deflates
    IoU, so `--annotator` scores against one annotator alone for comparison.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# CrossTask task 40567 "Change a Tire": the wikiHow-derived step taxonomy, in order.
TASK_STEPS = {
    "40567": ["brake on", "get things out", "start loose", "jack up",
              "unscrew wheel", "withdraw wheel", "put wheel", "screw wheel",
              "jack down", "tight wheel", "put things back"],
}
# Embedded annotations for the example video: (step, start, end) exactly as the
# CrossTask CSV lists them, both annotators, so a fresh clone can score offline.
EMBEDDED = {
    "40567_i2JWkDyg26A": [
        (1, 25.55, 26.35), (2, 35.67, 36.67), (3, 46.45, 49.05), (3, 52.84, 63.94),
        (4, 73.82, 88.82), (5, 89.01, 91.71), (6, 91.88, 94.38), (7, 95.77, 99.67),
        (8, 100.40, 104.40), (9, 104.49, 108.49), (10, 108.56, 114.66),
        (9, 114.79, 117.89), (11, 120.94, 125.44), (10, 125.67, 132.17),
        (1, 14.75, 28.90), (2, 35.67, 41.83), (3, 46.73, 48.50), (3, 53.86, 62.91),
        (4, 74.06, 88.89), (5, 88.99, 91.69), (6, 91.80, 93.90), (7, 97.24, 99.21),
        (8, 100.46, 104.43), (9, 104.49, 108.46), (10, 108.60, 114.70),
        (9, 114.74, 117.14), (11, 122.95, 125.56), (10, 125.63, 131.91),
    ],
}
VIDEO_META = {"i2JWkDyg26A": {"duration": 173.24, "fps": 25.0}}
IOU_THRESHOLDS = (0.1, 0.3, 0.5)
# Openers that mark a spoken caution rather than an action to perform.
ADVISORY = ("never ", "do not ", "don't ", "if nuts are tight", "avoid ",
            "be careful", "make sure you do not")


def load_annotations(key: str, path: str | None, annotator: int | None):
    """Reference intervals per step, merged across annotators unless one is picked.

    CrossTask lists each annotator's segments consecutively, so the file splits in
    half: the first occurrence of each (step, occurrence) belongs to annotator 0.
    """
    rows = None
    if path:
        f = os.path.join(path, "annotations", f"{key}.csv")
        if os.path.exists(f):
            rows = [(int(a), float(b), float(c))
                    for a, b, c in (l.strip().split(",") for l in open(f) if l.strip())]
    if rows is None:
        rows = EMBEDDED.get(key)
    if rows is None:
        sys.exit(f"no annotations for {key}: pass --annotations <crosstask_release dir>")

    if annotator is not None:
        half = len(rows) // 2
        rows = rows[:half] if annotator == 0 else rows[half:]

    per: dict[int, list[tuple[float, float]]] = {}
    for s, a, b in rows:
        per.setdefault(s, []).append((a, b))
    # Merge to one interval per step: earliest start, latest end. This is the
    # design-doc-neutral choice and it is deliberately generous — see the
    # docstring's note on annotator disagreement.
    return {s: (min(a for a, _ in v), max(b for _, b in v)) for s, v in per.items()}


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    ov = min(a[1], b[1]) - max(a[0], b[0])
    if ov <= 0:
        return 0.0
    return ov / ((a[1] - a[0]) + (b[1] - b[0]) - ov)


def is_advisory(text: str) -> bool:
    t = text.strip().lower()
    return any(t.startswith(p) for p in ADVISORY)


def load_record(path: str) -> dict:
    """First record of an MGA output file.

    MGA emits ONE JSON DOCUMENT per run (the platform reports
    `file_extension: "json"`). Files committed here before that was noticed carry
    a `.jsonl` extension, so line-delimited bodies are still accepted.
    """
    raw = open(path, encoding="utf-8").read().strip()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        doc = [json.loads(l) for l in raw.splitlines() if l.strip()]
    return doc[0] if isinstance(doc, list) else doc


def score(path: str, ann: dict, steps: list[str], duration: float) -> dict:
    rec = load_record(path)
    preds = rec["results"]
    spans = [(p["timestamp_start"], p["timestamp_end"]) for p in preds]

    # Reference recall: for each reference step, the best-overlapping prediction.
    # An advisory ("Do not touch the hot brakes") can never satisfy an action step,
    # but it occupies a span, so a purely temporal match will happily credit it.
    # Over a contiguously tiled timeline that inflates recall badly: EVERY reference
    # interval necessarily overlaps something. Excluding advisories from candidacy
    # is the minimum needed for recall to mean "the action was found".
    matched: dict[int, tuple[float, int]] = {}
    for s, ref in sorted(ann.items()):
        best = (0.0, -1)
        for i, sp in enumerate(spans):
            if is_advisory(preds[i]["instruction"]):
                continue
            v = iou(ref, sp)
            if v > best[0]:
                best = (v, i)
        matched[s] = best

    used = {i for v, i in matched.values() if i >= 0 and v > 0}
    advisory = [i for i, p in enumerate(preds) if is_advisory(p["instruction"])]
    unmatched = [i for i in range(len(preds)) if i not in used and i not in advisory]

    ref_end = max(b for _, b in ann.values())
    covered = sum(1 for v, _ in matched.values() if v > 0)
    contiguous = all(abs(spans[i][1] - spans[i + 1][0]) < 1e-6
                     for i in range(len(spans) - 1))
    # An EMPTY manual is a real outcome, not a malformed file: below the model's
    # reasoning floor a run completes with `results: []` and no error anywhere.
    # Scoring it as 0/n is the honest answer; crashing here hid it.
    truncated = bool(preds) and preds[-1]["instruction"].rstrip()[-1] not in ".!?"

    return {
        "path": path, "preds": preds, "spans": spans, "matched": matched,
        "advisory": advisory, "unmatched": unmatched, "ann": ann, "steps": steps,
        "ref_end": ref_end, "covered": covered, "n_ref": len(ann),
        "reach": max((b for _, b in spans), default=0.0),
        "start": min((a for a, _ in spans), default=0.0),
        "contiguous": contiguous, "truncated": truncated, "duration": duration,
        "words": sum(len(p["instruction"].split()) for p in preds),
    }


def report(r: dict) -> None:
    print(f"\n{'='*78}\n{os.path.basename(r['path'])}\n{'='*78}")
    print(f"  {len(r['preds'])} predicted steps (step field 0-{len(r['preds'])-1}), "
          f"{r['words']} words")
    print(f"  spans {r['start']:.1f}-{r['reach']:.1f}s   "
          f"annotated procedure ends {r['ref_end']:.1f}s   "
          f"video {r['duration']:.1f}s")
    print(f"  timeline tiled contiguously: {r['contiguous']}   "
          f"generation truncated: {r['truncated']}")

    print(f"\n  reference-step recall ({r['covered']}/{r['n_ref']} covered at IoU>0)")
    print(f"  {'ref':>3} {'step':<16} {'interval':>13} {'best IoU':>9}  matched prediction")
    for s in sorted(r["matched"]):
        v, i = r["matched"][s]
        a, b = r["ann"][s]
        txt = r["preds"][i]["instruction"][:52] if i >= 0 and v > 0 else "— none —"
        print(f"  {s:>3} {r['steps'][s-1]:<16} {a:6.1f}-{b:<6.1f} {v:9.2f}  {txt}")

    print("\n  recall at IoU thresholds (standard temporal-localization view):")
    for t in IOU_THRESHOLDS:
        n = sum(1 for v, _ in r["matched"].values() if v >= t)
        print(f"    IoU >= {t:<4} {n:>2}/{r['n_ref']}  ({100*n/r['n_ref']:5.1f}%)")
    hits = [v for v, _ in r["matched"].values() if v > 0]
    if hits:
        print(f"    mean IoU over matched steps: {sum(hits)/len(hits):.2f}")
        print(f"    mean IoU over all reference steps: {sum(hits)/r['n_ref']:.2f}")

    # Deliberately counted, never scored — see the module docstring.
    print(f"\n  predictions not matched to a reference step: "
          f"{len(r['advisory'])} advisory + {len(r['unmatched'])} other")
    for i in r["advisory"]:
        p = r["preds"][i]
        print(f"    advisory  [{p['timestamp_start']:6.1f}-{p['timestamp_end']:6.1f}]  "
              f"{p['instruction'][:60]}")
    for i in r["unmatched"]:
        p = r["preds"][i]
        print(f"    other     [{p['timestamp_start']:6.1f}-{p['timestamp_end']:6.1f}]  "
              f"{p['instruction'][:60]}")
    print("  (CrossTask's step list is a per-task reference procedure, not an "
          "inventory of\n   the video, so these are NOT scored as false positives.)")

    # The floor no model can beat at this sampling density.
    n_frames = len(r["preds"])
    interval = r["duration"] / 64.0
    print(f"\n  sampling floor: at max_frames=64 over {r['duration']:.0f}s, frames land "
          f"every {interval:.1f}s,")
    print(f"  so boundaries cannot resolve finer than that regardless of the model.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("outputs", nargs="*", help="MGA output file(s), .json")
    ap.add_argument("--compare", nargs="+", metavar="FILE",
                    help="score several outputs and print a one-line-each summary")
    ap.add_argument("--task", default="40567")
    ap.add_argument("--video", default="i2JWkDyg26A")
    ap.add_argument("--annotations", default="data/raw/crosstask/crosstask_release",
                    help="a CrossTask release dir; falls back to embedded data")
    ap.add_argument("--annotator", type=int, choices=(0, 1), default=None,
                    help="score against one annotator instead of the merge")
    args = ap.parse_args()

    files = args.compare or args.outputs
    if not files:
        ap.error("give one or more output files, or --compare")
    files = [f for pat in files for f in (glob.glob(pat) or [pat])]

    key = f"{args.task}_{args.video}"
    ann = load_annotations(key, args.annotations if os.path.isdir(args.annotations) else None,
                           args.annotator)
    steps = TASK_STEPS.get(args.task)
    if not steps:
        sys.exit(f"no step taxonomy embedded for task {args.task}")
    duration = VIDEO_META.get(args.video, {}).get("duration", 0.0)
    if args.annotator is not None:
        print(f"scoring against annotator {args.annotator} only")

    # `reference/*.json` also matches committed blueprint documents and other
    # non-outputs, so skip anything without a `results` key rather than dying on
    # the first one. Named explicitly, so a typo is not silently ignored.
    scorable = []
    for f in sorted(files):
        try:
            rec = load_record(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"skipping {os.path.basename(f)}: unreadable ({e})")
            continue
        if "results" not in rec:
            print(f"skipping {os.path.basename(f)}: not an MGA output")
            continue
        scorable.append(f)
    if not scorable:
        sys.exit("no MGA output files to score")

    results = [score(f, ann, steps, duration) for f in scorable]

    if args.compare:
        print(f"\n{'output':56} {'steps':>5} {'coverage':>14} {'recall':>7} "
              f"{'IoU>=0.5':>8} {'trunc':>5}")
        for r in results:
            n50 = sum(1 for v, _ in r["matched"].values() if v >= 0.5)
            print(f"{os.path.basename(r['path'])[:56]:56} {len(r['preds']):>5} "
                  f"{r['start']:6.0f}-{r['reach']:<6.0f} "
                  f"{r['covered']:>3}/{r['n_ref']:<3} {n50:>8} "
                  f"{'yes' if r['truncated'] else 'no':>5}")
        print()
    for r in results:
        report(r)


if __name__ == "__main__":
    main()
