#!/usr/bin/env python3
"""MGA over agent serving: load the model once, then one /query per clip.

    python3 sweep_serve.py --list                            # the clips, and which can be scored
    python3 sweep_serve.py --clips tire_i2JWkDyg26A          # one clip
    python3 sweep_serve.py --all                             # every clip, one session
    python3 sweep_serve.py --clips tire_i2JWkDyg26A --repeat 3
    python3 sweep_serve.py --all --keep                      # leave the session loaded
    python3 sweep_serve.py --all --session ses_…             # reuse it: no model load
    python3 sweep_serve.py --all --dry-run                   # encode, print the plan, call nothing
    python3 sweep_serve.py --all --background                # nohup + caffeinate, log to logs/
    python3 evaluate.py eval_serving/mga-output-tire_i2JWkDyg26A.json

    python3 sweep_serve.py --all --chunk 90 --overlap 15 --dry-run   # cut, print the plan
    python3 sweep_serve.py --all --chunk 90 --overlap 15
    python3 merge_chunks.py eval_serving_c90o15      # stitch the chunks per rule, score each

With --chunk, each clip goes as several shorter videos instead of one, and every
chunk's raw answer is saved to <out-dir>/chunks/<clip>.json for merge_chunks.py.
A shorter video gets its own 64 frames and fits the cap with far less
re-encoding than the whole clip. Every chunk after the first is asked with
CHUNK_PREAMBLE + --prompt: told it is a whole video, a chunk invents an
introduction and a conclusion. The first chunk, and a clip short enough to be
one chunk, is asked with --prompt alone: it is the video's real start, and told
it came from the middle, it lost the clock (every step at 0.0-0.0 s).

A batch run (run_mga_example.py) pays ~7 min of model download and load for
every video. Serving deploys the setting's package (the same copy of `mga`
run_mga_example.py uses, see mga_project.py) as `serving` and opens one session
on it: the model loads once and the session answers
POST /agents/sessions/{id}/query per video.

Limits, as measured with bundles on dev (2026-10-28):
  - one GPU worker at a time on dev
  - the video goes inline (`"file_ids": ["base64:<mp4>"]`) and the whole body is
    capped at 2 MiB, so every clip is re-encoded to fit, into data/clips-serving/.
    MGA samples 64 frames at 224 px, so frame size and rate can drop a long way;
    the soundtrack must stay, since whisper's transcript is half the input
  - each query has a 58 s budget; a 504 is retried
  - a serving session with no query for 30 minutes is closed (the deployment's
    default idle timeout)
  - a fresh agent can answer a chunk differently, even at temperature 0

A bad video fails only its own query, with a 400, and the sweep moves on.

The session is cancelled and the serving deployment stopped and deleted at the
end, Ctrl-C and `kill` included, unless --keep: it holds a GPU worker.
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import subprocess
import sys
import time

import mga_api as api
import mga_project as project
from background import add_background_flag, maybe_detach
from run_mga_example import DEFAULT_MAX_NEW_TOKENS, DEFAULT_PROMPT

OUT_DIR = "eval_serving"
CLIP_CACHE = "data/clips-serving"
BODY_CAP = 2 * 1024 * 1024
# Tried in order until the request fits: (height, fps, video CRF, audio kbit/s).
# 64 frames are sampled across the whole video, so even 2 fps leaves plenty to
# sample from; mono 16 kHz is what whisper resamples to anyway.
ENCODES = [(360, 15, 30, 32), (360, 8, 32, 24), (240, 5, 32, 24), (180, 4, 34, 16), (144, 2, 36, 16)]
# A chunk tries the source's own frame size and rate first (None keeps it).
CHUNK_ENCODES = [(None, None, 23, 64)] + ENCODES
# A tail chunk with fewer frames than max_frames (64) is mostly an end card. It also
# used to end the served agent (TVA on dev, 2026-10-26; since fixed).
MIN_CHUNK_FRAMES = 64
# From manual-gen-with-manual-generation-agent (src/lib/prompts.ts), where it is
# prepended to every chunk of a split video; here, to every chunk after the first.
# Without it each chunk is treated as a complete video: on 2026-10-27 the tire
# clip's first 60 s chunk opened with "Wait for the Toyota logo to appear on the screen".
CHUNK_PREAMBLE = (
    "This video is one segment taken from the middle of a longer recording of a "
    "single continuous procedure. List only the steps performed within this "
    "segment. Do not summarise, introduce, or conclude the overall procedure, and "
    "do not describe the segment as beginning or ending anything — the steps "
    "before and after it are covered elsewhere. Work at a fine grain: a single "
    "button press, acknowledging a dialog, reading a value, or watching an "
    "indicator each count as their own step, and steps only seconds apart are "
    "expected."
)
QUERY_TIMEOUT = 120     # the platform's budget is 58 s; past this the request is lost
QUERY_RETRIES = 3


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def query_body(video: bytes, prompt: str) -> dict:
    return {"model": "mga", "query": prompt, "file_ids": ["base64:" + base64.b64encode(video).decode()]}


def body_size(size: int, prompt: str) -> int:
    return len(json.dumps(query_body(b"\0" * size, prompt)).encode())


def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return bool(out.strip())


def encode_args(height: int | None, fps: float | None, crf: int, kbps: int, audio: bool) -> list[str]:
    filters = ([f"scale=-2:{height}"] if height else []) + ([f"fps={fps}"] if fps else [])
    a = ["-c:a", "aac", "-ac", "1", "-ar", "16000", "-b:a", f"{kbps}k"] if audio else ["-an"]
    return (["-vf", ",".join(filters)] if filters else []) + [
        "-c:v", "libx264", "-crf", str(crf), "-preset", "slow", *a, "-movflags", "+faststart"]


def encode_note(height: int | None, fps: float | None, crf: int, kbps: int, audio: bool) -> str:
    return (f"{height}p" if height else "source size") + (f" {fps} fps" if fps else " source fps") \
        + f" crf{crf}" + (f" aac {kbps}k mono" if audio else " no audio")


def shrink(path: str, prompt: str) -> tuple[str, str]:
    """Re-encode a clip until its query fits the 2 MiB cap. Cached by name."""
    if body_size(os.path.getsize(path), prompt) <= BODY_CAP:
        return path, "original"
    stem = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(CLIP_CACHE, exist_ok=True)
    out = os.path.join(CLIP_CACHE, f"{stem}.mp4")
    note = out + ".encode"
    if os.path.exists(out) and os.path.exists(note) and body_size(os.path.getsize(out), prompt) <= BODY_CAP:
        return out, open(note).read().strip()
    audio = has_audio(path)
    for rung in ENCODES:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, *encode_args(*rung, audio), out], check=True)
        if body_size(os.path.getsize(out), prompt) <= BODY_CAP:
            how = encode_note(*rung, audio)
            open(note, "w").write(how + "\n")
            return out, how
    sys.exit(f"{path}: no encode fits the 2 MiB request cap")


def probe(path: str) -> tuple[float, float]:
    """Duration and frame rate of a clip."""
    def ffprobe(*a: str) -> str:
        return subprocess.run(["ffprobe", "-v", "error", *a, "-of", "csv=p=0", path],
                              check=True, capture_output=True, text=True).stdout
    num, den = ffprobe("-select_streams", "v:0", "-show_entries", "stream=r_frame_rate").split(",")[0].strip().split("/")
    return float(ffprobe("-show_entries", "format=duration")), float(num) / float(den)


def cut_chunks(path: str, seconds: float, overlap: float, prompt: str) -> dict:
    """Cut a clip into chunks of `seconds`, each sharing `overlap` with the next. Cached.

    Each chunk is re-encoded, best quality first, until its query fits the cap, and
    keeps its soundtrack. Steps come back in chunk time, so `offset` is kept.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(CLIP_CACHE, stem, f"c{seconds:g}o{overlap:g}")
    manifest = os.path.join(folder, "manifest.json")
    if os.path.exists(manifest):
        with open(manifest) as f:
            return json.load(f)
    duration, fps = probe(path)
    starts = [0.0]
    while starts[-1] + seconds < duration - 0.05:
        starts.append(starts[-1] + seconds - overlap)
    windows = [(t, min(t + seconds, duration)) for t in starts]
    # A tail that adds less new footage than the overlap joins the chunk before:
    # on its own it is mostly an end card or a still, which a chunk describes as
    # steps ("Press the 'Enter' key." x11). So does one too short for 64 frames.
    if len(windows) > 1 and (windows[-1][1] - windows[-2][1] < overlap
                             or (windows[-1][1] - windows[-1][0]) * fps < MIN_CHUNK_FRAMES + 2):
        windows[-2:] = [(windows[-2][0], windows[-1][1])]
    audio = has_audio(path)
    os.makedirs(folder, exist_ok=True)
    chunks = []
    for i, (start, end) in enumerate(windows):
        out = os.path.join(folder, f"{i:02d}.mp4")
        length = end - start
        for rung in CHUNK_ENCODES:
            if (rung[1] or fps) * length < MIN_CHUNK_FRAMES + 2:
                continue
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", path,
                            "-t", f"{length:.3f}", *encode_args(*rung, audio), out], check=True)
            if body_size(os.path.getsize(out), prompt) <= BODY_CAP:
                break
        else:
            sys.exit(f"{path} chunk {i}: no encode fits the 2 MiB request cap")
        chunks.append({"file": out, "offset": round(start, 3), "duration": round(length, 3),
                       "encode": encode_note(*rung, audio), "bytes": os.path.getsize(out)})
    plan = {"source": path, "fps": fps, "duration": duration, "seconds": seconds,
            "overlap": overlap, "chunks": chunks}
    with open(manifest, "w") as f:
        json.dump(plan, f, indent=2)
    return plan


def manual_doc(answer: dict, stem: str) -> dict | None:
    """A served answer as batch's {"id", "results"} document, so evaluate.py reads it."""
    rows = (answer.get("response") or {}).get("data")
    return {"id": stem, "results": rows} if isinstance(rows, list) else None


def new_events(session_id: str, seen: set) -> None:
    """Print the session's events not seen yet. A session has no /logs."""
    status, body = api.request("GET", f"{api.sessions_url(session_id)}/events?limit=100",
                               allow_status=True)
    if status != 200 or not isinstance(body, dict):
        return
    for row in reversed(body.get("data") or []):          # newest first
        key = (row.get("created_at"), row.get("message"))
        if key not in seen:
            seen.add(key)
            log(f"  {str(row.get('level', 'info')):7} {row.get('message')}")


def release(session_id: str, deployment_id: str | None, setting: str | None) -> str:
    """Cancel and delete the session, then stop and delete its serving deployment.

    The deployment is what holds the GPU worker; a session alone does not. The
    package and the copy stay: run_mga_example.py's batch deployment uses them
    too, and cleanup.py deletes the package."""
    api.end_session(session_id)
    if not deployment_id:
        return f"cancelled and deleted {session_id}"
    api.end_deployment(deployment_id)
    if setting:
        project.forget(setting, "serving")
    return f"cancelled and deleted {session_id}, stopped and deleted {deployment_id}"


class AgentGone(Exception):
    """The session no longer answers: cancelled, closed after 30 idle minutes, or crashed."""


def ask(session: str, body: dict, deadline: float, seen: set) -> tuple[dict, float, int]:
    """POST /query, retrying 5xx and timeouts (the cold-agent 504).

    Until `deadline` (the model load) it retries without limit; after that, a few
    times. A query that hangs is not activity, and a session without a query for
    30 minutes is closed, so a stuck request must not be waited out.
    """
    tries = 0
    while True:
        tries += 1
        t = time.time()
        code, answer = api.request("POST", f"{api.sessions_url(session)}/query",
                                   body=body, allow_status=True, retries=0, timeout=QUERY_TIMEOUT)
        took = time.time() - t
        if code == 200:
            return answer, took, tries
        new_events(session, seen)
        log(f"    {code or 'no answer'} after {took:.0f} s: {str(answer)[:200]}")
        if code == 409:
            raise AgentGone(str(answer))
        if not (code >= 500 or code == 0) or (tries > QUERY_RETRIES and time.time() > deadline):
            raise RuntimeError(f"query failed ({code}): {str(answer)[:300]}")
        time.sleep(15)


def summary(doc: dict) -> str:
    rows = doc.get("results") or []
    if not rows:
        return "0 steps"
    texts = [r.get("instruction", "") for r in rows]
    span = f"{rows[0].get('timestamp_start', 0):.0f}-{rows[-1].get('timestamp_end', 0):.0f} s"
    return f"{len(rows)} steps, {len(set(texts))} distinct, {span}"


def query_video(session: str, video_path: str, prompt: str, stem: str, repeat: int,
                deadline: float, seen: set, entry: dict) -> dict | None:
    """Ask `repeat` times about one video; the first answer, as {"id", "results"}."""
    with open(video_path, "rb") as f:
        body = query_body(f.read(), prompt)
    entry["queries"] = []
    docs = []
    for r in range(repeat):
        try:
            answer, took, tries = ask(session, body, max(deadline, time.time() + 120), seen)
        except RuntimeError as e:
            entry["queries"].append({"error": str(e)})
            log(f"    FAILED: {e}")
            break
        doc = manual_doc(answer, stem)
        entry["queries"].append({"seconds": round(took, 1), "tries": tries,
                                 "server_ms": answer.get("query_response_time_ms"),
                                 "steps": len((doc or {}).get("results") or [])})
        if doc is None:
            log(f"    query {r + 1}: no manual in the answer: {str(answer)[:300]}")
            continue
        docs.append(doc)
        log(f"    query {r + 1}: {took:.1f} s" + (f" ({tries} tries)" if tries > 1 else "")
            + f"  {summary(doc)}")
    if len(docs) > 1:
        entry["identical"] = all(d["results"] == docs[0]["results"] for d in docs)
        log(f"    {len(docs)} answers identical: {entry['identical']}")
    return docs[0] if docs else None


def resume(path: str, plan: dict) -> None:
    """Carry an earlier run's answers into `plan`, chunk by chunk."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        doc = json.load(f)
    saved = {(c["offset"], c["duration"]): c for c in doc["chunks"]}
    for c in plan["chunks"]:
        old = saved.get((c["offset"], c["duration"]))
        # A chunk asked with another query is asked again. Older files keep one query per clip.
        if old and old.get("rows") is not None and old.get("query", doc.get("query")) == c["query"]:
            c["rows"] = old["rows"]


def output_path(args, stem: str) -> str:
    if args.chunk:
        return os.path.join(args.out_dir, "chunks", f"{stem}.json")
    return os.path.join(args.out_dir, f"mga-output-{stem}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", nargs="+", help="clip stems in --dir")
    ap.add_argument("--all", action="store_true", help="every mp4 in --dir")
    ap.add_argument("--dir", default="data/clips")
    ap.add_argument("--list", action="store_true", help="list the clips in --dir and stop")
    ap.add_argument("--out-dir", default=None,
                    help=f"default {OUT_DIR}/, or {OUT_DIR}_c<chunk>[o<overlap>]/ with --chunk")
    ap.add_argument("--chunk", type=float, default=0, metavar="SECONDS",
                    help="send each clip as chunks of this length; merge with merge_chunks.py")
    ap.add_argument("--overlap", type=float, default=0, metavar="SECONDS",
                    help="seconds each chunk shares with the next (with --chunk)")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--max-frames", type=int, default=64)
    ap.add_argument("--max-new-tokens", type=int, default=DEFAULT_MAX_NEW_TOKENS)
    ap.add_argument("--repeat", type=int, default=1, help="queries per clip, to check determinism")
    ap.add_argument("--fresh", action="store_true", help="re-run clips that already have an output")
    ap.add_argument("--dry-run", action="store_true", help="encode, print what would be sent, call nothing")
    ap.add_argument("--blueprint", default="mga", help="the blueprint to copy (default: mga)")
    ap.add_argument("--session", metavar="ID", help="query this running serving session; no new load")
    ap.add_argument("--keep", action="store_true",
                    help="leave the session and its deployment running, for --session")
    ap.add_argument("--ready-timeout", type=int, default=1800,
                    help="seconds to wait for the session to load (~7-12 min)")
    add_background_flag(ap, default_log="logs/sweep_serve.log")
    args = ap.parse_args()
    maybe_detach(args)

    available = sorted(glob.glob(os.path.join(args.dir, "*.mp4")))
    if args.list:
        for p in available:
            stem = os.path.splitext(os.path.basename(p))[0]
            sound = "audio" if has_audio(p) else "SILENT"
            scored = "scored by evaluate.py" if stem == "tire_i2JWkDyg26A" else ""
            print(f"  {stem:<28s} {os.path.getsize(p) / 1e6:5.1f} MB  {sound:6}  {scored}")
        return
    if args.all:
        clips = available
    elif args.clips:
        clips = [os.path.join(args.dir, c if c.endswith(".mp4") else c + ".mp4") for c in args.clips]
        missing = [c for c in clips if not os.path.exists(c)]
        if missing:
            sys.exit(f"no such clip: {', '.join(missing)}")
    else:
        sys.exit("pass --clips <stems...> or --all (or --list)")

    if args.overlap and not args.chunk:
        sys.exit("--overlap needs --chunk")
    if args.chunk and args.overlap >= args.chunk:
        sys.exit("--overlap must be shorter than --chunk")
    if args.out_dir is None:
        args.out_dir = OUT_DIR + (f"_c{args.chunk:g}" + (f"o{args.overlap:g}" if args.overlap else "")
                                  if args.chunk else "")

    stems = {p: os.path.splitext(os.path.basename(p))[0] for p in clips}
    if args.chunk:
        # Done means every chunk of the CURRENT cut has an answer to its CURRENT
        # query, so a rerun asks only the chunks still unanswered.
        # Sized against the longer, preamble query, so either query fits.
        plans = {p: cut_chunks(p, args.chunk, args.overlap, f"{CHUNK_PREAMBLE} {args.prompt}") for p in clips}
        for plan in plans.values():
            for n, c in enumerate(plan["chunks"]):
                c["query"] = f"{CHUNK_PREAMBLE} {args.prompt}" if n else args.prompt
        if not args.fresh:
            for p, plan in plans.items():
                resume(output_path(args, stems[p]), plan)
        todo = [p for p in clips if any(c.get("rows") is None for c in plans[p]["chunks"])]
        n_queries = sum(c.get("rows") is None for p in todo for c in plans[p]["chunks"])
    else:
        todo = [p for p in clips if args.fresh or not os.path.exists(output_path(args, stems[p]))]
        n_queries = len(todo)
    if not todo:
        sys.exit(f"nothing to run: every clip already has an output in {args.out_dir}/ (--fresh to re-run)")
    if not args.chunk:
        shrunk = {p: shrink(p, args.prompt) for p in todo}
    if args.dry_run:
        for p in todo:
            if args.chunk:
                print(f"{stems[p]}  {len(plans[p]['chunks'])} chunks")
                for c in plans[p]["chunks"]:
                    print(f"    {c['offset']:5.1f}-{c['offset'] + c['duration']:5.1f} s  "
                          f"{c['bytes']:>9,} B  ({c['encode']})")
                continue
            sent, how = shrunk[p]
            print(f"{stems[p]:28} {os.path.getsize(p):>11,} B -> {os.path.getsize(sent):>9,} B  "
                  f"body {body_size(os.path.getsize(sent), args.prompt):>9,} B  ({how})")
        print(f"\n{len(todo)} clip(s), {n_queries * args.repeat} queries -> {args.out_dir}/")
        return

    api.load_dotenv()
    os.makedirs(os.path.dirname(output_path(args, "x")), exist_ok=True)
    log(f"{len(todo)} clip(s), {n_queries * args.repeat} queries")

    seen: set = set()
    setting = None
    t0 = time.time()
    if args.session:
        session = args.session
        deployment = api.request("GET", api.sessions_url(session)).get("deployment_id")
    else:
        values = {"max_frames": args.max_frames, "max_new_tokens": args.max_new_tokens, "prompt": args.prompt}
        rec = project.agent(values, "serving", source=args.blueprint)
        setting, deployment = rec["key"], rec["serving"]
        session = api.open_session(deployment)["id"]
        project.record_session(session, setting=setting, kind="serving")
        log(f"{setting} · serving deployment {deployment} · session {session} · "
            f"max_frames {args.max_frames}, max_new_tokens {args.max_new_tokens}")
    run_log = {"session": session, "deployment": deployment, "setting": setting, "clips": []}
    try:
        api.wait_running(session, timeout_s=args.ready_timeout)
        log(f"session running after {time.time() - t0:.0f} s")
        deadline = t0 + args.ready_timeout     # only the first answer may wait on the load
        for i, path in enumerate(todo, 1):
            stem = stems[path]
            if not args.chunk:
                sent, how = shrunk[path]
                log(f"[{i}/{len(todo)}] {stem}  ({how}, {os.path.getsize(sent):,} B)")
                entry = {"clip": stem, "sent": sent, "encode": how}
                run_log["clips"].append(entry)
                doc = query_video(session, sent, args.prompt, stem, args.repeat, deadline, seen, entry)
                if doc:
                    with open(output_path(args, stem), "w") as f:
                        f.write(json.dumps(doc, separators=(",", ":")) + "\n")
                continue
            plan = plans[path]
            log(f"[{i}/{len(todo)}] {stem}  {len(plan['chunks'])} chunks")
            entry = {"clip": stem, "chunks": []}
            run_log["clips"].append(entry)
            try:
                for c in plan["chunks"]:
                    if c.get("rows") is not None:
                        continue                    # answered by an earlier run
                    c_entry = {"offset": c["offset"], "encode": c["encode"]}
                    entry["chunks"].append(c_entry)
                    log(f"  chunk {c['offset']:5.1f}-{c['offset'] + c['duration']:5.1f} s  "
                        f"({c['encode']}, {c['bytes']:,} B)")
                    doc = query_video(session, c["file"], c["query"], stem, args.repeat, deadline, seen, c_entry)
                    c["rows"] = doc["results"] if doc else None
            finally:
                if any(c.get("rows") is not None for c in plan["chunks"]):
                    with open(output_path(args, stem), "w") as f:
                        json.dump({"clip": stem, **plan}, f, indent=2)
    except AgentGone as e:
        run_log["stopped"] = str(e)
        log(f"STOPPED: the session no longer answers ({e}).\n"
            f"  Answers so far are saved; run the same command again to resume where it stopped.")
    finally:
        if args.keep:
            log(f"session {session} left running (--session {session}); closed after 30 idle min, "
                f"and its deployment {deployment} stays up until cleanup.py")
        else:
            log(release(session, deployment, setting))
        run_log["seconds"] = round(time.time() - t0)
        with open(os.path.join(args.out_dir, "serve-log.json"), "w") as f:
            json.dump(run_log, f, indent=2)
    if args.chunk:
        log(f"done in {run_log['seconds']} s. stitch and score it:  python3 merge_chunks.py {args.out_dir}")
    else:
        log(f"done in {run_log['seconds']} s. score the tire clip:  "
            f"python3 evaluate.py {args.out_dir}/mga-output-tire_i2JWkDyg26A.json")


if __name__ == "__main__":
    main()
