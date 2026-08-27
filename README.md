# Manual Generation Agent — End-to-End Example

A realistic, end-to-end walkthrough of running the **Manual Generation Agent (MGA)**
on the Archetype AI Platform (Dev): feed it a procedure video, get back an ordered,
timestamped manual, and score it against ground truth.

The scenario: a 2:53 roadside tire-change video with **11 documented reference
steps**. A developer wants a manual good enough that someone could follow it, and
wants to know how close the agent gets.

MGA V1 is zero-shot. The design doc states there are no build-time optimization
steps, and the platform agrees: the `mga` blueprint pins its own models
(`newton-fusion:1.0` and `whisper:large-v3`), so unlike the
[RED](https://github.com/archetypeai/rare-event-detection-agent-example) and
[OSM](https://github.com/archetypeai/operational-state-monitoring-agent-example)
examples there is **no classifier to fit and no `artifacts` map to pass**. That
removes four of their seven stages — everything between raw data and a fitted
artifact — and moves the difficulty to two other places: choosing a video the agent
can handle, and configuring generation.

```
video ─► sample frames ─┐
                        ├─► newton-fusion f1-0 ─► parse ─► steps + timestamps
        whisper ASR ────┘
```

## Quick start — try the agent in one command

Nothing to build and nothing to fit — **no `atai_core`, no GPU, no AWS**. You do
need two command-line tools and a dev API key:

```sh
brew install yt-dlp ffmpeg     # or: apt install yt-dlp ffmpeg
cp .env.example .env           # add ATAI_API_KEY and ATAI_API_ENDPOINT

python3 run_mga_example.py
```

It fetches the video, uploads it, creates a bundle, runs the agent, streams the
audit log, downloads the manual and prints it. **~15 minutes.**

**`ffmpeg` is not optional.** The sample clip's best format is separate video and
audio streams, and yt-dlp needs ffmpeg to mux them. Without it yt-dlp downloads
both fragments, skips the merge and **still exits 0**, so the failure used to
surface much later as `FileNotFoundError: data/clips/tire_*.mp4`. The script now
checks for both tools before it starts. (Already have the clip? Point `--video` at
it and neither tool is needed.)

**What you get:** a 20-step manual covering 14–138 s of the 173 s clip, each step
carrying a start and end timestamp you can trace back to the video, plus a content digest
that should match `a0742942…` exactly — generation is deterministic at
temperature 0, so a matching digest means you reproduced the reference run.

The clip's first 14 s are a music intro and its last 35 s are Toyota branding, so
14–138 s is the whole procedure, not a truncation. **A digest that differs does not
mean you did anything wrong.** The canonical `mga` blueprint is republished under
this example every few days and the output moves with it — the digest documented
here before this one stopped reproducing within a week, on an unchanged prompt.

Two settings decide whether that works, and both have silent failure modes:

| | send | if you get it wrong |
|---|---|---|
| `max_new_tokens` | **16384** (the blueprint default) | below ~16k the run returns an EMPTY manual, reported as success |
| `prompt` | say what to COVER, never how to format | a format-specifying prompt makes the parser return zero steps |

Why those numbers, what the instruction is worth, and how the same clip scored on
five blueprint versions: [**Evidence: what the configuration is worth**](#evidence-what-the-configuration-is-worth),
or check it offline with no API key via `python3 evaluate.py --compare reference/*.json`.

## The end-to-end example, stage by stage

```
1 select ─> 2 fetch ─> 3 configure ─> 4 run ─> 5 evaluate
(is the       (video +   (blueprint,    (the      (offline,
 video        reference   values,        platform)  no key)
 suitable?)   steps)      prompt)
                              ▲
                    every quality decision lives here
```

Five stages, and only one of them needs the platform. RED and OSM spend most of
their work producing a fitted classifier; MGA is zero-shot, so there is nothing to
fit, no grid to search and no artifact to upload. What replaces that work is
**configuration** — and the recurring lesson here is that the configuration you
need has twice been configuration the blueprint did not expose:

| when | the control out of reach | consequence | now |
|---|---|---|---|
| 2026-08-07 | `max_new_tokens` (hardcoded 256) | manual truncated at 6 steps, cut mid-clause | **fixed** — settable, default 16384 |
| 2026-08-11 | `prompt` (hardcoded instruction) | capped at 10 steps, 3.2 actions bundled into each | **fixed** 2026-08-12 — PLDEV-1730 |

Both are fixed today. The lesson is not either individual defect but that it
happened twice in five days, silently both times — a value the blueprint does not
wire is accepted with HTTP 201 and echoed straight back at you.

So read Stage 3 as the general case rather than a bug report about one value: the
platform accepts any `values` you send and silently ignores the ones the blueprint
does not wire, so **the first thing worth doing on any run is checking which of your
settings are real.** The script does that before it spends anything, and on the
current blueprint it reports everything honoured:

```
blueprint blp_6va7xxnrrn9fr8ybhgz3v3zvw7 (key=mga, active=True)
  honoured: ['max_frames', 'max_new_tokens', 'prompt']
```

When that stops being true it names the value instead, which is the whole point of
running the check on every run rather than trusting this README:

```
WARNING: ['prompt'] is not wired on this blueprint and will be silently ignored.
```

Each stage runs on its own, and **only stage 4 touches the platform** — everything
else, including the whole evaluation, works offline with no API key.

| Stage | How to run it | Needs |
|---|---|---|
| 1 Select a video | `ffprobe` for duration and an audio track; see the checks below | nothing |
| 2 Fetch | `run_mga_example.py --download-only` | nothing (yt-dlp + ffmpeg) |
| 3 Configure | `run_mga_example.py --dry-run` | nothing |
| 4 Run | `run_mga_example.py` | dev API key |
| 5 Evaluate | `evaluate.py --compare reference/*.json` | nothing |

## Evidence: what the configuration is worth

Skip this if you just want a manual — the quick start above is enough. This is
the measurement behind the two numbers it tells you to send.

The same 173 s video across five blueprint versions. **E is what you get today**;
A–D are kept because they are the evidence for what the instruction is worth:

|  | A — capped | B — A + tokens | C — B + prompt | D — prompt hardcoded | **E — current** |
|---|---|---|---|---|---|
| blueprint | `blp_0zmce3…` | `blp_76kyqm…` | `blp_76kyqm…` | `blp_5wtsdwmp…` | **`blp_6va7xx…`** |
| caller's prompt honoured | n/a | n/a | **yes** | no | **yes** |
| `max_new_tokens` | 256 (fixed) | 2048 | 2048 | 4096 | **16384** |
| steps emitted | 6 | 10 | 19 | 10 | 18 |
| coverage | 16–85 s | 16–120 s | 11–139 s | 11–135 s | 11–139 s |
| ref steps found (any overlap) | 4/11 | 10/11 | 10/11 | 11/11 | 11/11 |
| … at IoU ≥ 0.3 | 4/11 | 8/11 | 9/11 | 10/11 | 9/11 |
| … at IoU ≥ 0.5 | 3/11 | 4/11 | 5/11 | 4/11 | 7/11 |
| actions bundled per step | 2.2 avg, 5 max | 2.8 avg, 5 max | 1.6 avg, 4 max | 3.2 avg, 7 max | 1.7 avg, 4 max |
| cautions as their own step | 0 | 0 | 3 | 0 | 1 |
| content md5 | `275677ab…` | `577c21eb…` | `66d86cf4…` | `06b7b258…` | `caaea09f…` |

Every number above is computed from the committed outputs, so `python3 evaluate.py
--compare reference/*.json` reproduces the whole table offline.

**A–E are history, not a status board.** Each column is the blueprint that was
canonical on its day, and none of them reproduces now. Column E in particular is
no longer what this example produces — see
[What replaced it](#what-replaced-it-2026-08-20) for the runs that are current.

**Read it twice, because the metric and the manual disagree.** D scores best on
IoU ≥ 0.3 — 10/11, better than E — and is the worst manual of the five.
Loose-threshold recall rewards fewer, longer steps: MGA tiles the timeline
contiguously, so a 10-step manual has longer steps that overlap more of any
reference interval. Tighten the threshold and the ranking inverts — at IoU ≥ 0.5
D finds 4/11 and E finds 7/11.

What recall cannot see at all is that D bundles **3.2 actions into the average
step and 7 into its worst**, against 1.7 and 4 for E. An operator cannot tick off
"apply the parking brake" separately from "switch off the ignition", and each step
gets one frame thumbnail however many actions it contains. If you take one thing
from this example, take that: **evaluate a manual by whether it can be followed,
not only by temporal recall.**

**The cautions are the honest caveat.** A caller-supplied prompt is what lets a
spoken-only instruction become its own step at all — D, with the instruction
hardcoded, emits none. But restoring `prompt` did not restore all of them: C found
three, E finds one.

```
C:  "Never put any object or body part under the vehicle when using the jack."
    "Do not touch the disc wheels or brakes as they can be hot."
    "Do not exceed 80 kilometers per hour when driving with a compact spare wheel."
E:  "Do not exceed 80 kilometers per hour when driving with a compact spare wheel."
```

C and E differ in more than the prompt — different blueprint, different model
build, 2048 against 16384 tokens — so this is not a controlled comparison and the
two missing cautions are not attributable to any one of those. Worth a run to
pin down if safety content matters to your use case.

D is what a hardcoded instruction costs: fixed at *"Generate a concise, ordered
list of distinct steps with 10 steps or less."*, it was capped at 10 steps by
wording rather than by content — full write-up in
[`reference/COMPARISON-prompt-honoured-vs-hardcoded.md`](reference/COMPARISON-prompt-honoured-vs-hardcoded.md).
`prompt` became settable again on 2026-08-12 (**PLDEV-1730**), and E is the
result: 18 steps at 1.7 actions each, and 7 of 11 reference steps localised
within IoU ≥ 0.5 against D's 4.

### What replaced it (2026-08-20)

E's instruction was the example's default for a week. It broke on the second clip
anyone tried.

On an 80 s **silent** mouse-assembly video it returned 16 steps built from two
distinct sentences — `"Take out the battery."` fifteen times in a row, on a wired
USB mouse that has no battery in it. Two clauses did it:

| clause | what it did |
|---|---|
| "concise" + "at most 15 words" | drove a 4-word median; a cap that low is a target, not a limit |
| *(nothing)* | no clause required consecutive steps to differ, so at `temperature: 0` with `repetition_penalty: 1.0` the decoder re-emitted its own most-likely sentence |
| "use both what is shown and what is said" | the clip has no audio stream at all, so this asks for a track that does not exist |

The replacement drops the word cap and the step ceiling, adds an enumerated list
of the *kinds* of detail wanted, adds an explicit distinctness rule, and makes the
picture the fallback when there is no narration. The full text is `DEFAULT_PROMPT`
in `run_mga_example.py`, with a clause-by-clause rationale above it.

**Measured, same clip and config, only the prompt changed:**

| | previous prompt | current prompt |
|---|---|---|
| **mouse_assembly.mp4** (80 s, silent) | 16 steps, **2 unique**, 14 consecutive repeats | 16 steps, **16 unique**, 0 repeats |
| **tire_i2JWkDyg26A.mp4** (173 s, narrated) | 21 steps, 0–111 s | 20 steps, **14–138 s** |
| ref steps found (any overlap) | 8/11 | **9/11** |
| … at IoU ≥ 0.5 | 1/11 | **4/11** |
| content md5 | `8b30b0ad…` | `a0742942…` |

Both tire runs were made on 2026-08-20 against `blp_3e0hmpqfgc93wvncmr8m30eeny`,
so **that pair is controlled** — same blueprint, same `max_frames`, same
`max_new_tokens`, prompt the only difference. All four outputs are committed under
`reference/…-20260820-…json`.

**Do not compare either against E.** E is an older blueprint. Re-running E's exact
prompt today gives 21 steps over 0–111 s, not 18 over 11–139 s — so the drop from
E's 11/11 to today's 9/11 is the platform moving, not the prompt. Getting this
wrong once cost an afternoon: the first read of these numbers scored the new
prompt against E, concluded it was a regression, and was wrong.

**What the new prompt did NOT fix**, stated plainly so nobody re-derives it:

- **Zero grid terms on either clip.** The clause naming top-left/upper-center and
  friends produced nothing, twice. On this evidence it is inert against this model.
- **No on-screen text was ever quoted.** It cannot be: `size` defaults to 224, so
  every frame is squashed to 224×224 before the model sees it and label text is
  gone. Raising `size` is the lever, not wording — untested here.
- **The mouse manual is still largely invented.** It opens with a six-step teardown
  that is not in the footage and ends by plugging the mouse into a computer, which
  also never happens. Fixing the loop did not make it grounded.
- **Neither of today's runs finds `unscrew wheel` / `withdraw wheel`** (89–94 s).
  Both spend that window on a spoken caution instead. The previous prompt misses it
  too, so this is not a regression — but the manual is missing a real step, and
  "take the flat tire off" is not an optional part of changing a tire.

⚠️ **`max_new_tokens` is a floor, not a ceiling.** The model reasons before it
answers out of the same budget, so below the threshold it never reaches an answer
and you get `job.completed`, no error row, `results: []`. On this video:

| 2048 | 4096 | **16384** | 32768 | 65536 |
|---|---|---|---|---|
| 0 steps | 0 steps | **18 steps** | 18 steps | 18 steps |

The last three are byte-identical (generation is deterministic at temperature 0),
so the threshold is between 4k and 16k and nothing above it helps. Lowering this
does not shorten the manual — it empties it, silently.

Measured 2026-08-13 on the previous prompt and the blueprint canonical that day,
so read the **threshold**, not the step counts — today's default returns 20 steps
at 16384. The sweep has not been repeated since the prompt changed.

Run A is confirmed across **three runs on two machines**, and E across **three
budgets** (16384/32768/65536) — byte-identical every time, because generation is
deterministic at `temperature: 0.0`. A digest that differs means the input or the
configuration differed, not that the model wandered.

Careful with recall: it means *some non-advisory prediction overlaps the reference
interval*. MGA tiles the timeline with no gaps, so every interval necessarily
overlaps something — an early draft of this table claimed 11/11 for C because a
purely temporal match credited *"Do not touch the disc wheels or brakes"* as the
match for `unscrew wheel`. [`evaluate.py`](evaluate.py) excludes advisories and
reports IoU ≥ 0.3 / 0.5 alongside, because bare overlap is the most generous reading
available.

All outputs are committed under [`reference/`](reference), so the comparison is
checkable offline with no API key:

```sh
python3 evaluate.py --compare reference/*.json
```

### The narration ablation (2026-08-27)

The prompt work above left one question open: how much of a good manual comes from
the picture, and how much from the transcript? The tire clip answers it, because it
can be run twice with only the audio track differing.

`ffmpeg -i tire_i2JWkDyg26A.mp4 -an -c:v copy tire_i2JWkDyg26A-silent.mp4` strips
the audio without touching a frame — **the two files' video bitstreams are
byte-identical**, `0f1ca9b272bf15676c2cdb39012a893a` for both, so nothing else can
account for a difference. Same prompt, same `max_frames`, same `max_new_tokens`,
same blueprint, minutes apart. Both digests have been reproduced independently on
a second machine and a later day.

| | [**with audio**](reference/mga-output-i2JWkDyg26A-20260820-AUDIO-20steps.json) (`a0742942…`) | [**silent**](reference/mga-output-i2JWkDyg26A-20260827-SILENT-16steps.json) (`26d1fbc2…`) |
|---|---|---|
| steps | 20 | 16 |
| coverage | 14–138 s | 0–128 s |
| distinct step durations | 7 | **1 — every step exactly 8.0 s** |
| hubcap step (visible at 47 s) | ✅ | ✗ |
| "80 km/h", "Toyota repairer", "check the nuts after a couple of hundred km" | ✅ | ✗ |
| both spoken safety cautions | ✅ | ✗ |
| vocabulary | the video's — "wheel nuts", "wrench", "in sequence" | generic — "lug nuts", "lug wrench", "**star pattern**" (never said in this video) |
| invented content | — | "check the tire pressure of the spare", "drive to the nearest service station" |

**Removing narration does not degrade the manual. It changes what the manual is.**
Read the silent run as a *procedure* and it functions: all 16 steps are real steps
of a tire change, correctly ordered, at a sensible granularity, and it includes
taking the flat wheel off — which the with-audio run misses. Someone could follow
it. Judge it on that and it is fine.

Read it as a record *of this video* and it is not. Its steps are displaced 20–50 s
from what the footage shows at that time: it puts "loosen the lug nuts" at 24–32 s,
where the video is opening the trunk, and "jack up" at 32–40 s, where the video is
prying off a hubcap it never mentions at all. That distinction is not academic here,
because every step carries `frame_start`/`frame_end` and the downstream app renders
per-step thumbnails from them — so the prose reads correctly while the illustration
beside it shows a different action, with nothing to signal the mismatch.

**Both manuals are committed, so you can compare them without a GPU** — they are the
only two files in `reference/` that differ in exactly one input:

- [`mga-output-i2JWkDyg26A-20260820-AUDIO-20steps.json`](reference/mga-output-i2JWkDyg26A-20260820-AUDIO-20steps.json) — the narrated run. Also the `KNOWN_MD5` reference.
- [`mga-output-i2JWkDyg26A-20260827-SILENT-16steps.json`](reference/mga-output-i2JWkDyg26A-20260827-SILENT-16steps.json) — the same footage with the audio track stripped.

```sh
# side by side, scored against the CrossTask reference procedure
python3 evaluate.py --compare \
  reference/mga-output-i2JWkDyg26A-20260820-AUDIO-20steps.json \
  reference/mga-output-i2JWkDyg26A-20260827-SILENT-16steps.json

# read either one as a manual; the silent one trips the uniform-duration warning
python3 run_mga_example.py --show reference/mga-output-i2JWkDyg26A-20260827-SILENT-16steps.json
```

To regenerate them, strip the audio and run the example twice with no other flags —
the defaults are what both runs used:

```sh
ffmpeg -i data/clips/tire_i2JWkDyg26A.mp4 -an -c:v copy data/clips/tire_i2JWkDyg26A-silent.mp4
python3 run_mga_example.py --video data/clips/tire_i2JWkDyg26A.mp4        --output data/tire.json
python3 run_mga_example.py --video data/clips/tire_i2JWkDyg26A-silent.mp4 --output data/tire-silent.json
```

(Outputs land in `data/`, which is gitignored — the curated copies under
`reference/` are the tracked evidence.)

**Step boundaries appear to come from the transcript.** Across every run measured:

| clip | audio? | steps | distinct step durations |
|---|---|---|---|
| tire, new prompt | yes | 20 | 7 |
| tire, previous prompt | yes | 21 | 6 |
| tire, silent | **no** | 16 | **1** (8.0 s) |
| mouse_assembly, new prompt | **no** | 16 | **1** (5.0 s) |
| mouse_assembly, previous prompt | **no** | 16 | **1** (5.0 s) |

Perfect separation. With no audio there is nothing to segment on, so the timeline is
divided `duration / n_steps` and the timestamps carry no information. That is cheap
to detect, so `run_mga_example.py` now warns when every step has the same length. It
also explains the mouse clip's manual, which is invented in the same way for the
same reason — that clip has no audio stream at all.

**This is why the tire manual is good and the mouse manual is not.** The pipeline's
strength is turning narration into ordered, timestamped steps. On a silent clip
there is no fallback to the picture; there is a fallback to the model's prior.

**And that is why the silent tire run is a trap rather than a reassurance.** It
looks serviceable because changing a tire is in every model's training data, so the
prior it fell back on happens to be right. The mouse clip is the same configuration
with a prior that is not, and it produces a teardown that never happens and a
computer that never appears. The procedures worth generating manuals for are the
ones the model has never seen — a customer's own line, a specific machine — where
there is no correct prior to fall back on.

The practical consequence: **you cannot tell a memorised manual from an observed one
by reading it.** Both are fluent and plausible. What separates them is whether the
prior happened to match the footage, and the reader has no way to check that from
the text. The uniform step duration is the one cheap signal, which is why it is now
a warning rather than a footnote.

⚠️ **The headline metric prefers the manual that is worse as a record.**
`evaluate.py` scores the silent run **11/11** reference steps against the with-audio
run's 9/11. Its matches are
nonsense — *jack up* ↔ "tighten the lug nuts in a star pattern", *unscrew wheel* ↔
"fully lower the vehicle and remove the jack" — because uniform contiguous tiling
guarantees every reference interval overlaps something. Temporal recall rewards
blanketing the timeline, not describing it correctly. Read it alongside the step
text and the duration spread, never alone.

One more asymmetry worth keeping. The silent run contains "fully remove the lug
nuts" and "pull the flat tire off the hub" — the step the with-audio run **misses**.
It sits at 40–56 s, where the footage shows the hubcap and the nuts being loosened.
Right by memorised procedure, wrong by clock. The with-audio run misses it because
it is the step a narrator is least likely to announce, being the one thing that is
obvious on screen.

## Prerequisites

Notably short compared with RED and OSM: **no `atai_core` checkout, no Omega
checkpoint, no GPU, no AWS access.** MGA is zero-shot and the blueprint pins its own
models, so there is nothing to fit and nothing to upload.

- `.env` in the repo root:

  ```
  ATAI_API_KEY=<dev API key>
  ATAI_API_ENDPOINT=https://api.dev.u1.archetypeai.app
  ```

- **Python 3** — verified on 3.14. Every script here is **stdlib-only**: no
  virtualenv, no install step, no `requirements.txt`.
- **`yt-dlp`** to fetch the video, since CrossTask distributes YouTube IDs rather
  than media. Optional: the scripts print the URL and target path if it is missing.
- **`ffmpeg`** — **required if you let `yt-dlp` fetch the clip.** The chosen format
  is separate video and audio streams, and yt-dlp needs ffmpeg to mux them. Without
  it yt-dlp downloads both fragments, skips the merge, and **still exits 0**,
  leaving `tire_*.f396.mp4` and `tire_*.f140.m4a` but no `tire_*.mp4`. The script
  checks for it up front and says so; this line used to call ffmpeg optional, and
  someone lost time to exactly that.

  ```sh
  brew install ffmpeg   # or: apt install ffmpeg
  ```

## Stage 1 — Select a video

The first real decision, and the one that wasted the most time here. **MGA fails
outright on videos longer than about 5 minutes.** A 563 s video at default settings
ran 5 min 13 s of processing and then died:

```
ERROR  pipeline execution failed: Newton generation event stream closed
       without a terminal event
ERROR  pod.terminated  exit_code=1
```

Note this is *not* a context limit — at `max_frames: 16` the video occupies 392
visual tokens, **0.15% of F1-0's 256K window**.

So a video needs three properties, none of which the platform checks for you:

| Requirement | Why |
|---|---|
| **Under ~5 minutes** | longer runs fail outright, ~5 min in |
| **Has an audio track** | `AudioReaderNode` → whisper → `prompt.audio` is half the pipeline; run C's safety notes came from narration alone |
| **Reference steps, if you want to score it** | otherwise there is nothing to evaluate against |

Filtering CrossTask on duration matters more than it sounds. **At least 26% of its
annotated videos exceed the 5-minute ceiling** by annotated span alone — a lower
bound, since 72% of each video is background. Per task it ranges from 8%
("Make Irish Coffee") to 47% ("Add Oil to Your Car").

This example uses **CrossTask task 40567 "Change a Tire", video
[`i2JWkDyg26A`](https://www.youtube.com/watch?v=i2JWkDyg26A)** — 173.2 s (58% of the
ceiling), 640×360, 25 fps, 4,329 frames, with all 11 reference steps annotated across
28 segments by two independent annotators. It has the densest annotation of the 99
videos in that task.

Source resolution barely matters: the pipeline resizes every frame to 224×224, so
360p is no worse than 1080p, only smaller.

**Scope: one video, by design.** This example demonstrates the mechanics and
characterises a configuration defect; it is not a benchmark. Every figure below is a
single observation. The 4/11 → 10/11 result is large and has a mechanical
explanation, so it generalises with some confidence; the IoU figures are much more
video-specific and should not be quoted as MGA's accuracy. If a benchmark is ever
wanted, `INMUrGdA_RA` (201 s), `0kfJ7Lu4tvo` (222 s) and `Z63ZcJXc29E` (188 s) are
sub-5-minute videos of the same task with all 11 steps annotated, at ~15 min of GPU
each.

## Stage 2 — Fetch the video and reference annotations

```sh
python3 run_mga_example.py --download-only
#   fetching the video (5.7 MB) -> data/clips/tire_i2JWkDyg26A.mp4 ...
#   video md5 5771f46d637314fdad86d6c4c934dbb5  (matches the reference input)
```

No API calls, so this works before anyone has a key. A bare run fetches it
automatically when missing; `--video <path>` uses a copy you already have.

The input digest is checked because an **output** digest only means something if the
input bytes match. yt-dlp has been reproducible here (formats 396+140, 5,678,742
bytes) across two machines, but format selection can drift as YouTube changes what
it offers — if the video md5 differs the scripts say so, and note that the output
digest legitimately will not match.

Reference annotations need no download: the 11 intervals are embedded in
`evaluate.py` and `repro_truncation.py`. For other videos, point `--annotations` at a
[CrossTask release](https://www.di.ens.fr/~dzhukov/crosstask/crosstask_release.zip);
its format is `<step>,<start_sec>,<end_sec>` per video.

**Videos are never committed.** `data/` is gitignored, with `*.mp4`/`*.mkv`/`*.webm`
as a backstop. Each video is copyrighted by its uploader.

## Stage 3 — Configure the agent

This is where MGA's four missing stages went, and where the whole example turns.
Three values matter; what is broken about them has changed twice.

```sh
python3 run_mga_example.py --dry-run     # print the exact payload, call nothing
```

| Value | This example sends | Honoured today? | Notes |
|---|---|---|---|
| `max_frames` | **64** | ✅ | Blueprint default is 16. 64 is the reader/preprocessor batch size, so frames land in one batch. Changes the wording but **not** the outcome — see 3d. |
| `max_new_tokens` | **16384** | ✅ | Was the first defect (hardcoded **256**, manual truncated). Now settable and the blueprint's own default. It is a **floor**: the model reasons out of this budget, so 2048 and 4096 both return an EMPTY manual on this video while 16384/32768/65536 all returned the same manual. (Step counts from that sweep are 2026-08-13 figures on the previous prompt; the threshold is the durable claim and has not been re-measured since.) |
| `size` | not sent (224) | ✅ | Every frame is resized to `size × size` before the model sees it. `null` keeps the source resolution. Untested here; the obvious lever if on-screen text is unreadable. |
| `prompt` | a detail-oriented instruction | ✅ | Was the second defect: hardcoded to "10 steps or less" between 2026-08-11 and 08-12, so a caller's instruction was accepted and discarded. Wired again as `${values.prompt}` (**PLDEV-1730**). Say what to COVER, never how to format — the results parser owns the template. Rewritten 2026-08-20 after the previous instruction degenerated on a second clip — see [What replaced it](#what-replaced-it-2026-08-20). |

**Both defects are fixed; the pattern is the point.** Twice in five days the
setting that decided whether the manual was usable was one the blueprint did not
expose, and both times the failure was silent — HTTP 201, the value echoed back,
no effect. The sections below document each for the record, and the preflight in
the script is the durable lesson: **check which of your values are actually wired
before spending a run.**

### 3a. The token cap was the whole story  ⏮ HISTORIC — fixed upstream

**The cap is `max_new_tokens: 256`.** Not inferred — it is the documented default
of a superseded version of this blueprint, annotated there as
`# Maximum number of tokens the model may generate in the response. ..32768`.
The active blueprint no longer exposes the value, so 256 is now effectively
hardcoded.

The output-side evidence is punctuation: in run A, steps 0–4 all end in a full
stop and step 5 ends `…and raise the car` with none. Two independent generations
(`max_frames` 64 and 32) produced **the same 83 words** with different wording,
each cut mid-clause.

**It cannot be raised from a bundle on the active blueprint.** Three runs across
two machines, with and without `max_new_tokens: 1024`, all produced byte-identical
output — 1146 bytes, md5 `080e551a88f0bdcd857e48557366ae60`. The reason is visible
in the blueprint document, whose entire fusion config is:

```yaml
fusion:
  key: NewtonFusionModelNode
  config:
    model: ${models.fusion}     # ← that is the whole config
```

Nothing references `${values.max_new_tokens}`, so a value by that name is stored on
the bundle and connected to nothing. Both workarounds fail *silently*:
`values: {max_new_tokens: …}` returns 201 and is echoed back with no effect, and
`nodes: {fusion: {config: …}}` returns 201 with the `nodes` key absent from the
response entirely.

### This was a regression, not a missing feature  ⏮ HISTORIC

Four `mga` blueprints exist. Exactly one exposed the generation parameters, for
about **93 minutes** on 7 August:

| blueprint_id | created | active | generation params |
|---|---|---|---|
| `blp_7mtphg…` | 2026-08-06 11:55 | false | 0 |
| `blp_05vwf8…` | 2026-08-07 15:12 | false | 0 |
| **`blp_76kyqm…`** | **2026-08-07 17:59** | false | **8** |
| **`blp_0zmce3…`** | **2026-08-07 19:32** | **true** | 0 |

`blp_76kyqm…` wires eight of them straight into the fusion node —
`max_new_tokens`, `enable_thinking`, `temperature`, `top_k`, `top_p`,
`repetition_penalty`, `seed`, `stop` — and takes its instruction from a value too:
`default_text: ${values.prompt}`. Its `temperature: 0.0` default also explains the
determinism above; that YAML notes `0.0` "decodes greedily and makes every
sampling knob below inert".

The two MGA bundles visible in the dev console were both created inside that
93-minute window and carry real settings — `bnd_6ks6k…` has `max_new_tokens: 1024`
and a full custom prompt, `bnd_3ve9…` asks for "25 steps or less".

**So the question for the algorithm author is not whether these can exist, but why
eight generation parameters and the instruction were dropped from the active `mga`
blueprint, and whether they can be restored.**

### Two platform notes that made this easy to miss

- **`GET /agents/blueprints` only returns `is_active: true` blueprints.** Surveying
  "all 35 node config keys across all 8 blueprints" therefore missed the one
  version that had them.
- **Blueprints are soft-deprecated, not deleted.** The superseded version still
  resolves by `blueprint_id`, still accepts new bundles, and still runs. Only the
  *key* `mga` resolves to the active version. Bundles pin `blueprint_id`, so an
  existing bundle keeps working when a new version becomes active.

### 3b. What the model does when the cap is lifted  ⏮ HISTORIC — needs `prompt`

`blueprints/mga-tuned.yaml` proposed restoring these values on the active
blueprint. That has since happened — both `max_new_tokens` and `prompt` are wired
on the canonical blueprint — so this rung is reproducible with the plain default
target and no id pinning:

```sh
python3 run_mga_example.py --max-new-tokens 16384
```

The id this section used to name, `blp_76kyqm4vjp9pt8tvfz8tks7x6t`, is now
**unrunnable**: it wires sampling parameters into its own fusion node that the
node schema no longer accepts, so a run against it returns `202` and then dies at
resolution. Do not use it for diagnosis or anything else.

#### Run B — raising the budget alone

`max_new_tokens: 2048`, **everything else identical**: same video, `max_frames: 64`,
`size: 224`, `temperature: 0.0`, and the same instruction, because that
blueprint's default `prompt` value is character-for-character the string the
active blueprint hardcodes.

Result: **6 steps → 10, and 4 of 11 reference steps → 10 of 11.** Every step
terminates cleanly, so the truncation signature is gone. The steps it recovered are
exactly the ones A missed:

```
[ 85.0 -  95.0s]  Remove the wheel nuts and take off the flat tire.
[ 95.0 - 105.0s]  Lift the spare wheel onto the hub, line up the holes, and replace the wheel nuts…
[105.0 - 112.0s]  Lower the jack until the spare wheel touches the ground, then tighten…
[112.0 - 120.0s]  Lower the car completely, remove the jack, and store the tools and flat tire.
```

**But the binding constraint moved to the prompt.** It emitted exactly **10**
steps — the ceiling in the instruction ("10 steps or less"), not the token budget.
Only reference step 11 (`put things back`, 120.9–125.6 s) stayed missed, and only
just: the last prediction ends at 120.0 s.

#### Run C — budget plus a coverage-oriented instruction

Same as B, plus `--prompt`. This was the example's default instruction until
2026-08-20; it is quoted here as what Run C sent, not as what the example sends
today — see [What replaced it](#what-replaced-it-2026-08-20):

> Generate a concise, ordered list of every distinct step performed in this video,
> covering the procedure from the first action to the last. Include brief steps and
> steps that are repeated. Use up to 20 steps. Keep each step to at most 15 words.
> Use both what is shown and what is said. Do not invent steps that are not shown
> or stated in the video.

Each clause targets a measured failure:

| clause | the failure it addresses |
|---|---|
| "from the first action to the last" | A stopped at 85 s of 173 s |
| "include brief steps and steps that are repeated" | 5 of A's 7 misses were 2.6–4.6 s; `jack down` and `tight wheel` each recur |
| "up to 20 steps" | 11 reference steps cannot fit in "10 or less" — B proved this binds |
| "at most 15 words" | at `max_frames=32`, richer prose bought *less* coverage |
| "what is shown **and what is said**" | whisper feeds `prompt.audio` |

Deliberately **no output-format instruction**, and this turns out to be the whole
ballgame. `ManualGenerationPromptGeneratorNode` owns the template that
`ManualGenerationResultsParserNode` parses, so a prompt specifying its own format
makes the parser **return zero steps**.

That is why `prompt` was exposed and then rolled back: it was tested, found fragile
for exactly this reason, and reverted. Our results refine the picture rather than
contradict it — a prompt that says *what to cover* parses cleanly (this one produced
19 well-formed steps with frames and timestamps), while one that says *how to lay
the output out* (markdown headings) does not.

Two ways to expose it safely, if that is ever wanted:

- Have the prompt generator append the format requirement **after** the user
  instruction, so a user prompt cannot override it.
- Make a non-conforming response fail loudly rather than yield zero steps —
  `parser_compute_stats` already computes template conformance, so the signal exists.

**`max_new_tokens` carries none of this risk.** It is an integer that changes
generation length and cannot change output format; run B raised it with the default
prompt untouched and the parser was perfectly happy. The `prompt` fragility is not a
reason to withhold the budget.

Result: **19 steps, 11–139 s, 10 of 11 reference steps covered.** It emitted 19
against a ceiling of 20, so it stopped on content rather than configuration, and it
started *earlier* than A and B, catching "stop the car safely on a firm level
surface" that both missed.

**The audio path is confirmed working.** C surfaced spoken guidance that is not
visible action at all — *"Never put any object or body part under the vehicle when
using the jack"*, *"Do not touch the disc wheels or brakes as they can be hot"*,
*"Do not exceed 80 kilometers per hour when driving with a compact spare wheel"*.

### 3c. Reading the output as a manual

**The video contains 11 reference steps; C emitted 19.** That is not
over-generation in the sense of error. Breaking the 19 down:

| category | count |
|---|---|
| Map to a reference step | 14 |
| Spoken cautions | 4 |
| Action with no reference equivalent | 1 |

Two things to understand before treating "19 vs 11" as a precision penalty.

**CrossTask's step list is a reference procedure, not an inventory.** It is
declared at the *task* level and derived from a wikiHow article:

```
40567 / Change a Tire / http://www.wikihow.com/Change-a-Tire / 11
brake on, get things out, start loose, jack up, unscrew wheel, withdraw wheel,
put wheel, screw wheel, jack down, tight wheel, put things back
```

Annotators then mark where each of those 11 occurs. Anything wikiHow omits is
absent **by construction** — so "set up a warning triangle", "remove the hubcap",
"switch off the ignition" and every spoken caution could not appear even if they
are plainly in the video. Across CrossTask, ~31% of a task's steps aren't depicted
in a given video; this one happens to contain all 11.

**And the contiguous-tiling property forces the cautions to become steps.** Because
the output partitions the timeline with no gaps, a caution spoken during a pause in
the action has nowhere else to go. It is a schema limitation — there is no
`kind: action | caution` field — rather than a generation defect.

Read as a manual, C is good, and arguably more useful than the 11-step reference:
the cautions land exactly where a technical writer would put them, immediately
before the step they apply to. If a strict action-only list is wanted, the
instruction needs to say so — *"List only actions performed; do not list warnings
or advice as steps"* — which is untested.

## Stage 4 — Create the agent and run it

```sh
python3 run_mga_example.py
```

Upload → create a bundle → run → stream `/logs` → download → print. **~15 minutes**:
about 7.5 min of model download and load on *every* run (nothing is cached between
runs), then roughly 2.5× realtime processing.
**Run one at a time.** Dev looked like it serialised jobs — four submissions once
produced ~800 s queue waits — but three concurrent runs on 2026-08-13 came up as
three concurrent pods and one was SIGKILLed mid-load (`exit=137`).

> ### The defaults target the `mga` KEY — and why that matters
>
> `run_mga_example.py` sends `"blueprint": "mga"`, not an id. A key resolves to
> whatever is canonical and active; a pinned id does not survive republication, and
> `mga` was republished three times in the week of 2026-08-11.
>
> A superseded id fails **late and quietly**: reading it works, the bundle returns
> `201 ready`, `/run` returns `202 running`, and then the pod dies one second in
> with `resolving blueprint: invalid config for 1 node(s)`. This example used to
> default to such an id — `blp_76kyqm…`, pinned to reach `max_new_tokens` and
> `prompt` before they were exposed — and every run failed until it was switched
> back to the key.
>
> Pin an id only to reproduce one specific past run, and expect it to stop working:
>
> ```sh
> python3 run_mga_example.py --blueprint <blueprint_id>
> ```
>
> The script prints a warning when the target is `is_active: false`. Superseded
> versions stay *readable* long after they stop being *runnable*, so reading one
> back successfully tells you nothing about whether a run against it will resolve.
>
> `--show` recognises the reference digests and tells you which run you are looking
> at, including the empty one.

The same five calls in raw HTTP, for integrating without the Python — sending what
the script sends, including the `prompt`. Each call has a way to get it wrong; all
of them are explained in
[Platform behavior worth knowing](#platform-behavior-worth-knowing):

```sh
# 1. upload            (declare video/mp4 — the DECLARED type is enforced)
#    Upload under a UNIQUE name: file_id is the filename, and it is a mutable
#    pointer shared across your org. Re-using a name orphans the previous
#    object and kills whichever run is still holding it.
curl -X POST -H "Authorization: Bearer $ATAI_API_KEY" \
  -F "file=@clip.mp4;filename=clip-20260813T154418Z-410f.mp4;type=video/mp4" \
  "$ATAI_API_ENDPOINT/v0.5/files"

# 2. bundle            (/bundles is PLURAL; no artifacts map for mga)
#    SEND A PROMPT. Omit it and the blueprint's own instruction applies —
#    "…with 10 steps or less" — which halves the manual. Say what to COVER,
#    never how to format: the parser owns the output template.
curl -X POST -H "Authorization: Bearer $ATAI_API_KEY" -H "Content-Type: application/json" \
  "$ATAI_API_ENDPOINT/agents/bundles" -d '{
    "blueprint": "mga",
    "values": {
      "max_frames": 64,
      "max_new_tokens": 16384,
      "prompt": "Generate an ordered list of every distinct step performed in this video, in order. Include brief steps and steps that are repeated. Write each step as a direct, actionable instruction, as if telling the reader what to do. Do not narrate or describe what is happening in the video. Capture the details needed to repeat the action exactly: control and part names, printed and on-screen text quoted word for word, part numbers, model and serial numbers, tool types and sizes, quantities, and which way things move. For every component being handled, state where it is using grid terms such as top-left, top-right, bottom-left, bottom-right, center-left, or upper-center. Where several similar actions are performed, such as four screws on one panel, treat each as its own step and say what distinguishes it. Where the video shows how to tell the step worked, say that. Base every step on what is visible; use the spoken narration where there is any, and rely on the picture alone where the video is silent. Each step must describe a different action from the one before it — where two moments look alike, state what changed between them. Do not invent steps, parts, or details that are not shown or stated in the video."
    }
  }'

# 3. run               (returns 202, not 201)
curl -X POST -H "Authorization: Bearer $ATAI_API_KEY" -H "Content-Type: application/json" \
  "$ATAI_API_ENDPOINT/agents/bundles/$BUNDLE_ID/run" -d '{
    "connectors": {"source": [{"type": "file", "id": "<the file_id you uploaded>",
                               "format": "mp4"}]}
  }'

# 4. watch             (/logs, not /events; the status field lies both ways)
curl -H "Authorization: Bearer $ATAI_API_KEY" \
  "$ATAI_API_ENDPOINT/agents/instances/$AGENT_ID/logs?limit=500"

# 5. results           (ref is relative, resolves under /v0.5, needs the token)
curl -H "Authorization: Bearer $ATAI_API_KEY" \
  "$ATAI_API_ENDPOINT/agents/instances/$AGENT_ID/results"
```

### What a run produces

**One JSON document per run** — not JSONL. The platform reports
`file_extension: "json"` and the body parses as a single object:

```json
{"id":"tire_i2JWkDyg26A","results":[
  {"step":0,"instruction":"Stop the car safely on a firm level surface away from traffic.",
   "frame_start":275.0,"frame_end":525.0,
   "timestamp_start":11.0,"timestamp_end":21.0}, …]}
```

Four properties, none of them documented in the blueprint:

- **Timestamps in seconds come free.** The blueprint exposes only
  `parser_output_frame_indices`; `timestamp_start`/`timestamp_end` are emitted
  anyway, so no frames→seconds conversion is needed. `manual-gen-jos` needs a
  second model pass to get `[MM:SS]` reliably — MGA gets it in one.
- **Frame indices are SOURCE frames, not sampled frames.** Max observed index
  3,475 against 4,329 source frames; sampled space would have capped at 63.
  `frame = timestamp × 25 fps` holds exactly on every row of every run.
- **`step` is 0-based.** A 10-step manual reports `step` 0–9. CrossTask reference
  steps are 1-based (1–11), so a scorer that joins them naively is off by one —
  and it produces plausible-looking wrong alignments rather than an error.
- **The output tiles the timeline contiguously.** Every step's `timestamp_end` is
  exactly the next step's `timestamp_start`, on integer-second boundaries, in
  every run recorded here. **MGA performs segmentation, not event detection.** There is no
  "nothing happening here" option, so every span must be labelled — which is why
  a spoken safety caution gets its own step (see
  [Reading the output as a manual](#3c-reading-the-output-as-a-manual)).

## Stage 5 — Evaluate

```sh
python3 evaluate.py data/mga-output.json                        # what you just produced
python3 evaluate.py --compare data/mga-output.json reference/*.json   # against the ladder
```

[`evaluate.py`](evaluate.py) is **offline and stdlib-only** — no API key, no network
— so re-scoring is free and repeatable, which matters because a run happens once but
you will read its numbers many times.

It deliberately **does not compute precision** against CrossTask. The step list is a
per-task, wikiHow-derived reference procedure rather than an inventory of the video,
so real actions it omits would be scored as false positives. Unmatched predictions
are counted and displayed, never scored. See [3c](#3c-reading-the-output-as-a-manual).

### Accuracy of the current run, step by step

| # | Reference step | Interval | Best-matching prediction | Predicted | IoU |
|---|---|---|---|---|---|
| 1 | brake on | 14.8–28.9 | Stop the car safely on a firm level surface awa… | 11.0–21.0 | 0.35 |
| 2 | get things out | 35.7–41.8 | Retrieve the spare wheel and tools from the trunk. | 35.0–46.0 | **0.56** |
| 3 | start loose | 46.5–63.9 | Use the wrench to loosen the wheel nuts one hal… | 50.0–60.0 | **0.57** |
| 4 | jack up | 73.8–88.9 | Place the jack and raise the vehicle until the… | 76.0–85.0 | **0.60** |
| 5 | unscrew wheel | 89.0–91.7 | Remove the wheel nuts and take off the flat tire. | 85.0–95.0 | 0.27 |
| 6 | withdraw wheel | 91.8–94.4 | Remove the wheel nuts and take off the flat tire. | 85.0–95.0 | 0.26 |
| 7 | put wheel | 95.8–99.7 | Lift the spare wheel onto the hub and align the… | 95.0–99.0 | **0.69** |
| 8 | screw wheel | 100.4–104.4 | Replace the wheel nuts and tighten them until t… | 99.0–105.0 | **0.67** |
| 9 | jack down | 104.5–117.9 | Lower the jack until the spare wheel touches th… | 105.0–112.0 | **0.52** |
| 10 | tight wheel | 108.6–132.2 | Lower the car completely, remove the jack, and… | 118.0–126.0 | 0.34 |
| 11 | put things back | 120.9–125.6 | Lower the car completely, remove the jack, and… | 118.0–126.0 | **0.58** |

**11/11 reference steps covered, 9/11 at IoU ≥ 0.3, 7/11 at IoU ≥ 0.5**, mean IoU
0.49. Generated from `data/mga-output.json` by `evaluate.py`, so re-running the
example regenerates these numbers rather than contradicting them.

The four rows below 0.4 are the interesting ones, and they are **two pairs of
reference steps collapsing onto one prediction each**: steps 5 and 6 both land on
"Remove the wheel nuts and take off the flat tire", and 10 and 11 both on "Lower
the car completely, remove the jack, and store the tools". That is the residue of
the same bundling that makes a coarse manual score well and read badly. Every other
row sits at 0.5–0.69.

Now that `prompt` is settable, that is an actionable lever rather than a
limitation — an instruction asking for nut removal and wheel removal as separate
steps would likely split them. Untested.

Step 1 is the other sub-0.4 row, for a different reason: the prediction starts at
11.0 s where the reference starts at 14.8 s, and the two annotators disagree about
that step by 17× (below).

**IoU** (Intersection over Union) measures how much the predicted and true time
spans agree: `overlap / total time covered by either`. 1.0 is exact, 0.0 is no
overlap, and **0.5 is the conventional threshold for "counts as a correct
detection"** in temporal action localization. Worked example, reference step 3:

```
reference   46.5 ─────────────────────── 63.9        (17.4 s)
prediction        50.0 ────────── 60.0               (10.0 s)
overlap           50.0 ────────── 60.0               (10.0 s)
union       46.5 ─────────────────────── 63.9        (17.4 s)
                              IoU = 10.0 / 17.4 = 0.57
```

It penalizes both error directions, which is why it beats comparing midpoints — a
prediction centred correctly but three times too long scores well on midpoint
distance. Three caveats on these numbers:

- IoU alone does not say *which* direction the error goes. Step 3's prediction sits
  inside its reference; step 2's contains it; both score ~0.56.
- Reference intervals are merged across two annotators (earliest start, latest
  end), which inflates their duration and slightly deflates every score.
- **The two annotators disagree severely on step 1** — one marked `[25.6–26.4]`
  (0.8 s), the other `[14.8–28.9]` (14.1 s), a 17× difference. That row's 0.35 is
  softer evidence than the rest.

### The sampling floor

At `max_frames: 64` on a 173 s video, frames land every 2.7 s, so boundaries cannot
be resolved finer than that however good the model is. On a 6-second reference step
that caps IoU around 0.7–0.8 even for a perfect prediction.

### `max_frames` — measured only while the token cap bound  ⏮ HISTORIC

| | `max_frames=64` | `max_frames=32` |
|---|---|---|
| Steps emitted | 6 | 6 |
| Coverage | 16.0–85.0 s | 16.0–81.0 s |
| **Words generated** | **83** | **83** |
| Characters | 441 | 449 |
| Final step cut at | `…and raise the car` | `…Locate the jacking point under the` |

Two entirely different generations landing on the **same word count**, each cut
mid-clause — because at 256 tokens the budget, not the frame count, decided the
length. That is why both rows say 6 steps.

**This is no longer the binding constraint**, so the comparison says nothing about
what `max_frames` is worth today. A real sweep is still owed: 64 is used
throughout here because it matches the reader and preprocessor batch size, not
because it was measured against 16 or 128 on an uncapped run.

## Lessons learned (as of 2026-08-13)

What five blueprint versions, five token budgets and a reproduction on a second
machine actually settled — and what they did not.

### 1. The setting you need is the one that is not exposed, and it moves

Twice in five days the value that decided whether the manual was usable was one the
blueprint did not wire. First `max_new_tokens`, hardcoded at 256, which truncated a
173 s video to 6 steps with the last cut mid-clause. Then `prompt`, hardcoded to
*"…with 10 steps or less"*, which halved the manual by wording rather than by
content. Both are fixed now; neither was fixed when the documentation about it was
written.

The failure is silent in the only place you would look: an unwired value is
accepted with **HTTP 201 and echoed straight back to you in `values`**. So the
durable lesson is not either defect — it is that **a value is honoured only if it
is declared in `values` *and* referenced as `${values.<key>}` by some node**, and
that costs one GET to check and 15 minutes of GPU to discover the hard way.

```
blueprint blp_6va7xxnrrn9fr8ybhgz3v3zvw7 (key=mga, active=True)
  honoured: ['max_frames', 'max_new_tokens', 'prompt']
```

### 2. `max_new_tokens` is a floor, not a ceiling

The model reasons before it answers and pays for the reasoning out of the same
budget, so below a threshold it never reaches an answer at all. Same video, same
prompt, only the budget changing (2026-08-13, on the prompt that was default then
— the step counts are of their day, the threshold is the claim):

| 2048 | 4096 | **16384** | 32768 | 65536 |
|---|---|---|---|---|
| 0 steps | 0 steps | **18 steps** | 18 steps | 18 steps |

The last three are **byte-identical** — generation is deterministic at temperature
0 — so the threshold sits between 4k and 16k and nothing above it changes the
answer. **Lowering this does not give you a shorter manual, it gives you no
manual.**

Worth contrasting with the sibling `tva` example, where raising the budget turned a
correct answer into no answer. Same knob, opposite direction, because the failure
modes differ: MGA ran out of room before finishing its reasoning; TVA had room to
rehearse a verdict twelve times and lose it. **Neither "raise it" nor "lower it" is
the lesson — measure it on your own input, and read the parser's warning.**

### 3. Recall rewards the manual you would not want

The 10-step run scores **better** than run E at IoU ≥ 0.3 — 10/11
against 9/11 — and is plainly the worse manual. MGA tiles the timeline
contiguously, so fewer steps means longer ones that overlap more of any reference
interval. Tighten the threshold and it inverts: **4/11 against 7/11 at IoU ≥ 0.5.**

What recall cannot see at all is that the 10-step run bundles **3.2 actions into
the average step and 7 into its worst**, against 1.7 and 4. An operator cannot tick
off "apply the parking brake" separately from "switch off the ignition", and each
step gets one frame thumbnail however many actions it contains. **Evaluate a manual
by whether it can be followed, not only by temporal recall.**

### 4. `file_id` is a mutable pointer in an org-wide namespace

`file_id` **is the filename**. Upload the same name again and it repoints to a new
object, orphaning the old one:

```
upload 1: {"file_id": "clip.mp4", "file_uid": "fil_58shkhqzt…"}
upload 2: {"file_id": "clip.mp4", "file_uid": "fil_6zmefmn6m…"}
```

The timing is what makes it lethal. A run resolves its inputs at **submit** time
but does not fetch the bytes until after ~7.5 min of model loading, so any upload
of that name inside the window kills a run that already looked healthy — and it
surfaces as `S3 object not found`, which reads as a storage fault and implicates
nothing. It killed three runs here in one afternoon. **Suffix every upload**;
the video stem alone is not enough, because two runs of one video collide and so
does a colleague running the same file.

### 5. Nothing about an empty run looks broken

`status: ready`, HTTP `202`, `running` → `completed`, no ERROR row, and a 38-byte
output containing `{"id": "…", "results": []}`. Every signal says success. The one
thing that distinguishes it is a `WARN` in `/logs`:

```
WARN parser.running  ManualGenerationResultsParserNode: generation ended inside the
  model's reasoning block, so it never produced an answer; dropping 0 rehearsed row(s).
```

That warning did not exist a week ago, and without it an empty manual was
indistinguishable from a working one. **Count the steps in every output before you
trust a run**, and treat `/logs` as the only honest channel — the `status` field
lies in both directions.

### 6. Target the blueprint key; a pinned id fails late and quietly

`mga` was republished **three times in the week of 2026-08-11**. A superseded id
still reads back, still creates a bundle (`201 ready`), still starts a run
(`202 running`) — and then the pod dies one second in with `resolving blueprint:
invalid config for 1 node(s)`. This example defaulted to such an id and every run
failed until it was switched back to the key. **Reading a blueprint back
successfully tells you nothing about whether a run against it will resolve.**

### 7. A reproducibility check that hashes the wrong thing is worse than none

Self-inflicted, and the sharpest lesson here. `KNOWN_MD5` hashed the **file bytes**,
so when the output serialization changed — same manual, different whitespace — the
script told a colleague who had reproduced the run *exactly* that they had not. A
check whose entire job is answering "did the model give me the same answer" cannot
be sensitive to how we happened to format the file. It now hashes the parsed manual
with sorted keys. **Digest the thing you are actually claiming is reproducible.**

### 8. Dev does not serialize jobs

Four submissions once produced ~800 s queue waits, which looked like a queue. It is
not: three concurrent runs came up as **three concurrent pods**, and one was
SIGKILLed mid-load (`pod.terminated  Error (exit=137)`). Run one at a time unless
you are deliberately testing this.

### What this establishes, and what it does not

**Establishes**, on one 173 s video: the mechanics end to end, reproduced
byte-for-byte on a second machine; a measured reasoning floor between 4k and 16k
tokens with everything above it inert; that a caller-supplied prompt roughly halves
action-bundling (3.2 → 1.7 per step) and is what lets a spoken-only instruction
become its own step; and that loose-threshold recall ranks these manuals in the
wrong order.

**Does not establish**: accuracy on other tasks or videos, behaviour above the
~5-minute single-input ceiling, or whether the floor is 16384 rather than something
between 4k and 16k — three budgets were tested, not the interval. Every IoU figure
is a single observation on a video whose two annotators disagree with each other by
up to 17×. **Treat these as red flags worth reproducing, not as MGA's accuracy.**

**Not settled** — why restoring `prompt` did not restore every caution. Run C found
three spoken cautions, today's run finds one. C differs in blueprint, model build
*and* token budget as well as prompt, so nothing here isolates the cause. Also
untested: `size` (224 throughout; `null` keeps source resolution and is the obvious
lever if on-screen text matters), and chunking long videos, which the companion app
does but this example does not.

## Platform behavior worth knowing

- **Reference a blueprint by KEY, not by id.** Both endpoints accept either, and a
  key resolves to whatever is canonical and active. A pinned id fails *after* the
  `202`, at pod start — see [lesson 6](#6-target-the-blueprint-key-a-pinned-id-fails-late-and-quietly).
- **A run can complete successfully and return nothing** — `job.completed`, no
  ERROR row, `results: []`. Count the steps in every output; see
  [lesson 5](#5-nothing-about-an-empty-run-looks-broken).
- **The bundle endpoint is plural.** `POST /agents/bundle` → **404**; use
  `POST /agents/bundles`. The clients committed in the RED and OSM example repos
  use the singular form and **fail against current dev**.
- **Starting a run returns 202, not 201.** Treat only 201 as success and you will
  report a failure while the agent runs unattended, burning GPU with nothing
  collecting its output.
- **Poll `/logs`, not `/events`.** `GET /agents/instances/{id}/logs` is the stream
  the console shows — model load timings, per-input progress, the real error.
  `/events` returns only "run started" and "dispatched to JOS".
- **The instance `status` field lies in both directions.** It stayed `running`
  20+ minutes after a pod terminated with `exit=1`, and `running` after a job had
  completed. Judge terminality from the log stream.
- **Values are accepted whether or not they do anything.** A key absent from the
  blueprint's `values`, or present but never referenced as `${values.<key>}`, is
  stored on the bundle and silently ignored. `nodes` overrides are dropped outright.
- **Cold start is ~7.5 min per run and is never cached.** whisper ~35–40 s download
  + ~4 s load; **newton-fusion ~5 min download + 1 min 40 s load**. Consistent
  across every run. Total wall clock 877–886 s for a 173 s video.
- **Dev does NOT serialize these jobs**, despite appearances. Four concurrent
  submissions once produced queue waits of **799–817 s**, which looked like a
  queue; three concurrent runs on 2026-08-13 instead came up as three concurrent
  pods and one was SIGKILLed mid-load (`pod.terminated  Error (exit=137)`). Run one
  at a time, and budget a sweep as the sum of its runs.
- **Videos over ~5 minutes fail**, and the same error appears on much shorter
  inputs. A 563 s video at default settings ran 5 min 13 s then died:
  `pipeline execution failed: Newton generation event stream closed without a
  terminal event`, exit 1. This is *not* a context limit — at `max_frames: 16` the
  video occupies 392 visual tokens, **0.15% of the 256K window**. The companion app
  hit the identical error on **90-second** chunks, clustered at 138–175 s into a
  chunk while other chunks in the same run ran 384–461 s and finished, which looks
  like an inactivity timeout on the generation stream rather than a duration cap.
- **`max_frames` has no validation at all.** 16, 64, 256, 501, 512, 1024 and `-1`
  (keep every frame) were all accepted with `status: ready`. The arithmetic ceiling
  from `preprocessor_max_pixels` (24 Mi at `size: 224`) is 501 frames and the API
  will not enforce it.
- **No progress events during processing.** Between `input_started` and the
  terminal event there are zero log rows across 5–7 minutes — no frame counts, no
  transcript length, no per-node timing. Neither a failure nor the time spent can
  be attributed.

## Where the deployment diverges from the design doc

Observations for the author, not defects.

- **Audio is in V1.** The doc says F1-0 adds audio in a later version; the
  blueprint runs `whisper:large-v3` into `prompt.audio`, and run C's spoken safety
  cautions prove the transcript reaches the model. Audio is used — via ASR, not
  fusion-native.
- **The runtime instruction arrived, by a different route than the doc implies.**
  The doc describes an algorithm that "takes an input instruction and video" and
  mentions passing the SOP at runtime. `mga` still sets `text_extensions: []`, so
  the *input* path stays closed and a text file cannot be supplied — but
  `default_text` is now `${values.prompt}`, so the instruction is a **bundle
  value** instead. By contrast `tva` takes its instruction from a `prepare` node.
  Three shapes for one idea across two blueprints is worth a convention.
- **Context is not the binding constraint.** The doc motivates its preprocessing
  work by videos overflowing the context window. In practice the constraints are
  the ~5-minute ceiling and the *output* budget — and not because the manual is
  long: the budget is consumed by a reasoning phase the doc does not describe,
  which is why 4096 tokens yields no manual at all while 16384 yields a full one, at
  0.15–0.6% context utilization throughout.
- **The model is fp8** — `f1_0_35b_a3b_fp8_base_040625b4d0750b`, confirming the
  35B/3B-active MoE description and adding quantization the doc does not mention.

## Recommendations

Numbers 1, 2 and 3 have since been implemented; they are kept with their outcome so
the list reads as a record rather than a wish — including where the outcome was not
quite what was asked for.

1. ~~**Expose `max_new_tokens` on the active `mga` blueprint.**~~ ✅ **Done.** It is
   settable on the canonical blueprint. This was the single largest quality gain
   measured here: 4/11 → 8/11 reference steps, prompt untouched.
2. ~~**Expose `prompt`.**~~ ✅ **Done** (PLDEV-1730, 2026-08-12) — and implemented
   exactly as suggested: `text_extensions: []` kept closed, `default_text` changed
   to `${values.prompt}` with the old sentence as the default. Measured gain on
   this clip: action-bundling falls from **3.2 actions per step to 1.7**, and
   IoU ≥ 0.5 rises from 4/11 to 7/11. **Still open underneath it:** the parser can
   still return zero steps for a format-specifying prompt, silently — appending the
   format requirement after the caller's instruction, or failing loudly on a
   non-conforming response, would close that. Evidence in
   [`reference/COMPARISON-prompt-honoured-vs-hardcoded.md`](reference/COMPARISON-prompt-honoured-vs-hardcoded.md).
3. ~~**Raise the default `max_new_tokens` above 256.**~~ ✅ **Done** — 256 → 2048 →
   **16384**, and the middle step is the interesting one: 2048 returned an *empty*
   manual on this video, because the budget is consumed by a reasoning phase before
   any answer is emitted. A default that silently produces nothing is worse than
   one that truncates visibly. 16384 is comfortably above the floor; **please treat
   it as a minimum rather than a tunable**, and consider rejecting sub-floor values
   at bundle creation instead of after 15 minutes of GPU.
4. **Reconsider the default instruction.** Now that `prompt` is settable this only
   affects callers who send none — but they are the ones least likely to know why
   their manual is half the length it could be. "10 steps or less" caps by wording
   rather than by content, and nothing asks the model to cover the whole recording.
5. **Consider a `kind` field distinguishing actions from cautions**, or a notes
   section. Contiguous tiling forces spoken warnings to become numbered steps, and
   when the step budget is tight they are the first thing dropped — one run
   surfaced three spoken cautions, another on the same clip surfaced one, with no
   way to ask for them explicitly.
6. **Emit progress events during processing.** Frame count decoded, transcript
   length, per-node timing would have made the 563 s failure, the truncation and
   the empty-result case diagnosable in one run instead of eight. The new
   `generation ended inside the model's reasoning block` warning is exactly the
   right kind of signal — more of that.
7. **Document the ~5-minute ceiling**, and consider whether 90-second overlapping
   chunks belong inside the blueprint rather than in each caller. The companion app
   implements them and hits `event stream closed without a terminal event` on
   90-second chunks anyway, clustered at 138–175 s while other chunks in the same
   run survive 384–461 s — so chunking alone does not avoid it.
8. **Fix the endpoint drift in the RED and OSM example repos.** Their committed
   clients call `POST /agents/bundle` and fail for anyone following those READMEs.
9. **Keep blueprint ids resolvable, or document that they are not.** A pinned id
   stopped working mid-session when the node schema changed under it. Callers who
   followed the obvious advice — pin a version for reproducibility — were the ones
   who broke.
10. **Make `file_id` collisions impossible, or visible.** `file_id` is the filename
    and it is a mutable pointer in a namespace shared across the org, so an
    unrelated upload of the same name orphans an in-flight run's input. It fails
    ~7 minutes later as `S3 object not found`, which implicates storage rather than
    the overwrite. Returning a distinct handle, or refusing to repoint a name an
    active run depends on, would remove a failure mode that is undiagnosable from
    the caller's side.

## Repo layout

| Path | What it is |
|---|---|
| `run_mga_example.py` | **Start here.** Single-file, stdlib-only end-to-end run: upload → bundle → run → watch `/logs` → download → print. Warns about values the blueprint will ignore. |
| `repro_truncation.py` | Historic: a minimal reproduction of the 256-token truncation, reporting each symptom as CONFIRMED / NOT REPRODUCED. The cap it targets is fixed upstream, so it no longer reproduces anything; kept because `--analyse` still reads a committed output offline and its wired-value preflight is a working regression check. |
| `blueprints/mga-tuned.yaml` | The blueprint that restores `max_new_tokens` and the instruction as bundle values. Needs an admin to register. |
| `reference/` | The three committed run outputs (A, B, C) plus a snapshot of the active blueprint document. |
| `evaluate.py` | Scores an output against CrossTask: reference-step recall, IoU at several thresholds, advisories and unmatched predictions counted separately. Offline. `--compare` prints the A/B/C ladder. |
| `mga_api.py` | Shared stdlib client (streaming upload for large videos, network retries, `/logs` polling). |
| `inspect_bundle.py` | Discovery: dumps a bundle, its blueprint, its runs, their logs and outputs. |
| `probe_max_frames.py` | Creates bundles across a `max_frames` ladder and optionally runs them. Phase 1 is free. |
| `watch_runs.py` | Watches runs via `/logs` with log-based terminality. Use rather than polling `status`. |
| `poll_probe.py` | Attaches to already-launched runs by agent id and compares outputs. |
| `docs/` | The MGA design doc this example implements. |

## Not yet built

- **Semantic step matching.** `evaluate.py` matches on temporal overlap and
  excludes advisories, which is enough to stop the worst inflation but not enough to
  catch a systematic one-step offset — C's predictions run a few seconds late, so
  several reference steps match their *neighbour's* action. Confirming a match by
  text as well as time is the next real improvement.
- **Breadth, deliberately.** This example uses **one video by design**: its job is
  to show the mechanics end to end and to characterise a configuration defect, not
  to benchmark MGA. So treat every number here as a single observation, not an
  average — the 4/11 → 10/11 jump is large and mechanically explained, but the IoU
  figures in particular would move on another video. If a benchmark is ever wanted,
  `INMUrGdA_RA` (201 s), `0kfJ7Lu4tvo` (222 s) and `Z63ZcJXc29E` (188 s) are
  sub-5-minute videos of the same task with all 11 steps annotated, at ~15 min of
  GPU each.
- **A video-suitability preflight.** Duration under ~5 minutes, an audio track
  present, and a sane frame rate are all things worth checking before spending a
  run.
- **A `max_frames` sweep.** Deferred on evidence — 32 vs 64 changed the wording but
  produced the same 6 steps and the same coverage, so it cannot measure anything
  until the cap is lifted.
- **A precision-oriented prompt.** Whether an action-only instruction keeps C's
  10/11 recall while dropping the 4 caution entries is untested.
- **Chunking for long videos**, for the ~5-minute ceiling.

## Dependency risk — the predicted failure, and what actually happened

This section used to warn that the example depended on a superseded blueprint kept
alive only because it still resolved by id, and that *"someone tidying up old
blueprints would be entirely justified in deleting it, and nothing would warn them
that an example repo points at it."*

**That happened on 2026-08-11**, though not by deletion. Three `mga` versions were
published in about two hours, and the `fusion` node's schema stopped accepting the
generation parameters the pinned blueprint wires in from its own defaults. It now
fails at resolution:

```
resolving blueprint: invalid config for 1 node(s):
  - `fusion`: config validation failed: Additional properties are not allowed
    ('repetition_penalty', 'seed', 'stop', 'temperature', 'top_k', 'top_p'
     were unexpected)
```

Reading it, creating a bundle against it and submitting a run all still succeed. The
pod dies one second after it starts. Nothing a caller sends can avoid it, because the
rejected properties are in the blueprint document rather than in the payload.

Two things follow, and both are more general than this repo:

- **Pinning a version for reproducibility is what broke.** The blueprint key `mga`
  resolves to whatever is canonical and active and survived every republication;
  the id did not. Both scripts now default to the key.
- **The committed outputs in [`reference/`](reference) are the only record of what
  MGA produced with a caller-supplied prompt**, because that configuration can no
  longer be run at all. That is why they are checked in rather than regenerated on
  demand, and why `evaluate.py` scores files rather than requiring a live run. Treat
  `reference/` as evidence, not as a cache.

### What was migrated, and what is left

`max_new_tokens` is now exposed on the canonical blueprint, so most of the old
checklist is done:

| # | Change | Status |
|---|---|---|
| 1 | Point the example at the active blueprint | ✅ both scripts default to the key `mga` |
| 2 | Track new blueprint versions | ✅ solved by using the key rather than an id |
| 3 | Re-run and update the expected digest | ✅ `KNOWN_MD5` is the 20-step run, re-measured 2026-08-20 on `blp_3e0hmp…`. It went stale within a week last time; expect to redo this |
| 4 | Revisit the `--max-new-tokens` default | ✅ 16384 — measured; 4096 returns an empty manual, above 16384 changes nothing |
| 5 | Decide what to do about `--prompt` | ✅ honoured again as of 2026-08-12 — **PLDEV-1730** landed. Instruction rewritten 2026-08-20 after it degenerated on a second clip |
| 6–7 | Re-frame the headline and Stage 3 | ✅ both defects fixed; the framing is now the recurring pattern |
| 8 | Delete this section | ⏳ once the ladder above has been re-run on a second video |

**`KNOWN_MD5` goes stale, and that is the normal case.** It has now been wrong
twice for two different reasons. First it was the digest of the 19-step run, which
needed a caller-supplied prompt the blueprint had stopped accepting. Then it was
`caaea09f…` (18 steps, 11–139 s), measured 2026-08-13 — and by 08-20 that no longer
reproduced *on an unchanged prompt*, because the canonical blueprint had been
republished under it; the same instruction now returns 21 steps over 0–111 s.

It is currently `a074294237a829f98be4fb0c82925583` — 20 steps, 14–138 s, measured
2026-08-20 on `blp_3e0hmpqfgc93wvncmr8m30eeny` with the current `DEFAULT_PROMPT`,
and committed as `reference/mga-output-i2JWkDyg26A-20260820-AUDIO-20steps.json` so the
check can be run offline. **A mismatch is more likely to mean the blueprint moved
than that you did something wrong** — check `blueprint <id>` in the run's own
output before assuming otherwise. `EMPTY_MD5` sits next to it so a run that came
back empty is named rather than left as a mystery; an empty manual carries no
generated text, so that one digest is prompt-independent and has never moved.

**What should *not* change:** `repro_truncation.py` keeps its committed baselines.
Its wired-value preflight reads the blueprint document rather than hardcoding key
names, so it flipped from reporting `max_new_tokens` as `NOT WIRED` to `honoured`
with no code change, and did the same for `prompt` on 2026-08-12. That property is
why it survives the removal of the walkthrough that used it.

## Cleanup

Dev has **one GPU** and serializes these jobs, so an abandoned run blocks everyone.
Cancel anything you are not watching:

```sh
# list non-terminal mga runs
curl -s -H "Authorization: Bearer $ATAI_API_KEY" \
  "$ATAI_API_ENDPOINT/agents/instances?limit=100" \
  | python3 -c 'import sys,json; [print(x["id"], x["status"]) for x in json.load(sys.stdin)["data"] \
      if x.get("blueprint")=="mga" and x["status"] not in ("completed","failed","canceled")]'

# cancel one
curl -X POST -H "Authorization: Bearer $ATAI_API_KEY" \
  "$ATAI_API_ENDPOINT/agents/instances/$AGENT_ID/cancel"
```

Do **not** send `DELETE` to an instance URL expecting a no-op — it returns 204 and
removes the run. Use `/cancel`.

Bundles are cheap and harmless to leave; runs are not.

## Data attribution

Annotations and step taxonomies come from **CrossTask**:

> D. Zhukov, J.-B. Alayrac, R. G. Cinbis, D. Fouhey, I. Laptev, J. Sivic.
> "Cross-task weakly supervised learning from instructional videos", CVPR 2019.
> https://github.com/DmZhukov/CrossTask · https://arxiv.org/abs/1903.08225

The CrossTask repository is **BSD-3-Clause**. The dataset ships annotations and
YouTube IDs — 2,750 annotated videos across 18 primary tasks, segments as
`<step>,<start_sec>,<end_sec>`. **The videos themselves are not distributed by the
authors and are copyrighted by their uploaders**; this repo commits neither the
videos nor the annotation dump, only the code that fetches them. For videos YouTube
has since removed, the authors host a
[mirror](https://www.rocq.inria.fr/cluster-willow/dzhukov/missing_videos.tar.gz).

If you select your own videos, filter for duration: at least 26% of CrossTask
exceeds the ~5-minute ceiling by annotated span alone, which is a lower bound since
72% of each video is background.

