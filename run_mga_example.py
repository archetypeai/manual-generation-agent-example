#!/usr/bin/env python3
"""Run the Manual Generation Agent on a CrossTask video, end to end.

Stdlib only apart from mga_api.py and mga_project.py next to it — no pip
install, no virtualenv. Put a .env next to them and run it.

    ATAI_API_KEY=<API key>
    ATAI_API_ENDPOINT=https://platform.u1.archetypeai.app/api

    python3 run_mga_example.py --download     # fetch the video, then run
    python3 run_mga_example.py                # video already on disk
    python3 run_mga_example.py --max-frames 16
    python3 run_mga_example.py --dry-run      # print the plan, call nothing

Expect ~10 minutes on prod (617 s on 2026-10-27, with bundles: ~7 min of model
download and load on every run, since nothing is cached between runs, then ~1.1x
realtime processing). Dev took ~15 minutes.

On the Agent Lifecycle 2.0 a run is: the project's copy of `mga` at this run's
max_frames, max_new_tokens and prompt (made once per setting, see mga_project.py),
its package and batch deployment (also once per setting), and one batch session
over the video. state/lifecycle.json keeps the ids; `python3 cleanup.py --yes`
deletes the sessions, deployments and packages.

Run ONE AT A TIME. Three concurrent runs on dev on 2026-08-13 came up as three
concurrent pods, and one was SIGKILLed (exit 137) partway through loading.

THREE THINGS THAT WILL BITE YOU

  1. Set the values on the copy. A package, a deployment and a batch session take
     none, and dev's `mga` defaults (2026-11) are max_new_tokens 8192 and a prompt
     that caps the manual at ~10 steps. A plain copy runs at those.

  2. A session has /events, not /logs. Events say what the platform did (opened,
     dispatched, failed with a reason); the model-load timings and the parser's
     warnings a bundle run's /logs carried are not there. An empty manual still
     ends `completed`, so count the steps.

  3. `file_id` IS the filename, and it is a MUTABLE, ORG-WIDE pointer. Uploading
     the same name again repoints it and orphans the previous object, so a
     colleague running this example with the default clip kills your in-flight
     run — and the other way round. It surfaced ~7 min later as
     `S3 object not found` (with bundles), blaming storage rather than the
     overwrite. upload_name() below suffixes every upload for this reason.

Also worth knowing: unlike the `red`/`osm` blueprints, MGA needs NO `artifacts`
map — the blueprint pins newton-fusion and whisper itself. And `max_frames`
accepted any value on a bundle (16, 512, 1024, -1 all returned `ready`), so do
not expect the API to stop you setting something the pipeline cannot honour.
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

# CrossTask task 40567 "Change a Tire", video i2JWkDyg26A. 173 s, 640x360,
# 25 fps, all 11 reference steps annotated. Chosen deliberately: MGA fails on
# videos over ~5 minutes, and this one is 58% of that ceiling. Source resolution
# barely matters because the pipeline resizes every frame to 224x224.
VIDEO_URL = "https://www.youtube.com/watch?v=i2JWkDyg26A"
VIDEO_PATH = "data/clips/tire_i2JWkDyg26A.mp4"  # gitignored: videos are not ours to redistribute
# Copy from the blueprint KEY, not an id.
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

# WHAT CHANGED, and what it means for this example. Both of the controls this
# example was written to work around are now exposed on the canonical blueprint —
# so the defaults below are simply honoured, and no workaround is needed:
#
#   max_new_tokens  settable since blp_5wtsdwmp… (was hardcoded at 256, which is
#                   the truncation this example originally documented). The
#                   blueprint's own default is now 16384 — and that default is
#                   LOAD-BEARING, see the note on DEFAULT_MAX_NEW_TOKENS below.
#   prompt          settable again as of blp_6va7xxnrrn9fr8ybhgz3v3zvw7
#                   (canonical 2026-08-12), wired as ${values.prompt}. In between
#                   it was hardcoded to "Generate a concise, ordered list of
#                   distinct steps with 10 steps or less." — a ~10-step ceiling
#                   imposed by wording rather than by content. Stage C of the
#                   README ladder is reproducible again.
#
# Do not take that as permanent. This is the third prompt-related republication in
# a week, and each time the failure was silent: an unwired value is accepted with
# HTTP 201 and echoed straight back. The preflight below checks rather than assumes,
# and you should keep it in any client you build.

# The instruction the default configuration uses. Deliberately says what to cover
# and NOT how to format it: ManualGenerationResultsParserNode owns the output
# template, and a prompt that specifies its own format (markdown headings, for
# instance) makes the parser return zero steps. This one parses cleanly.
#
# REPLACED 2026-08-20. The previous instruction was:
#
#   "Generate a concise, ordered list of every distinct step performed in this
#    video, covering the procedure from the first action to the last. Include
#    brief steps and steps that are repeated. Use up to 20 steps. Keep each step
#    to at most 15 words. Use both what is shown and what is said. Do not invent
#    steps that are not shown or stated in the video."
#
# It degenerated on a second clip. On an 80 s silent mouse-assembly video it
# emitted 16 steps with TWO distinct strings — "Take out the battery." fifteen
# times in a row, on a wired USB mouse with no battery in it. Two clauses caused
# it: "concise" plus "at most 15 words" drove a 4-word median, and nothing in the
# text required consecutive steps to differ, so the decoder re-emitted its own
# highest-probability sentence at temperature 0 with repetition_penalty 1.0.
#
# What replaced them, each clause against an observed failure:
#
#   enumerated detail    Naming the KINDS of detail wanted (part names, quoted
#                        on-screen text, part numbers, tool sizes, direction of
#                        movement) makes a repeated string impossible to emit.
#                        This is the anti-loop mechanism; the word cap was not.
#   distinctness rule    "Each step must describe a different action from the one
#                        before it" — aimed directly at the loop above.
#   imperative framing   Without it the model narrates rather than instructs.
#   modality clause      "rely on the picture alone where the video is silent".
#                        The mouse clip has NO audio stream, so an instruction to
#                        use what is "said" invites an invented narration.
#
# Measured effect, same clip and config, only the prompt changed:
#
#   mouse_assembly.mp4   before: 16 steps,  2 unique, 14 consecutive repeats
#                        after:  16 steps, 16 unique,  0 repeats
#   tire_i2JWkDyg26A     before: 21 steps, 0-111s, 8/11 reference steps, 1 at IoU>=0.5
#                        after:  20 steps, 14-138s, 9/11 reference steps, 4 at IoU>=0.5
#
# Both tire runs were made on 2026-08-20 against blp_3e0hmpqfgc93wvncmr8m30eeny,
# so that pair IS controlled. Do not compare either against the 18-step run in
# reference/: that is an older blueprint and no longer reproduces.
#
# What it did NOT fix, so you know the limits: the grid-term clause produced zero
# grid terms on either clip, and no on-screen text was ever quoted — every frame
# is resized to 224x224 before the model sees it (`size`, not sent here), which
# destroys label text. On the mouse clip the manual is still largely invented: it
# describes a teardown that is not in the footage.
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
# The model REASONS BEFORE IT ANSWERS, and the reasoning is paid for out of this
# budget. Set it too low and generation ends mid-reasoning having emitted no
# answer at all: the job still reports `completed`, with no ERROR row and
# `results: []`. Measured on the 173 s example clip, 2026-08-13, with the PREVIOUS
# default prompt and the blueprint canonical that day:
#
#     2048  ->  0 steps      4096  ->  0 steps
#    16384  -> 18 steps     32768  -> 18 steps     65536  -> 18 steps
#
# The last three are BYTE-IDENTICAL (generation is deterministic at temperature
# 0), so the threshold is between 4k and 16k and nothing above 16k changes the
# answer. Do not lower this to "save tokens" — below the threshold you do not get
# a shorter manual, you get no manual.
#
# The STEP COUNTS above are from that prompt and that blueprint and no longer
# reproduce — today's default yields 20 steps at 16384. The THRESHOLD is the
# claim worth keeping, and it has not been re-measured since the prompt changed.
# A longer prompt does not spend this budget (it is input, not output), but the
# reasoning phase it provokes might, so treat 16384 as the floor it always was.
DEFAULT_MAX_NEW_TOKENS = 16384

# What the defaults below produce on dev. These are CONTENT digests — md5 over the
# parsed manual with keys sorted, not over the file bytes — so reformatting the
# output cannot break the check. Hashing bytes did break it once: changing the
# separators in normalise_id() told everyone who had reproduced the run exactly
# that they had not.
# 20 steps, 14-138s of 173s. Measured 2026-08-20 on blp_3e0hmpqfgc93wvncmr8m30eeny
# with DEFAULT_PROMPT above; committed as
# reference/mga-output-i2JWkDyg26A-20260820-AUDIO-20steps.json.
#
# The previous value, caaea09f1c19645d444fb6ce33f1b139 (18 steps, 11-139s), was
# measured 2026-08-13 and is NOT reproducible today even with the old prompt: the
# same prompt on today's blueprint gives 8b30b0ad6b83bb4a74ea59e37624246c, 21
# steps over 0-111s. The blueprint moved under it. Expect this to happen again.
#
# PROD, 2026-10-27: 16 steps, 14-138s, identical on three runs against
# https://platform.u1.archetypeai.app/api, blp_7vc935235b964bw5b6819dq065;
# committed as reference/mga-output-i2JWkDyg26A-20261027-PROD-16steps.json.
# Prod's `mga` is not dev's (whisper-base instead of whisper:large-v3, renamed
# nodes), so the two digests are expected to differ.
KNOWN_MD5 = "866909a9315bce0e493db7ab1627749d"
DEV_MD5 = "a074294237a829f98be4fb0c82925583"
# An empty manual is `{"id": ..., "results": []}` — it carries no generated text,
# so this digest does not depend on the prompt and survived the change above.
EMPTY_MD5 = "1e724b1286bd1771ec6b3856b47d4daa"
# The original 256-token-capped output, for historic comparison.
TRUNCATED_MD5 = "275677ab10f75f4eb26e3c07d4154f85"

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


def upload_name(path: str) -> str:
    """A collision-proof name to upload under.

    `file_id` IS the filename, and it is a mutable pointer: uploading the same
    name again repoints it to a fresh object and orphans the previous one. That
    is not a local concern — the name lives in one org-wide namespace, so a
    colleague running this example destroys YOUR in-flight run, and vice versa.

    It fails late and looks like a platform fault. Input resolution succeeds at
    submit time, but the pod does not fetch the bytes until ~7.5 min of model
    loading later, so the run dies minutes in with

      ERROR source.running  skipping video "clip.mp4": downloading video
        "s3://…/files_service/archetypeai/c03580dd-…" from S3 failed:
        S3 object not found

    with nothing implicating the upload that overwrote it. Suffix every upload.

    Original stem + UTC timestamp + 4 random hex, e.g.

      tire_i2JWkDyg26A-20260813T153042Z-9f3a.mp4

    The stem keeps it recognisable in a shared namespace and the timestamp sorts;
    the random tail is the part that actually guarantees uniqueness, since two
    people starting within the same second is precisely the case being fixed.
    """
    stem, ext = os.path.splitext(os.path.basename(path))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stem}-{stamp}-{uuid.uuid4().hex[:4]}{ext or '.mp4'}"


def content_digest(path: str) -> str:
    """md5 of the manual's CONTENT, independent of how it was serialised.

    Hashing raw file bytes was a mistake: it made the reproducibility check
    hostage to our own formatting. Changing the separators in normalise_id() once
    was enough to report a mismatch to everyone who had in fact reproduced the run
    exactly — the worst possible failure for a check whose whole job is to tell you
    whether the model gave you the same answer.

    Keys are sorted and separators fixed here, so only the manual itself counts.
    """
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
                  f"      footage — see the narration ablation in the README.")
    print(f"\ncontent md5 {digest}")
    ids = {str(r.get("id", "")) for r in load_records(path)}
    if not any(REFERENCE_VIDEO in i for i in ids):
        # Comparing a different clip's manual against this one's digest tells the
        # reader nothing, but "differs from the reference run" reads like a result.
        print(f"  no reference digest for {', '.join(sorted(ids)) or 'this video'} —"
              f" KNOWN_MD5 is for {REFERENCE_VIDEO} only")
    elif digest == KNOWN_MD5:
        print("  matches the reference run exactly (generation is deterministic)")
    elif digest == DEV_MD5:
        print("  matches the dev reference run of 2026-08-20 (20 steps), not prod's")
    elif digest == EMPTY_MD5:
        print("  this is the EMPTY result: generation ended inside the model's\n"
              "  reasoning block without producing an answer. Raise --max-new-tokens\n"
              f"  to {DEFAULT_MAX_NEW_TOKENS}.")
    elif digest == TRUNCATED_MD5:
        print("  this is the historic TRUNCATED output from the 256-token cap\n"
              "  (6 steps, 16-85s of 173s). That cap is long gone; you are probably\n"
              "  looking at an archived file rather than a fresh run.")
    else:
        print(f"  differs from the reference run ({KNOWN_MD5}) — different video,"
              f" max_frames,\n  prompt, audio track, or a changed blueprint. The"
              f" blueprint is the usual answer:\n  it is republished under this example"
              f" every few days.")


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
                    help=f"the blueprint to copy, by id or KEY (default: {BLUEPRINT_DEFAULT!r}). "
                         f"A key tracks whatever is canonical and active. A pinned id "
                         f"can stop resolving when the blueprint is republished — which "
                         f"is exactly what happened to the id this used to default to.")
    ap.add_argument("--max-new-tokens", type=int, default=DEFAULT_MAX_NEW_TOKENS,
                    help=f"output budget (default {DEFAULT_MAX_NEW_TOKENS}, matching the "
                         f"blueprint). The model reasons before answering out of this "
                         f"same budget, so anything below ~16k returns an EMPTY manual "
                         f"rather than a short one — 4096 gives 0 steps on the example "
                         f"clip. Raising it above 16384 changes nothing.")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="the instruction, honoured as ${values.prompt} on the "
                         "canonical blueprint (verified by the preflight, which warns "
                         "if that stops being true). Say what to COVER, never how to "
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

    # Preflight BEFORE uploading: a value the blueprint will silently ignore should
    # be reported before spending bandwidth and 15 minutes of GPU, not after.
    # A copy refuses an undeclared value with a 400, but one that is declared and
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
        print("  NOTE: this blueprint is superseded — results cannot ship.")
    honoured = [k for k in values if k not in inert]
    print(f"  honoured: {honoured}")
    if inert:
        print(f"  WARNING: {inert} is not wired on this blueprint and will be "
              f"silently ignored.")
        if "prompt" in inert:
            print(f"    prompt has been unwired before. When it is, the blueprint's "
                  f"own instruction applies:\n"
                  f"    {doc.get('values', {}).get('prompt')!r}\n"
                  f"    which caps the manual at ~10 steps however long the video is.")

    print(f"uploading {args.video} ...")
    # Unique per upload: file_id is an org-wide mutable pointer, so a plain
    # basename lets a colleague's run overwrite this one's input mid-flight.
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


if __name__ == "__main__":
    main()
