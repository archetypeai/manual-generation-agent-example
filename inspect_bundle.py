#!/usr/bin/env python3
"""Stage 0 — discover the MGA contract from a bundle that already works.

Nothing in this repo should guess at the platform's schema. This script reads a
known-good bundle out of dev and writes every response to discovery/, so the
blueprint key, the bundle `values`, the connector shape for video, and the output
format are all *observed* rather than assumed.

The reference bundle is the dev console example:
  https://console.dev.u1.archetypeai.app/dashboard/agents/bnd_3ve9qfkve99e980rd858tm9dfb

What it collects:
  1. the bundle itself            -> blueprint key, values, connectors
  2. that blueprint's definition  -> every tunable value + its default, models
  3. every agent instance it ran  -> connector payloads that were accepted
  4. one run's events             -> the audit log, incl. JOS dispatch + timings
  5. one run's results + output   -> the real output format
  6. the org's blueprint list     -> what else is registered

Usage:
  python3 inspect_bundle.py                      # the console reference bundle
  python3 inspect_bundle.py --bundle bnd_...     # some other bundle
  python3 inspect_bundle.py --blueprints-only    # just list blueprints
"""
from __future__ import annotations

import argparse
import json
import os

from mga_api import agents_base, fetch_ref, request, require_key

# The dev console example linked from the design doc discussion.
REFERENCE_BUNDLE = "bnd_3ve9qfkve99e980rd858tm9dfb"
OUTDIR = "discovery"


def dump(name: str, payload) -> None:
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    print(f"  wrote {path}")


def show(label: str, payload, depth: int = 0) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, sort_keys=True)[:6000])


def get(path: str, label: str, name: str):
    """GET a path, dump it, and report the status rather than dying on 404."""
    status, payload = request("GET", path, allow_status=True)
    if status != 200:
        print(f"\n=== {label} ===\n  HTTP {status}: {str(payload)[:500]}")
        return None
    show(label, payload)
    dump(name, payload)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", default=REFERENCE_BUNDLE)
    ap.add_argument("--blueprints-only", action="store_true")
    ap.add_argument("--max-runs", type=int, default=5,
                    help="how many of the bundle's runs to detail")
    args = ap.parse_args()

    require_key()
    agents = agents_base()

    # 6 first: cheap, and it tells us the blueprint vocabulary up front.
    listing = get(f"{agents}/blueprints?limit=100", "blueprints in this org",
                  "blueprints")
    if listing:
        rows = listing.get("data", listing if isinstance(listing, list) else [])
        print("\n  blueprint keys:")
        for bp in rows:
            print(f"    {str(bp.get('blueprint_key') or bp.get('id')):<28} "
                  f"canonical={bp.get('is_canonical')}  {bp.get('name', '')}")
    if args.blueprints_only:
        return

    bundle = get(f"{agents}/bundle/{args.bundle}", f"bundle {args.bundle}",
                 "bundle")
    if not bundle:
        print("\nCould not read the reference bundle. If this is a 403/404 the "
              "bundle may belong to another org than your API key's.")
        return

    blueprint_key = bundle.get("blueprint") or bundle.get("blueprint_key")
    if blueprint_key:
        get(f"{agents}/blueprints/{blueprint_key}",
            f"blueprint '{blueprint_key}'", "blueprint")

    # 3: the runs. Their connector payloads are the authoritative example of how
    # a video is handed to the agent.
    instances = get(f"{agents}/instances?limit=100", "agent instances (all)",
                    "instances")
    runs = []
    if instances:
        rows = instances.get("data", instances if isinstance(instances, list) else [])
        runs = [a for a in rows if a.get("bundle_id") == args.bundle]
        print(f"\n  {len(runs)} run(s) belong to {args.bundle}:")
        for a in runs:
            print(f"    {a.get('id')}  {a.get('status'):<10} "
                  f"{json.dumps(a.get('connectors', {}))[:200]}")

    for run in runs[: args.max_runs]:
        rid = run["id"]
        get(f"{agents}/instances/{rid}", f"instance {rid}", f"instance_{rid}")
        get(f"{agents}/instances/{rid}/events", f"events {rid}", f"events_{rid}")
        results = get(f"{agents}/instances/{rid}/results", f"results {rid}",
                      f"results_{rid}")
        if not results:
            continue
        items = results.get("data", results if isinstance(results, list) else [])
        for item in items:
            inner = item.get("data") or {}
            ref = inner.get("ref") or item.get("ref")
            fname = inner.get("filename") or f"{rid}-output"
            if not ref:
                continue
            try:
                payload = fetch_ref(ref)
            except Exception as e:  # a presigned ref may already have expired
                print(f"  could not fetch {fname}: {e}")
                continue
            os.makedirs(OUTDIR, exist_ok=True)
            out = os.path.join(OUTDIR, os.path.basename(fname))
            open(out, "wb").write(payload)
            print(f"  wrote {out} ({len(payload)} bytes)")
            print("\n--- first 4000 bytes of the real output ---")
            print(payload[:4000].decode(errors="replace"))

    print(f"\nDone. Raw responses are in {OUTDIR}/ (gitignored).")


if __name__ == "__main__":
    main()
