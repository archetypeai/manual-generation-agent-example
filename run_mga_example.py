#!/usr/bin/env python3
"""Run the Manual Generation Agent on a CrossTask video, end to end.

Stdlib only apart from mga_api.py and mga_project.py next to it: no pip install,
no virtualenv. Put a .env next to them and run it.

    ATAI_API_KEY=<API key>
    ATAI_API_ENDPOINT=https://platform.u1.archetypeai.app/api

    python3 run_mga_example.py                # fetch the video if missing, then run
    python3 run_mga_example.py --download-only
    python3 run_mga_example.py --dry-run      # print the calls, make none
    python3 run_mga_example.py --show data/mga-output.json

A run is: the project's copy of `mga` with this run's max_frames, max_new_tokens
and prompt set on it, its package and batch deployment (all made once per setting,
see mga_project.py), and one batch session over the video. About 12 minutes on
prod, most of it loading the model. state/lifecycle.json keeps the ids;
`python3 cleanup.py --yes` deletes the sessions, deployments and packages.

Three things worth knowing:

  1. Set the values on the copy. A package, a deployment and a batch session take
     none, and `mga`'s own defaults (max_new_tokens 8192, a prompt that caps the
     manual at 10 steps) give a shorter manual, or none.
  2. A session that ends `completed` can still hold an empty manual, so the script
     counts the steps and exits non-zero when there are none.
  3. `file_id` is the filename, shared across your organization: uploading the same
     name again replaces the file under any run still reading it. upload_name()
     makes every upload's name unique.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import glob
import os
import subprocess
import sys
import uuid

import mga_api as api
import mga_project as project

# CrossTask task 40567 "Change a Tire", video i2JWkDyg26A: 173 s, 640x360, 25 fps,
# all 11 reference steps annotated. MGA fails on videos over ~5 minutes.
VIDEO_URL = "https://www.youtube.com/watch?v=i2JWkDyg26A"
VIDEO_PATH = "data/clips/tire_i2JWkDyg26A.mp4"  # gitignored: videos are not ours to redistribute
# Copy from the blueprint KEY, which resolves to the current version of `mga`.
BLUEPRINT_DEFAULT = "mga"

# Says what to COVER and never how to format: the results parser owns the output
# template, and a prompt that sets its own format (markdown headings, say) makes it
# return zero steps. The distinctness rule stops the model repeating one step, and
# "rely on the picture alone where the video is silent" covers clips with no audio.
DEFAULT_PROMPT = (
    "Generate an ordered list of every distinct step performed in this video, in "
    "order. Include brief steps and steps that are repeated. Write each step as a "
    "direct, actionable instruction, as if telling the reader what to do. Do not "
    "narrate or describe what is happening in the video. Capture the details "
    "needed to repeat the action exactly: control and part names, printed and "
    "on-screen text quoted word for word, part numbers, model and serial numbers, "
    "tool types and sizes, quantities, and which way things move. For every "
    "component being handled, state where it is using grid terms such as top-left, "
    "top-right, bottom-left, bottom-right, center-left, or upper-center. Where "
    "several similar actions are performed, such as four screws on one panel, "
    "treat each as its own step and say what distinguishes it. Where the video "
    "shows how to tell the step worked, say that. Base every step on what is "
    "visible; use the spoken narration where there is any, and rely on the picture "
    "alone where the video is silent. Each step must describe a different action "
    "from the one before it \u2014 where two moments look alike, state what changed "
    "between them. Do not invent steps, parts, or details that are not shown or "
    "stated in the video."
)
# The model reasons before it answers, out of this same budget. Too low and it never
# reaches an answer: the session still ends `completed`, with an empty manual. On the
# example clip 2048 and 4096 returned nothing, and 16384, 32768 and 65536 returned
# the same manual, so 16384 it is.
DEFAULT_MAX_NEW_TOKENS = 16384

# md5 over the parsed manual with keys sorted, not the file bytes, so formatting
# cannot change it. The 16-step manual the defaults give on prod, committed as
# reference/mga-output-tire_i2JWkDyg26A-batch.json. It changes when `mga` is updated.
KNOWN_MD5 = "866909a9315bce0e493db7ab1627749d"
# An empty manual, `{"id": ..., "results": []}`.
EMPTY_MD5 = "1e724b1286bd1771ec6b3856b47d4daa"

def _on_path(name: str) -> bool:
    return any(os.access(os.path.join(p, name), os.X_OK)
               for p in os.environ.get("PATH", "").split(os.pathsep))


def download_video(path: str) -> None:
    """Fetch the sample clip with yt-dlp.

    ffmpeg is required: the format is separate video and audio streams, and without
    ffmpeg yt-dlp downloads both, skips the merge and still exits 0.
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


def upload_name(path: str) -> str:
    """A unique name to upload under: the stem, a UTC timestamp and 4 random hex,
    e.g. tire_i2JWkDyg26A-20260813T153042Z-9f3a.mp4.

    `file_id` is the filename, in one namespace for your whole organization, and
    uploading the same name again points it at the new file. A run that has not read
    its video yet (the model loads first, for minutes) then fails with
    `S3 object not found`.
    """
    stem, ext = os.path.splitext(os.path.basename(path))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stem}-{stamp}-{uuid.uuid4().hex[:4]}{ext or '.mp4'}"


def content_digest(path: str) -> str:
    """md5 of the manual's content, independent of how it was serialised."""
    return hashlib.md5(
        json.dumps(load_records(path), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_records(path: str) -> list[dict]:
    """Read an MGA output file.

    MGA emits ONE JSON DOCUMENT per run — the platform reports
    `file_extension: "json"` and the body parses as a single object. Older files
    in this repo carry a `.jsonl` extension from when this example assumed
    line-delimited output, so both shapes are accepted: parse as JSON first, fall
    back to one-object-per-line.
    """
    raw = open(path, encoding="utf-8").read().strip()
    if not raw:
        return []
    try:
        doc = json.loads(raw)
        return doc if isinstance(doc, list) else [doc]
    except json.JSONDecodeError:
        return [json.loads(l) for l in raw.splitlines() if l.strip()]


def normalise_id(payload: bytes, canonical: str) -> bytes:
    """Rewrite each record's `id` back to the video's own stem.

    The output `id` is derived from the name the video was uploaded under, so the
    uniqueness suffix from upload_name() would otherwise leak into the results and
    make every run's digest differ — defeating the determinism check below and
    making two people's outputs look unequal when only the filenames differed.
    """
    raw = payload.decode().strip()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:                     # tolerate a line-delimited body
        doc = [json.loads(l) for l in raw.splitlines() if l.strip()]
    for rec in (doc if isinstance(doc, list) else [doc]):
        rec["id"] = canonical
    return (json.dumps(doc, separators=(",", ":")) + "\n").encode()


# The reference video, as it appears in a record's `id`. The digest below is for
# THIS clip; comparing another video's manual against it is meaningless.
REFERENCE_VIDEO = "i2JWkDyg26A"


def uniform_step_durations(results: list[dict]) -> float | None:
    """The step length, if EVERY step has the same one — else None.

    This is a grounding check, not a formatting quirk. Every run measured on a clip
    with no audio track divided the timeline evenly (mouse_assembly: 16 x 5.0s of
    80s; tire_i2JWkDyg26A-silent: 16 x 8.0s of 128s), while every run on a narrated
    clip produced boundaries that vary and land on real events (7 and 6 distinct
    durations). Step boundaries appear to come from the transcript, so with no
    audio there is nothing to segment on and the parser falls back to
    duration / n_steps.

    When that happens the timestamps carry no information — they cannot be traced
    back to the video — and in both measured cases the TEXT was also ungrounded:
    a memorised generic procedure rather than a description of this footage.
    """
    if len(results) < 3:
        return None
    lengths = {round(r["timestamp_end"] - r["timestamp_start"], 3) for r in results}
    return lengths.pop() if len(lengths) == 1 else None


def show(path: str) -> None:
    digest = content_digest(path)
    for rec in load_records(path):
        print(f"\nmanual for {rec.get('id')} — {len(rec.get('results', []))} steps")
        for r in rec.get("results", []):
            print(f"  [{r['timestamp_start']:>6.1f} - {r['timestamp_end']:>6.1f}s] "
                  f"(frames {r['frame_start']:.0f}-{r['frame_end']:.0f})  {r['instruction']}")
        step = uniform_step_durations(rec.get("results", []))
        if step is not None:
            print(f"\n  ⚠️  every step is exactly {step}s long. The timeline was divided"
                  f" evenly rather\n      than localised, which is what happens when the"
                  f" clip has no audio track.\n      Check that the video has one"
                  f" (`ffprobe -show_streams`); if it does not, treat the\n      timestamps"
                  f" as meaningless and the steps as probably not grounded in the\n"
                  f"      footage.")
    print(f"\ncontent md5 {digest}")
    ids = {str(r.get("id", "")) for r in load_records(path)}
    if not any(REFERENCE_VIDEO in i for i in ids):
        # Comparing a different clip's manual against this one's digest tells the
        # reader nothing, but "differs from the reference run" reads like a result.
        print(f"  no reference digest for {', '.join(sorted(ids)) or 'this video'} —"
              f" KNOWN_MD5 is for {REFERENCE_VIDEO} only")
    elif digest == KNOWN_MD5:
        print("  matches the reference run exactly (generation is deterministic)")
    elif digest == EMPTY_MD5:
        print("  this is the EMPTY result: generation ended inside the model's\n"
              "  reasoning block without producing an answer. Raise --max-new-tokens\n"
              f"  to {DEFAULT_MAX_NEW_TOKENS}.")
    else:
        print(f"  differs from the reference run ({KNOWN_MD5}) — different video,"
              f" max_frames,\n  prompt or audio track, or `mga` has been updated since.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", default=VIDEO_PATH)
    ap.add_argument("--download", action="store_true",
                    help="fetch the video into data/clips/ first, then run")
    ap.add_argument("--download-only", action="store_true",
                    help="fetch the video into data/clips/ and stop — no API calls")
    ap.add_argument("--max-frames", type=int, default=64,
                    help="frames uniformly sampled across the whole video. 64 is the "
                         "reader and preprocessor batch size, so they land in one "
                         "batch; the blueprint default is 16. (default: 64)")
    ap.add_argument("--blueprint", default=BLUEPRINT_DEFAULT,
                    help=f"the blueprint to copy, by key or id (default: {BLUEPRINT_DEFAULT!r}). "
                         f"A key resolves to the current version; an old id can stop working.")
    ap.add_argument("--max-new-tokens", type=int, default=DEFAULT_MAX_NEW_TOKENS,
                    help=f"output budget (default {DEFAULT_MAX_NEW_TOKENS}). The model reasons before answering out of this "
                         f"same budget, so anything below ~16k returns an EMPTY manual "
                         f"rather than a short one — 4096 gives 0 steps on the example "
                         f"clip. Raising it above 16384 changes nothing.")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="the instruction (the preflight checks `mga` uses it). Say what to COVER, never how to "
                         "format: the results parser owns the output template, and a "
                         "format-overriding prompt makes it return zero steps.")
    ap.add_argument("--output", default="data/mga-output.json")
    ap.add_argument("--show", metavar="JSONL", help="pretty-print an output file, no API calls")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.show:
        show(args.show)
        return

    values = {"max_frames": args.max_frames, "max_new_tokens": args.max_new_tokens,
              "prompt": args.prompt}

    if args.dry_run:
        endpoint = api.api_base()
        key = project.setting_key(values, args.blueprint)
        print("once per setting (reused while state/lifecycle.json holds it):\n")
        print(f"POST {endpoint}/agents/blueprints/{args.blueprint}/copy")
        print(json.dumps({"project_id": f"<{project.PROJECT}>", "blueprint_key": key,
                          "overrides": {"values": values}}, indent=2))
        print(f"POST {endpoint}/agents/packages")
        print(json.dumps({"blueprint_ids": ["<the copy>"], "target": {"kind": "platform"}},
                         indent=2))
        print(f"POST {endpoint}/agents/deployments")
        print(json.dumps({"package_id": "<the package>", "kind": "batch"}, indent=2))
        print(f"\nper run:\n\nPOST {endpoint}/agents/sessions")
        print(json.dumps({"deployment_id": "<the deployment>", "connectors": {"source": [
            {"type": "file", "id": upload_name(args.video), "format": "mp4"}]}}, indent=2))
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

    # Preflight before uploading: a copy refuses an undeclared value with a 400, but one that is declared and
    # referenced by no node or connector is accepted and has no effect.
    bp = api.get_blueprint(args.blueprint)
    doc = bp["document"]
    wired = json.dumps({"nodes": doc.get("nodes"), "connectors": doc.get("connectors")})
    inert = [k for k in values
             if k not in doc.get("values", {})
             or ("${values." + k + "}") not in wired]
    print(f"blueprint {bp['id']} (key={bp['blueprint_key']}, "
          f"active={bp['is_active']})")
    if not bp["is_active"]:
        print("  NOTE: this blueprint is not the current version.")
    honoured = [k for k in values if k not in inert]
    print(f"  honoured: {honoured}")
    if inert:
        print(f"  WARNING: {inert} is not wired on this blueprint and will be "
              f"silently ignored.")

    print(f"uploading {args.video} ...")
    # Unique per upload: file_id is shared across your organization (upload_name()).
    file_id = api.upload_file(args.video, upload_name(args.video))["file_id"]
    print(f"  file_id={file_id}")

    # The setting's copy, package and batch deployment: made on the first run at
    # this setting, reused after. No `artifacts` map: the mga blueprint pins
    # newton-fusion and whisper itself — the main shape difference from red/osm,
    # which require a fitted classifier artifact.
    print(f"\nthe agent for {project.setting_key(values, args.blueprint)} ...")
    rec = project.agent(values, "batch", source=args.blueprint)
    print(f"  copy {rec['blueprint']}  package {rec['package']}  "
          f"deployment {rec['batch']}")

    session_id = api.open_session(rec["batch"], file_id)["id"]
    project.record_session(session_id, setting=rec["key"], kind="batch",
                           video=args.video, out=args.output)
    print(f"started session\n  session_id={session_id}\n"
          f"  watching its status and /events (~7.5 min of model loading first) ...")

    try:
        verdict = api.poll_session(session_id)
    except KeyboardInterrupt:
        # Never leave a run on the GPU: an abandoned run blocks everyone behind it.
        print(f"\ninterrupted — cancelling {session_id}")
        api.cancel_session(session_id)
        sys.exit(130)
    print(f"\nsession {verdict}")

    payload = api.fetch_results(session_id)
    steps = 0
    if payload is not None:
        # The output `id` comes from the uploaded name; put the video's own stem back.
        payload = normalise_id(payload, os.path.splitext(os.path.basename(args.video))[0])
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        open(args.output, "wb").write(payload)
        print(f"  saved {args.output} ({len(payload)} bytes, "
              f"md5 {hashlib.md5(payload).hexdigest()})")
        # A run can complete with results: [] — seen when the model produced text
        # the parser could not fit to its template, and when max_new_tokens ran out
        # mid-reasoning. The session status does not distinguish that from success,
        # so check the count here.
        steps = sum(len(r.get("results", [])) for r in load_records(args.output))
        if steps == 0:
            print("\n  WARNING: the session completed but produced ZERO steps. Most "
                  "likely\n  the prompt specified an output format the results "
                  "parser rejects,\n  or --max-new-tokens ran out before the answer.")
        show(args.output)
    if verdict != "completed":
        sys.exit(f"session {verdict} — {api.sessions_url(session_id)}/events")
    if steps == 0:                       # `completed` does not mean there is a manual
        sys.exit(f"session {session_id} completed without a manual")


if __name__ == "__main__":
    main()
