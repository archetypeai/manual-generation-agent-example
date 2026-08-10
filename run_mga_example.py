#!/usr/bin/env python3
"""Run the Manual Generation Agent on a CrossTask video, end to end.

Single file, stdlib only — no pip install, no virtualenv. Copy this file plus a
.env next to it and run it.

    ATAI_API_KEY=<dev key>
    ATAI_API_ENDPOINT=https://api.dev.u1.archetypeai.app

    python3 run_mga_example.py --download     # fetch the video, then run
    python3 run_mga_example.py                # video already on disk
    python3 run_mga_example.py --max-frames 16
    python3 run_mga_example.py --dry-run      # print the plan, call nothing

Expect ~15 minutes: ~7.5 min of model download/load on every run (nothing is
cached between runs), then ~2.5x realtime processing. Dev appears to run these
one at a time, so a second concurrent run just queues — submitting four at once
produced 800-second queue waits rather than any parallelism.

FOUR THINGS THAT WILL BITE YOU (all verified on dev, 2026-08-10)

  1. The bundle endpoint is PLURAL. `POST /agents/bundle` returns 404; it is
     `POST /agents/bundles`. The clients committed in the RED and OSM example
     repos still use the singular form and fail against current dev.

  2. Starting a run returns HTTP 202, not 201. Treat only 201 as success and you
     will report a failure while the agent runs unattended on the GPU with
     nothing collecting its output.

  3. Poll /logs, NOT /events. `GET /agents/instances/{id}/logs` is the stream the
     console shows: model load timings, per-input progress, and the real error.
     `/events` returns only "run started" and "dispatched to JOS" — not enough to
     diagnose anything.

  4. The instance `status` field lies in BOTH directions. We saw `running`
     persist 20+ minutes after a pod terminated with exit=1, and `running` after
     a job had completed. Decide terminality from the log stream.

Also worth knowing: unlike the `red`/`osm` blueprints, MGA needs NO `artifacts`
map — the blueprint pins newton-fusion and whisper itself. And `max_frames`
accepts any value at bundle creation (16, 512, 1024, -1 all return `ready`), so
the API will not stop you setting something the pipeline cannot honour.
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

# CrossTask task 40567 "Change a Tire", video i2JWkDyg26A. 173 s, 640x360,
# 25 fps, all 11 reference steps annotated. Chosen deliberately: MGA fails on
# videos over ~5 minutes, and this one is 58% of that ceiling. Source resolution
# barely matters because the pipeline resizes every frame to 224x224.
VIDEO_URL = "https://www.youtube.com/watch?v=i2JWkDyg26A"
VIDEO_PATH = "data/clips/tire_i2JWkDyg26A.mp4"  # gitignored: videos are not ours to redistribute
# The ACTIVE canonical blueprint (is_canonical: true, is_active: true), newest of
# four mga versions. Default because it is what ships.
BLUEPRINT_ACTIVE = "blp_0zmce3qnjp8659dp1pszdqbtx4"
# Superseded (is_active: false) but still runnable. Wires max_new_tokens, prompt,
# temperature, top_k, top_p, repetition_penalty, seed, stop into the fusion node.
# DIAGNOSIS ONLY — nothing built on an inactive blueprint can ship.
BLUEPRINT_WITH_PARAMS = "blp_76kyqm4vjp9pt8tvfz8tks7x6t"
# What this script produced on dev at max_frames=64. Generation is deterministic,
# so a matching digest means you reproduced the run exactly.
KNOWN_MD5 = "080e551a88f0bdcd857e48557366ae60"

TERMINAL_EVENTS = ("pod.terminated", "job.completed", "job.failed", "job.canceled")


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def env() -> tuple[str, str]:
    load_dotenv()
    key = os.environ.get("ATAI_API_KEY")
    endpoint = (os.environ.get("ATAI_API_ENDPOINT") or "").rstrip("/")
    if not key:
        sys.exit("ATAI_API_KEY is not set (put it in .env)")
    if not endpoint:
        sys.exit("ATAI_API_ENDPOINT is not set (put it in .env)")
    return key, endpoint


def api(method: str, url: str, body=None, raw: bool = False):
    key, _ = env()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
            # 202 is a success here — see gotcha 2 in the module docstring.
            if resp.status not in (200, 201, 202):
                sys.exit(f"{method} {url} unexpected status {resp.status}")
            return payload if raw else json.loads(payload or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} failed ({e.code}): {e.read().decode(errors='replace')}")


def download_video(path: str) -> None:
    if os.path.exists(path):
        print(f"video already present: {path}")
        return
    if not any(os.access(os.path.join(p, "yt-dlp"), os.X_OK)
               for p in os.environ.get("PATH", "").split(os.pathsep)):
        sys.exit("yt-dlp not found. Install it, or download the video manually:\n"
                 f"  {VIDEO_URL}\n  and save it as {path}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    print(f"downloading {VIDEO_URL} -> {path} ...")
    subprocess.run([
        "yt-dlp", "--no-warnings",
        "-f", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4", "-o", path, VIDEO_URL,
    ], check=True)


def upload(path: str) -> str:
    """POST the video to /v0.5/files as multipart/form-data.

    The declared Content-Type matters: the files API enforces a MIME allowlist on
    what you declare, not on the bytes, so an .mp4 sent as text/csv is rejected.
    This reads the file into memory, which is fine for the 5 MB example clip;
    stream it in chunks if you swap in something much larger.
    """
    key, endpoint = env()
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        b"Content-Type: video/mp4\r\n\r\n",
        open(path, "rb").read(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(f"{endpoint}/v0.5/files", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"upload failed ({e.code}): {e.read().decode(errors='replace')}")
    print(f"  file_id={out['file_id']}")
    return out["file_id"]


def watch(agent_id: str, timeout_s: int = 60 * 60) -> str:
    """Stream /logs until a terminal event. Ignores the `status` field entirely."""
    _, endpoint = env()
    seen: set[str] = set()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rows = sorted(
            api("GET", f"{endpoint}/agents/instances/{agent_id}/logs?limit=500").get("data", []),
            key=lambda r: str(r.get("created_at")),
        )
        for r in rows:
            marker = f"{r.get('created_at')}|{r.get('event_type')}|{r.get('message')}"
            if marker in seen:
                continue
            seen.add(marker)
            print(f"  {str(r.get('created_at'))[11:19]} {str(r.get('level')):7} "
                  f"{str(r.get('event_type')):28} {r.get('message')}")
        if rows and str(rows[-1].get("event_type")) in TERMINAL_EVENTS:
            failed = any(str(r.get("level")) == "ERROR" for r in rows)
            return "failed" if failed else "completed"
        time.sleep(20)
    return "timeout"


def fetch_results(agent_id: str, out_path: str) -> str | None:
    _, endpoint = env()
    results = api("GET", f"{endpoint}/agents/instances/{agent_id}/results")
    items = results.get("data") or []
    if not items:
        print("  no results (a failed run leaves none)")
        return None
    inner = items[0].get("data") or {}
    ref = inner.get("ref") or items[0].get("ref")
    if not ref:
        print("  results carried no download ref")
        return None
    # A run output's ref is a RELATIVE platform path that resolves under /v0.5 and
    # needs the bearer token; an absolute ref is presigned and must not get the
    # header. Run outputs do not expire, so this can be re-fetched later.
    payload = api("GET", f"{endpoint}/v0.5{ref}", raw=True) if not ref.startswith("http") \
        else urllib.request.urlopen(ref, timeout=300).read()
    open(out_path, "wb").write(payload)
    print(f"  saved {out_path} ({len(payload)} bytes, "
          f"md5 {hashlib.md5(payload).hexdigest()})")
    return out_path


def show(path: str) -> None:
    raw = open(path, "rb").read()
    digest = hashlib.md5(raw).hexdigest()
    for line in raw.decode(errors="replace").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        print(f"\nmanual for {rec.get('id')} — {len(rec.get('results', []))} steps")
        for r in rec.get("results", []):
            print(f"  [{r['timestamp_start']:>6.1f} - {r['timestamp_end']:>6.1f}s] "
                  f"(frames {r['frame_start']:.0f}-{r['frame_end']:.0f})  {r['instruction']}")
    print(f"\nmd5 {digest}")
    if digest == KNOWN_MD5:
        print("  matches the reference run exactly (generation is deterministic)")
    else:
        print(f"  differs from the reference run ({KNOWN_MD5}) — different video,"
              f" max_frames, or a changed blueprint")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", default=VIDEO_PATH)
    ap.add_argument("--download", action="store_true",
                    help="fetch the video into data/clips/ first, then run")
    ap.add_argument("--download-only", action="store_true",
                    help="fetch the video into data/clips/ and stop — no API calls")
    ap.add_argument("--max-frames", type=int, default=64,
                    help="frames uniformly sampled across the whole video. 64 is the "
                         "reader/preprocessor batch size, so they land in one batch; "
                         "the blueprint default is 16. (default: 64)")
    ap.add_argument("--blueprint", default=BLUEPRINT_ACTIVE,
                    help=f"blueprint id or key (default {BLUEPRINT_ACTIVE}, the "
                         f"active canonical mga)")
    ap.add_argument("--max-new-tokens", type=int, default=None,
                    help="raise the output budget. Inert on the active blueprint, "
                         "which hardcodes 256 — see the README.")
    ap.add_argument("--prompt", default=None,
                    help="override the instruction. Only settable on a blueprint "
                         "that declares values.prompt.")
    ap.add_argument("--name", default="MGA CrossTask tire change")
    ap.add_argument("--output", default="data/mga-output.jsonl")
    ap.add_argument("--show", metavar="JSONL", help="pretty-print an output file, no API calls")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.show:
        show(args.show)
        return

    _, endpoint = env()
    values = {"max_frames": args.max_frames}
    if args.max_new_tokens is not None:
        values["max_new_tokens"] = args.max_new_tokens
    if args.prompt is not None:
        values["prompt"] = args.prompt

    if args.dry_run:
        print(f"POST {endpoint}/agents/bundles")
        print(json.dumps({"blueprint": args.blueprint, "name": args.name,
                          "values": values}, indent=2))
        print(f"POST {endpoint}/agents/bundles/<id>/run")
        print(json.dumps({"connectors": {"source": [
            {"type": "file", "id": os.path.basename(args.video), "format": "mp4"}]}}, indent=2))
        return

    if args.download or args.download_only:
        download_video(args.video)
    if args.download_only:
        print(f"video ready at {args.video} "
              f"({os.path.getsize(args.video) / 1e6:.1f} MB)")
        return
    if not os.path.exists(args.video):
        sys.exit(f"no video at {args.video} — pass --download to fetch it")

    print(f"uploading {args.video} ...")
    file_id = upload(args.video)

    # No `artifacts` map: the mga blueprint pins newton-fusion:1.0 and
    # whisper:large-v3 itself. This is the main shape difference from red/osm,
    # which require a fitted classifier artifact.
    # Warn about values this blueprint will silently ignore. A value is only
    # honoured if it is declared in `values` AND referenced as ${values.<key>} by
    # some node or connector; the API accepts anything and drops the rest.
    bp = api("GET", f"{endpoint}/agents/blueprints/{args.blueprint}")
    doc = bp["document"]
    wired = json.dumps({"nodes": doc.get("nodes"), "connectors": doc.get("connectors")})
    inert = [k for k in values
             if k not in doc.get("values", {})
             or ("${values." + k + "}") not in wired]
    print(f"blueprint {bp['id']} (key={bp['blueprint_key']}, "
          f"active={bp['is_active']})")
    if not bp["is_active"]:
        print("  NOTE: this blueprint is superseded — results cannot ship.")
    if inert:
        print(f"  WARNING: {inert} is not wired on this blueprint and will be "
              f"silently ignored.")

    print(f"creating bundle (values={values}) ...")
    bundle = api("POST", f"{endpoint}/agents/bundles",
                 body={"blueprint": args.blueprint, "name": args.name,
                       "values": values})
    print(f"  bundle_id={bundle['id']}  status={bundle.get('status')}")

    agent = api("POST", f"{endpoint}/agents/bundles/{bundle['id']}/run",
                body={"connectors": {"source": [
                    {"type": "file", "id": file_id, "format": "mp4"}]}})
    agent_id = agent["id"]
    print(f"started run\n  agent_id={agent_id}\n  watching /logs "
          f"(~7.5 min of model loading first) ...")

    verdict = watch(agent_id)
    print(f"\nrun {verdict}")
    saved = fetch_results(agent_id, args.output)
    if saved:
        show(saved)
    if verdict == "failed":
        sys.exit(f"run failed — full log: "
                 f"{endpoint}/agents/instances/{agent_id}/logs")


if __name__ == "__main__":
    main()
