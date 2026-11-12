# Manual Generation Agent — End-to-End Example

Run Archetype AI's **Manual Generation Agent (MGA)** on a procedure video and get back
an ordered, timestamped manual, then score it against reference annotations. The
example runs on Agent Lifecycle 2.0 (projects, packages, deployments and sessions),
in batch and in serving.

The video is a 2:53 roadside tire change from [CrossTask](#data-attribution), with
11 reference steps.

```
video ─► sample frames ─┐
                        ├─► Newton ─► parse ─► steps + timestamps
        speech-to-text ─┘
```

MGA is zero-shot: the `mga` blueprint brings its own models, so there is nothing to
fit and no data to prepare. You set three values, run it on a video, and read the
manual.

## Verified on production

Run against `https://platform.u1.archetypeai.app/api` on 2026-11-12:

| | Batch (`run_mga_example.py`) | Serving (`sweep_serve.py`) |
|---|---|---|
| steps | 16, over 14–138 s of the 173 s clip | 15, over 14–138 s |
| reference steps found at IoU ≥ 0.3 | 11/11 | 9/11 |
| … at IoU ≥ 0.5 | 7/11 | 6/11 |
| time | 12.4 min for the session | 879 s for the deployment to start, then 27.3 s for the query |
| output | [`reference/mga-output-tire_i2JWkDyg26A-batch.json`](reference/mga-output-tire_i2JWkDyg26A-batch.json) | [`reference/mga-output-tire_i2JWkDyg26A-serving.json`](reference/mga-output-tire_i2JWkDyg26A-serving.json) |

Generation is deterministic: the batch run returned the same manual as earlier
runs, byte for byte (content md5 `866909a9…`). The clip's first 14 s are an
intro and its last 35 s are branding, so 14–138 s is the whole procedure.

## Quick start

You need Python 3.10+ (every script is standard library only), an API key, and
`yt-dlp` and `ffmpeg` to fetch the video.

```sh
brew install yt-dlp ffmpeg     # or: apt install yt-dlp ffmpeg
cp .env.example .env           # set ATAI_API_KEY and ATAI_API_ENDPOINT

python3 run_mga_example.py
```

It fetches the video, uploads it, makes the agent (a copy of `mga` with the values
set, its package and a batch deployment), opens a session, prints the session's
events while it runs, then downloads the manual and prints it:

```
[  14.0 -   19.0s] (frames  350- 475)  Stop your car safely on a firm level surface, away from traffic if possible.
…
[ 132.0 -  138.0s] (frames 3300-3450)  Do not exceed 80 kilometres per hour when driving with a compact spare wheel.

content md5 866909a9315bce0e493db7ab1627749d
  matches the reference run exactly (generation is deterministic)
```

Then score it, offline:

```sh
python3 evaluate.py data/mga-output.json
```

`ffmpeg` is required: the video comes as separate video and audio streams, and
without ffmpeg yt-dlp downloads both, skips the merge and still exits 0. Already have
the clip? Pass `--video <path>`.

## How it works

`run_mga_example.py` makes these calls. `--dry-run` prints them without making any.

| Step | Call | Notes |
|---|---|---|
| 1. Upload | `POST /files` | Under a unique name: `file_id` is the filename, shared across your organization, so reusing a name replaces the file under any run still reading it. |
| 2. Project | `POST /projects` | `MGA CrossTask`, found by name after the first run. |
| 3. Copy of `mga` | `POST /agents/blueprints/mga/copy` | With `max_frames`, `max_new_tokens` and `prompt` as `overrides.values`. One copy per setting, keyed `mga-f64-t16384-pc647bb`. |
| 4. Package | `POST /agents/packages` | Poll until `ready`. |
| 5. Deployment | `POST /agents/deployments` | `kind: batch`; ready at once. |
| 6. Session | `POST /agents/sessions` | One per video: `connectors.source` names the uploaded file. |
| 7. Watch | `GET /agents/sessions/{id}` and `/events` | `pending → running → completed`, or `failed` with an `error`. |
| 8. Results | `GET /agents/sessions/{id}/results` | The manual is a file: `ref` is a root path (`/api/files/…/content`), so resolve it against the host. |

Steps 2–5 happen once per setting. `state/lifecycle.json` (gitignored) keeps the ids,
and later runs reuse them.

### The three values

| Value | This example sends | Why |
|---|---|---|
| `max_frames` | 64 | Frames sampled evenly across the video. The blueprint default is 16; prod accepts 1–64. |
| `max_new_tokens` | 16384 | The model reasons before it answers, out of this same budget. The blueprint default is 8192; on this clip 2048 and 4096 returned an **empty** manual, and 16384 and above returned the same full one. |
| `prompt` | [`DEFAULT_PROMPT`](run_mga_example.py) | Say what to **cover**, never how to format: the results parser owns the output format, and a prompt that sets its own (markdown headings, say) returns zero steps. The blueprint's default asks for "10 steps or less". |

A package, a deployment and a batch session take no values, so they go on the copy.
Before uploading, the script checks that `mga` actually uses each of the three.

### The output

One JSON document per run:

```json
{"id": "tire_i2JWkDyg26A", "results": [
  {"step": 0, "instruction": "Stop your car safely on a firm level surface, away from traffic if possible.",
   "frame_start": 350.0, "frame_end": 475.0, "timestamp_start": 14.0, "timestamp_end": 19.0},
  …]}
```

- `step` is 0-based. Frame indices are the source video's.
- The steps tile the timeline with no gaps, so a spoken caution becomes its own step.

## Serving

A batch session loads the model for every video. A serving deployment loads it
once, then answers a query per video in seconds:

```sh
python3 sweep_serve.py --clips tire_i2JWkDyg26A     # videos in data/clips/
python3 evaluate.py eval_serving/mga-output-tire_i2JWkDyg26A.json
```

It deploys the same setting's package as `serving`, opens one session, waits for it
to be `running` (about 15 minutes), then sends each video to
`POST /agents/sessions/{id}/query`. At the end it cancels and deletes the session and
stops and deletes the deployment, which holds a GPU worker; Ctrl-C and `kill` do the
same. `--keep` leaves both up for `--session ses_…`.

- **The video goes inline, and a request is capped at 2 MiB**, so each clip is
  re-encoded to fit (frames are resized to 224 px for the model anyway).
- **Longer videos go in chunks:** `--chunk 90 --overlap 15` sends 90 s pieces, and
  `python3 merge_chunks.py eval_serving` stitches them into one manual per clip.
- A session with no query for 30 minutes is closed.

## Things to know

- **Keep videos under about 5 minutes** in batch. Longer runs fail partway through.
- **Use a video with narration.** Step boundaries come from the speech. A silent
  video gets steps of equal length and a manual written largely from what the model
  expects, not from the footage; the script warns when every step is the same length.
- **A `completed` session can hold an empty manual.** The script counts the steps and
  exits non-zero when there are none.
- **Reference the blueprint by key (`mga`)**, which resolves to the current version.
  When `mga` is updated, the manual can change, and the md5 with it.
- **Score with care.** `evaluate.py` reports reference-step recall and temporal IoU,
  not precision: CrossTask lists a fixed procedure per task, so real steps it omits
  (removing a hubcap, a spoken caution) are shown but not counted against the manual.
  A manual with fewer, longer steps can score better and be harder to follow.

## Cleanup

```sh
python3 cleanup.py          # list what would be deleted
python3 cleanup.py --yes    # delete it
```

It deletes the sessions, deployments and packages this example made, in that order,
starting from the packages in `state/lifecycle.json`. The project and its copies of
`mga` stay, so the next run only packages again. Downloaded manuals and uploaded
videos are not touched. `state/` belongs to one platform endpoint: to run against
another, move it aside.

## Repo layout

| File | |
|---|---|
| `run_mga_example.py` | Batch: one session per video, end to end. |
| `sweep_serve.py` | Serving: one deployment and session, a query per video or chunk. |
| `merge_chunks.py` | Stitches chunked serving answers into one manual per clip. Offline. |
| `evaluate.py` | Scores a manual against the CrossTask annotations. Offline. |
| `cleanup.py` | Deletes what the scripts made. |
| `mga_api.py`, `mga_project.py` | The API client and the project, copy, package and deployment bookkeeping. |
| `background.py` | `--background` for `sweep_serve.py`. |
| `reference/` | The verified batch and serving manuals. |

## Data attribution

Annotations and step lists come from **CrossTask**:

> D. Zhukov, J.-B. Alayrac, R. G. Cinbis, D. Fouhey, I. Laptev, J. Sivic.
> "Cross-task weakly supervised learning from instructional videos", CVPR 2019.
> https://github.com/DmZhukov/CrossTask · https://arxiv.org/abs/1903.08225

The CrossTask repository is BSD-3-Clause. The videos are not distributed by the
authors and are copyrighted by their uploaders; this repo commits neither the videos
nor the annotation dump, only the code that fetches the video and the 11 reference
intervals `evaluate.py` scores against. To score other CrossTask videos, point
`evaluate.py --annotations` at a
[CrossTask release](https://www.di.ens.fr/~dzhukov/crosstask/crosstask_release.zip).
