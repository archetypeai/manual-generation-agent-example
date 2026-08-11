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
import glob
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
# Target the blueprint KEY, not an id.
#
# The platform accepts either, and a key always resolves to whatever is currently
# canonical and active. That is not a stylistic preference — it is the difference
# between working and not. `mga` was republished twice in two hours on 2026-08-11,
# and the id this file used to pin STOPPED RESOLVING mid-session:
#
#   resolving blueprint: invalid config for 1 node(s):
#     - `fusion`: config validation failed: Additional properties are not allowed
#       ('repetition_penalty', 'seed', 'stop', 'temperature', 'top_k', 'top_p'
#        were unexpected)
#
# Those parameters are wired into that blueprint's own fusion node, so nothing a
# caller does can avoid them. Reading it, creating a bundle against it and
# submitting a run all still succeed; the pod dies one second after it starts.
BLUEPRINT_DEFAULT = "mga"

# Kept for provenance, not for use.
#
# blp_0zmce3... was the active canonical when this example was written. It
# hardcoded max_new_tokens: 256, which truncated the manual at 6 steps covering
# half the video — the finding this example exists to document.
BLUEPRINT_HISTORIC_ACTIVE = "blp_0zmce3qnjp8659dp1pszdqbtx4"
# blp_76kyqm... was superseded but runnable, and exposed max_new_tokens and prompt.
# This file defaulted to it for exactly that reason. It is now UNRUNNABLE (above).
BLUEPRINT_DEAD_WITH_PARAMS = "blp_76kyqm4vjp9pt8tvfz8tks7x6t"

# WHAT CHANGED, and what it means for this example:
#   max_new_tokens is now settable on the canonical blueprint and defaults to 2048
#   rather than 256, so the truncation this example documents is FIXED upstream.
#   `prompt` is NOT settable there — the source connector hardcodes
#   "Generate a concise, ordered list of distinct steps with 10 steps or less."
#   so --prompt is accepted, reported as inert by the preflight below, and ignored.
#   Stage C of the ladder in the README cannot be reproduced until a canonical
#   blueprint exposes ${values.prompt} again.

# The instruction the default configuration uses. Deliberately says what to cover
# and NOT how to format it: ManualGenerationResultsParserNode owns the output
# template, and a prompt that specifies its own format (markdown headings, for
# instance) makes the parser return zero steps. This one parses cleanly.
DEFAULT_PROMPT = (
    "Generate a concise, ordered list of every distinct step performed in this "
    "video, covering the procedure from the first action to the last. Include "
    "brief steps and steps that are repeated. Use up to 20 steps. Keep each step "
    "to at most 15 words. Use both what is shown and what is said. Do not invent "
    "steps that are not shown or stated in the video."
)
# What the defaults below produce on dev. Generation is deterministic
# (temperature 0.0), so a matching digest means you reproduced the run exactly.
KNOWN_MD5 = "4067a03bc740135bdbee1f0b62cbc511"
# The active blueprint's truncated output, for comparison.
TRUNCATED_MD5 = "080e551a88f0bdcd857e48557366ae60"

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


def _on_path(name: str) -> bool:
    return any(os.access(os.path.join(p, name), os.X_OK)
               for p in os.environ.get("PATH", "").split(os.pathsep))


def download_video(path: str) -> None:
    """Fetch the sample clip with yt-dlp.

    Two things here are not obvious and both have bitten someone:

    FFMPEG IS REQUIRED, not optional. The chosen format is separate video +
    audio streams, and yt-dlp needs ffmpeg to mux them. Without it, yt-dlp
    downloads both fragments, SKIPS the merge, and still EXITS 0 — leaving
    tire_*.f396.mp4 and tire_*.f140.m4a but no tire_*.mp4. The next step then
    fails with a bare FileNotFoundError several lines later.

    AND `--no-warnings` HID THE REASON. yt-dlp does say "You have requested
    merging of multiple formats but ffmpeg is not installed", and that flag
    suppressed it. So it is gone; `--no-update` keeps the version nag quiet
    without silencing anything that matters.
    """
    if os.path.exists(path):
        print(f"video already present: {path}")
        return
    if not _on_path("yt-dlp"):
        sys.exit("yt-dlp not found. Install it, or download the video manually:\n"
                 f"  {VIDEO_URL}\n  and save it as {path}")
    if not _on_path("ffmpeg"):
        sys.exit("ffmpeg not found, and yt-dlp needs it to combine the video and "
                 "audio streams.\n"
                 "  brew install ffmpeg   (or)   apt install ffmpeg\n\n"
                 "Without it yt-dlp exits 0 having downloaded two fragments and "
                 "merged nothing, which shows up later as a missing file.")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    print(f"downloading {VIDEO_URL} -> {path} ...")
    subprocess.run([
        "yt-dlp", "--no-update",
        "-f", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4", "-o", path, VIDEO_URL,
    ], check=True)

    # yt-dlp exiting 0 does not mean the file is there. Say so plainly rather
    # than letting the next step fail on a missing path.
    if not os.path.exists(path):
        stem = os.path.splitext(path)[0]
        leftovers = sorted(
            os.path.basename(f) for f in glob.glob(f"{stem}.f*")
        )
        sys.exit(
            f"yt-dlp reported success but {path} does not exist.\n"
            + (f"  Unmerged fragments present: {', '.join(leftovers)}\n"
               "  That means the merge step did not run — check that ffmpeg works.\n"
               if leftovers else "")
            + f"  Delete {os.path.dirname(path) or '.'}/ and try again, or download "
              f"the clip by hand:\n    {VIDEO_URL}"
        )


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
    elif digest == TRUNCATED_MD5:
        print("  this is the TRUNCATED output of the active blueprint (6 steps,\n"
              "  16-85s of 173s). Its hardcoded max_new_tokens: 256 cannot be\n"
              "  raised from a bundle — see the README's 'token cap' section.")
    else:
        print(f"  differs from the reference run ({KNOWN_MD5}) — different video,"
              f" max_frames, prompt, or a changed blueprint")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", default=VIDEO_PATH)
    ap.add_argument("--download", action="store_true",
                    help="fetch the video into data/clips/ first, then run")
    ap.add_argument("--download-only", action="store_true",
                    help="fetch the video into data/clips/ and stop — no API calls")
    ap.add_argument("--max-frames", type=int, default=65,
                    help="frames uniformly sampled across the whole video. The "
                         "blueprint default is 16. Note 64 is the reader and "
                         "preprocessor batch size, so 65 deliberately spills into a "
                         "second batch. (default: 65)")
    ap.add_argument("--blueprint", default=BLUEPRINT_DEFAULT,
                    help=f"blueprint id or KEY (default: {BLUEPRINT_DEFAULT!r}). A key "
                         f"tracks whatever is canonical and active. A pinned id can "
                         f"stop resolving when the blueprint is republished — which is "
                         f"exactly what happened to the id this used to default to.")
    ap.add_argument("--max-new-tokens", type=int, default=4096,
                    help="output budget (default 4096). The canonical blueprint's own "
                         "default is 2048, which is enough for this 2:53 clip but not "
                         "for a longer one; 4096 matches what the companion app sends "
                         "so the two do not disagree. Raise it if the last step comes "
                         "back cut mid-sentence.")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="the instruction. NOTE: the canonical blueprint currently "
                         "HARDCODES its prompt, so this is reported inert by the "
                         "preflight and then ignored. Do not specify an output format "
                         "in it either — the results parser owns the template, and a "
                         "format-overriding prompt makes it return zero steps.")
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

    # The video is a single fixed 5.7 MB asset whose URL is baked in, so fetch it
    # automatically when missing rather than making the caller pass a flag.
    if not os.path.exists(args.video):
        download_video(args.video)
    else:
        print(f"using {args.video} "
              f"({os.path.getsize(args.video) / 1e6:.1f} MB)")
    if args.download_only:
        return

    # Preflight BEFORE uploading: a value the blueprint will silently ignore should
    # be reported before spending bandwidth and 15 minutes of GPU, not after.
    # A value is only honoured if it is declared in `values` AND referenced as
    # ${values.<key>} by some node or connector; the API accepts anything else and
    # drops it.
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

    print(f"uploading {args.video} ...")
    file_id = upload(args.video)

    # No `artifacts` map: the mga blueprint pins newton-fusion:1.0 and
    # whisper:large-v3 itself — the main shape difference from red/osm, which
    # require a fitted classifier artifact.
    shown = dict(values)
    if "prompt" in shown and len(shown["prompt"]) > 60:
        shown["prompt"] = shown["prompt"][:57] + "..."
    print(f"creating bundle (values={json.dumps(shown)}) ...")
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
