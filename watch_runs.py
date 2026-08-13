#!/usr/bin/env python3
"""Watch MGA runs via /logs, which is the only stream that tells the truth.

Three things learned the hard way, all encoded here:

  * `GET /agents/instances/{id}/logs` carries the real JOS/pod log — model
    download and load timings, per-input progress, and the actual error. The
    `/events` endpoint only ever returns "run started" and "dispatched to JOS",
    which is what RED's client polled, and it is not enough to diagnose anything.
  * the instance `status` field is unreliable in BOTH directions. It stayed
    "running" for 20+ minutes after a pod terminated with exit=1. So terminality
    is judged from the log stream (`pod.terminated`, `job.completed`, an ERROR
    row), not from `status`.
  * dev serializes these jobs. Submitting four at once produced 800+ second
    queue waits, not parallelism, so `job.admitted.wait_seconds` is worth
    printing to keep queueing distinct from execution.

Usage:
  python3 -u watch_runs.py label=agt_... [label=agt_...] [--download]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from mga_api import agents_base, download_results, request, require_key

TERMINAL_EVENTS = ("pod.terminated", "job.completed", "job.failed",
                   "job.canceled")
STATUS_TERMINAL = ("completed", "failed", "canceled")


def logs(agent_id: str) -> list[dict]:
    payload = request("GET", f"{agents_base()}/instances/{agent_id}/logs?limit=500")
    return sorted(payload.get("data", []), key=lambda x: str(x.get("created_at")))


def fmt(row: dict) -> str:
    ts = str(row.get("created_at", ""))[11:19]
    lvl = str(row.get("level", ""))
    et = str(row.get("event_type", ""))
    msg = str(row.get("message", ""))
    return f"  {ts} {lvl:7} {et:32} {msg}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("specs", nargs="+", help="label=agent_id")
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=3 * 60 * 60)
    ap.add_argument("--download", action="store_true",
                    help="fetch /results for runs that reach a clean end")
    ap.add_argument("--outdir", default="discovery/probe")
    args = ap.parse_args()

    require_key()
    jobs = {}
    for spec in args.specs:
        label, _, agent = spec.partition("=")
        if not agent:
            sys.exit(f"bad spec {spec!r}; want label=agent_id")
        jobs[label] = agent

    os.makedirs(args.outdir, exist_ok=True)
    seen: dict[str, set] = {label: set() for label in jobs}
    done: dict[str, str] = {}
    deadline = time.time() + args.timeout

    while len(done) < len(jobs) and time.time() < deadline:
        for label, agent_id in jobs.items():
            if label in done:
                continue
            rows = logs(agent_id)
            for row in rows:
                key = f"{row.get('created_at')}|{row.get('event_type')}|{row.get('message')}"
                if key in seen[label]:
                    continue
                seen[label].add(key)
                print(f"[{label}]{fmt(row)}", flush=True)
                pl = row.get("payload") or {}
                if row.get("event_type") == "job.admitted":
                    print(f"[{label}]    queue wait: "
                          f"{pl.get('wait_seconds')}s", flush=True)
            last = rows[-1] if rows else {}
            et = str(last.get("event_type", ""))
            info = request("GET", f"{agents_base()}/instances/{agent_id}")
            status = info.get("status")
            if et in TERMINAL_EVENTS or status in STATUS_TERMINAL:
                # Prefer the log's verdict: status lags, and lies both ways.
                failed = any(str(r.get("level")) == "ERROR" for r in rows)
                verdict = "FAILED" if failed else (status or "ended")
                done[label] = verdict
                print(f"[{label}]  ==> {verdict} (status field said "
                      f"{status!r})", flush=True)
        if len(done) < len(jobs):
            waiting = [l for l in jobs if l not in done]
            print(f"  {time.strftime('%H:%M:%S')} waiting on {waiting}",
                  flush=True)
            time.sleep(args.interval)

    print("\n=== verdicts ===", flush=True)
    for label, verdict in done.items():
        print(f"  {label:<12} {verdict:<10} {jobs[label]}", flush=True)

    if args.download:
        for label, verdict in done.items():
            if verdict == "FAILED":
                print(f"  {label}: failed, skipping /results", flush=True)
                continue
            out = os.path.join(args.outdir, f"mga-{label}.json")
            saved = download_results(jobs[label], out)
            if saved:
                body = open(saved, "rb").read()
                print(f"\n----- {label} output ({len(body)} bytes) -----",
                      flush=True)
                print(body[:3000].decode(errors="replace"), flush=True)


if __name__ == "__main__":
    main()
