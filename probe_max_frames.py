#!/usr/bin/env python3
"""Probe the real `max_frames` ceiling of the `mga` blueprint.

The blueprint ships `max_frames: 16`, but the VideoReaderNode's own default is
`-1` (keep every frame) and the value is exposed in `values`, so 16 is a
deployment choice rather than a limit. This script establishes where the actual
wall is, in two phases:

  phase 1  create a bundle at each candidate max_frames and record the HTTP
           status. Free and instant — it catches values rejected by schema
           validation without burning a GPU run.
  phase 2  run the ones that were accepted against a real video, and compare
           outputs. This is what distinguishes "accepted then silently clamped"
           from "actually honoured", which phase 1 cannot see.

Two arithmetic ceilings are worth holding in mind while reading the results:

  preprocessor_max_pixels = 25,165,824 (24 Mi) at size=224 permits
      25165824 / (224*224) = 501 frames
  reader_batch_size = preprocessor_batch_size = 64, so max_frames <= 64 is a
      single batch; above that the preprocessor pushes multiple batches and the
      prompt node has to reassemble them. That is the untested path.

Usage:
  python3 probe_max_frames.py --create-only          # phase 1, no GPU
  python3 probe_max_frames.py --video data/clips/x.mp4
  python3 probe_max_frames.py --frames 16,64 --video ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from mga_api import (agents_base, blueprint_must_exist, download_results,
                     poll_agent, request, require_key, upload_file)

BLUEPRINT_KEY = "mga"
# 16 = shipped default. 64 = C2.6's documented frame capability and the single
# batch boundary. 256 = multi-batch but under the pixel cap. 501 = exactly the
# max_pixels cap at size=224. 512 = just over it. 1024 = well over. -1 = the
# node default, "keep every frame" (16,885 for our test video).
DEFAULT_LADDER = [16, 64, 256, 501, 512, 1024, -1]
TIMEOUT_S = 60 * 60


def create_bundle(max_frames: int, name: str) -> tuple[int, dict | str]:
    """Create a bundle overriding only max_frames. No artifacts map: the mga
    blueprint pins newton-fusion and whisper itself, so there is nothing to
    supply — unlike red/osm, which require a fitted classifier."""
    return request("POST", f"{agents_base()}/bundles", body={
        "blueprint": BLUEPRINT_KEY,
        "name": name,
        "values": {"max_frames": max_frames},
    }, allow_status=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", default="data/clips/tire_zlyrv-h97z8.mp4")
    ap.add_argument("--frames", help="comma-separated ladder override")
    ap.add_argument("--create-only", action="store_true",
                    help="phase 1 only: create bundles, never run them")
    ap.add_argument("--run", help="comma-separated subset to actually run")
    ap.add_argument("--outdir", default="discovery/probe")
    args = ap.parse_args()

    ladder = ([int(x) for x in args.frames.split(",")] if args.frames
              else DEFAULT_LADDER)

    require_key()
    bp = blueprint_must_exist(BLUEPRINT_KEY)
    v = bp["document"]["values"]
    size, maxpx = v["size"], v["preprocessor_max_pixels"]
    print(f"blueprint {bp['id']}  max_frames default={v['max_frames']}  "
          f"size={size}  max_pixels={maxpx:,}")
    print(f"  arithmetic cap at size={size}: {maxpx // (size * size)} frames\n")

    print("=== phase 1: bundle creation ===")
    bundles: dict[int, str] = {}
    for mf in ladder:
        status, payload = create_bundle(mf, f"MGA probe max_frames={mf}")
        if status in (200, 201) and isinstance(payload, dict):
            bundles[mf] = payload["id"]
            print(f"  max_frames={mf:<6} HTTP {status}  {payload['id']}  "
                  f"status={payload.get('status')}")
        else:
            print(f"  max_frames={mf:<6} HTTP {status}  REJECTED: "
                  f"{str(payload)[:300]}")

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "bundles.json"), "w") as f:
        json.dump(bundles, f, indent=2)

    if args.create_only:
        print("\nphase 1 only (--create-only). Accepted values above are not "
              "proof they are honoured — phase 2 is what shows that.")
        return

    to_run = ([int(x) for x in args.run.split(",")] if args.run
              else sorted(bundles))
    to_run = [mf for mf in to_run if mf in bundles]
    if not to_run:
        sys.exit("nothing to run")

    print(f"\n=== phase 2: uploading {args.video} ===")
    uploaded = upload_file(args.video)
    file_id = uploaded.get("file_id") or uploaded.get("id")
    print(f"  file_id={file_id}")
    with open(os.path.join(args.outdir, "upload.json"), "w") as f:
        json.dump(uploaded, f, indent=2)

    print(f"\n=== phase 2: launching {len(to_run)} run(s): {to_run} ===")
    runs: dict[int, str] = {}
    for mf in to_run:
        status, payload = request(
            "POST", f"{agents_base()}/bundles/{bundles[mf]}/run",
            body={"connectors": {"source": [{"type": "file", "id": file_id,
                                             "format": "mp4"}]}},
            allow_status=True)
        # The run endpoint answers 202 Accepted, not 201. Treating only 2xx-
        # created as success silently orphans a launched run: the agent is on the
        # platform burning GPU with no client polling it.
        if status in (200, 201, 202) and isinstance(payload, dict):
            runs[mf] = payload["id"]
            print(f"  max_frames={mf:<6} agent={payload['id']}  "
                  f"status={payload.get('status')}")
        else:
            print(f"  max_frames={mf:<6} RUN REJECTED HTTP {status}: "
                  f"{str(payload)[:300]}")

    with open(os.path.join(args.outdir, "runs.json"), "w") as f:
        json.dump(runs, f, indent=2)

    print("\n=== phase 2: polling ===")
    for mf, agent_id in runs.items():
        print(f"\n--- max_frames={mf}  {agent_id} ---")
        status = poll_agent(agent_id, TIMEOUT_S)
        print(f"  finished: {status}")
        out = os.path.join(args.outdir, f"mga-mf{mf}.jsonl")
        saved = download_results(agent_id, out)
        if saved:
            body = open(saved, "rb").read()
            print(f"  --- first 1500 bytes ---\n{body[:1500].decode(errors='replace')}")


if __name__ == "__main__":
    main()
