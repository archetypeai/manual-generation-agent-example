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

**What you get:** an 18-step manual covering 11–139 s of the 173 s clip, each step
carrying a start and end timestamp you can trace back to the video, plus a content digest
that should match `caaea09f…` exactly — generation is deterministic at
temperature 0, so a matching digest means you reproduced the reference run.

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

**Read it twice, because the metric and the manual disagree.** D scores best on
IoU ≥ 0.3 — 10/11, better than today's E — and is the worst manual of the five.
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

⚠️ **`max_new_tokens` is a floor, not a ceiling.** The model reasons before it
answers out of the same budget, so below the threshold it never reaches an answer
and you get `job.completed`, no error row, `results: []`. On this video:

| 2048 | 4096 | **16384** | 32768 | 65536 |
|---|---|---|---|---|
| 0 steps | 0 steps | **18 steps** | 18 steps | 18 steps |

The last three are byte-identical (generation is deterministic at temperature 0),
so the threshold is between 4k and 16k and nothing above it helps. Lowering this
does not shorten the manual — it empties it, silently.

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
| `max_new_tokens` | **16384** | ✅ | Was the first defect (hardcoded **256**, manual truncated). Now settable and the blueprint's own default. It is a **floor**: the model reasons out of this budget, so 2048 and 4096 both return an EMPTY manual on this video while 16384/32768/65536 return the same 18 steps. |
| `size` | not sent (224) | ✅ | Every frame is resized to `size × size` before the model sees it. `null` keeps the source resolution. Untested here; the obvious lever if on-screen text is unreadable. |
| `prompt` | a coverage instruction | ✅ | Was the second defect: hardcoded to "10 steps or less" between 2026-08-11 and 08-12, so a caller's instruction was accepted and discarded. Wired again as `${values.prompt}` (**PLDEV-1730**). Say what to COVER, never how to format — the results parser owns the template. |

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

Same as B, plus `--prompt`:

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
      "prompt": "Generate a concise, ordered list of every distinct step performed in this video, covering the procedure from the first action to the last. Include brief steps and steps that are repeated. Use up to 20 steps. Keep each step to at most 15 words. Use both what is shown and what is said. Do not invent steps that are not shown or stated in the video."
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
python3 evaluate.py reference/mga-output-i2JWkDyg26A-mf64-mnt2048-coverage-prompt.json
python3 evaluate.py --compare reference/*.json         # the whole ladder
```

[`evaluate.py`](evaluate.py) is **offline and stdlib-only** — no API key, no network
— so re-scoring is free and repeatable, which matters because a run happens once but
you will read its numbers many times.

It deliberately **does not compute precision** against CrossTask. The step list is a
per-task, wikiHow-derived reference procedure rather than an inventory of the video,
so real actions it omits would be scored as false positives. Unmatched predictions
are counted and displayed, never scored. See [3c](#3c-reading-the-output-as-a-manual).

### Accuracy of run A, where it does predict

| # | Reference step | Interval | MGA prediction | Predicted | IoU |
|---|---|---|---|---|---|
| 1 | brake on | 14.8–28.9 | Set up a warning triangle and turn on the hazard lights. | 16.0–25.0 | 0.64 |
| 2 | get things out | 35.7–41.8 | Retrieve the spare wheel and tools from the trunk. | 35.0–46.0 | 0.56 |
| 3 | start loose | 46.5–63.9 | Use the wrench to loosen the wheel nuts one half to one turn… | 50.0–66.0 | **0.71** |
| 4 | jack up | 73.8–88.9 | Place the jack under the vehicle's jacking point and raise the car | 66.0–85.0 | 0.49 |
| 5–11 | unscrew wheel → put things back | 89.0–132.2 | — no prediction — | — | 0.00 |

**Recall 4/11 = 36%; mean IoU 0.60 over matched steps, 0.22 over all 11.** Where
run A predicts, localization is good. It simply stops.

**IoU** (Intersection over Union) measures how much the predicted and true time
spans agree: `overlap / total time covered by either`. 1.0 is exact, 0.0 is no
overlap, and **0.5 is the conventional threshold for "counts as a correct
detection"** in temporal action localization. Worked example, step 3:

```
reference   46.5 ─────────────────────── 63.9        (17.4 s)
prediction        50.0 ──────────────────────── 66.0 (16.0 s)
overlap           50.0 ─────────────── 63.9         (13.9 s)
union       46.5 ────────────────────────── 66.0    (19.5 s)
                              IoU = 13.9 / 19.5 = 0.71
```

It penalizes both error directions, which is why it beats comparing midpoints — a
prediction centred correctly but three times too long scores well on midpoint
distance. Three caveats on these numbers:

- IoU alone does not say *which* direction the error goes. Step 1 is too short
  (prediction inside reference), step 2 too long (reference inside prediction), and
  both score ~0.6.
- Reference intervals are merged across two annotators (earliest start, latest
  end), which inflates their duration and slightly deflates every score.
- **The two annotators disagree severely on step 1** — one marked `[25.6–26.4]`
  (0.8 s), the other `[14.8–28.9]` (14.1 s), a 17× difference. That step's 0.64 is
  softer evidence than the other three.

### The sampling floor

At `max_frames: 64` on a 173 s video, frames land every 2.7 s, so boundaries cannot
be resolved finer than that however good the model is. On a 6-second reference step
that caps IoU around 0.7–0.8 even for a perfect prediction.

### `max_frames` changes the words, not the outcome

| | `max_frames=64` | `max_frames=32` |
|---|---|---|
| Steps emitted | 6 | 6 |
| Coverage | 16.0–85.0 s | 16.0–81.0 s |
| **Words generated** | **83** | **83** |
| Characters | 441 | 449 |
| Final step cut at | `…and raise the car` | `…Locate the jacking point under the` |
| md5 | `080e551a…` | `7d3ed9c3…` |

Two entirely different generations landing on the **same word count**, each cut
mid-clause. It also shows verbosity trading directly against coverage: `mf=32`
produced richer descriptions and 4 s *less* coverage. A `max_frames` sweep is
therefore deferred — it cannot measure anything while the cap binds.

## Reproducing — LEGACY

> **⚠️ LEGACY. Kept for the record, not currently reproducible.**
>
> This section reproduces a **token-cap defect that no longer exists**, using a
> **blueprint that no longer runs**. Both changed on 2026-08-11:
>
> - The canonical `mga` blueprint now exposes `max_new_tokens` and defaults it to
>   **16384** instead of the hardcoded 256 this section is about. The defect is
>   fixed upstream.
> - The blueprint these steps target, `blp_76kyqm4vjp9pt8tvfz8tks7x6t`, is
>   **unrunnable**. It wires `repetition_penalty`, `seed`, `stop`, `temperature`,
>   `top_k` and `top_p` into its own `fusion` node, and the platform stopped
>   accepting those properties. Reading it, creating a bundle and submitting a run
>   all still succeed; the pod dies one second after it starts with
>   `resolving blueprint: invalid config for 1 node(s)`.
> - `prompt` was **not settable** on the canonical blueprint while this section was
>   written, so the ladder's rung C could not be built. It is settable again as of
>   2026-08-12 (PLDEV-1730), and rung C's equivalent is rung E in the table at the
>   top of this README.
>
> **The offline path below still works** and is worth keeping: it analyses the
> committed reference outputs and needs no API key, no GPU and no network. The
> live paths (2 and 3) will fail at blueprint resolution.
>
> Not deleted because the committed outputs, digests and reference intervals are
> the evidence for the finding, and the finding was real. Delete it once a
> canonical blueprint exposes `${values.prompt}` and this can be rewritten against
> something that runs.

[`repro_truncation.py`](repro_truncation.py) isolates the defect and reports each
symptom as `CONFIRMED` / `NOT REPRODUCED`. Single file, stdlib only, with the 11
reference intervals embedded so **no CrossTask download is needed**.

### 1. Offline — instant, no API key, no GPU  ✅ still works

```sh
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf64.json
```

```
  SYMPTOM 1 — coverage stops before the procedure ends
    last prediction ends     : 85.0s
    last reference step ends : 132.2s
    procedure left uncovered : 47.2s (36% of the annotated procedure)
    => CONFIRMED
  SYMPTOM 2 — reference steps 5-11 get no prediction
    missed: [5, 6, 7, 8, 9, 10, 11]  (7/11 reference steps)
    => CONFIRMED (exactly steps 5-11, as filed)
  SYMPTOM 3 — final step is cut mid-clause (the token-cap signature)
    step 5: ends 'r'  <-- no terminal punctuation
    => CONFIRMED

3/3 symptoms confirmed.
```

Coverage is judged against the **annotated procedure's end (132.2 s)**, not the
video duration — the tail is outro with no steps in it, so measuring to 173.2 s
would penalize output that is actually complete.

### 2. Live against dev — ~15 minutes  ❌ fails at blueprint resolution

```sh
printf 'ATAI_API_KEY=<key>\nATAI_API_ENDPOINT=https://api.dev.u1.archetypeai.app\n' > .env
python3 repro_truncation.py
```

Every run opens with a configuration block that states what is being sent **and
whether the blueprint will honour it**, so an inert value is caught before you
spend 15 minutes:

```
  blueprint       blp_0zmce3qnjp8659dp1pszdqbtx4
                  key=mga  canonical=True  ACTIVE — this is what ships
  max_frames      64                          -> honoured
  max_new_tokens  1024                        -> NOT A VALUE on this blueprint — accepted by the API, ignored

  instruction in force (HARDCODED in the blueprint — not settable from a bundle):
    "Generate a concise, ordered list of distinct steps with 10 steps or less."

  WARNING: ['max_new_tokens'] will have NO effect on this blueprint.
```

That check reads the blueprint document rather than hardcoding known keys, so it
kept working across both fixes: `max_new_tokens` flipped from `NOT A VALUE` to
`honoured` when it was exposed, and `prompt` did the same on 2026-08-12, neither
requiring a code change. That is what makes it a regression test rather than a
snapshot.

### 3. The full ladder  ⚠️ A is unreachable; B and C need no id

Rung A reproduced a hardcoded 256-token cap that no longer exists, so it cannot be
re-run at all. B and C no longer need the pinned id they were written with — both
`max_new_tokens` and `prompt` are wired on the canonical blueprint, so drop
`--blueprint` entirely. The historic outputs stay committed under `reference/`.

```sh
# A — the shipped defect: 6 steps, 4/11 reference steps, output truncated
#     NOT REPRODUCIBLE: the 256-token cap it depends on was removed upstream.
python3 repro_truncation.py --max-new-tokens 1024

# B — budget only:      10 steps, 10/11 reference steps (4/11 at IoU>=0.5)
python3 repro_truncation.py --max-new-tokens 16384

# C — budget + prompt:  19 steps, 10/11 reference steps (5/11 at IoU>=0.5)
#     Today the same prompt at 16384 gives 18 steps and 7/11 at IoU>=0.5 (rung E).
python3 repro_truncation.py --max-new-tokens 16384 \
    --prompt "Generate a concise, ordered list of every distinct step performed in this video, covering the procedure from the first action to the last. Include brief steps and steps that are repeated. Use up to 20 steps. Keep each step to at most 15 words. Use both what is shown and what is said. Do not invent steps that are not shown or stated in the video."
```

## Platform behavior worth knowing

- **Reference a blueprint by KEY, not by id.** `POST /agents/bundles` and
  `GET /agents/blueprints/{…}` both accept either, and a key resolves to whatever is
  canonical and active. Three `mga` versions were published in about two hours on
  2026-08-11, and a pinned id **stopped resolving mid-session**: the superseded
  blueprint wires generation parameters into its own `fusion` node that the node
  schema no longer accepts, so it now fails with `resolving blueprint: invalid
  config for 1 node(s)`. Reading it, creating a bundle and submitting a run all
  still succeed; the pod dies one second after it starts. Pin an id only to
  reproduce a specific past run.
- **A run can complete successfully and return nothing.** `max_new_tokens: 2048` —
  the current blueprint's own default — produced `{"id":"…","results":[]}` on this
  173 s video: `job.completed`, no ERROR row, 39 bytes, after 543 s of generation.
  Count the steps; neither the status nor the logs will tell you.
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
- **Dev serializes these jobs.** Four concurrent submissions produced queue waits
  of **799–817 s**, not parallelism. Budget a sweep as the sum of its runs.
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
- **There is no runtime instruction on the active blueprint.** The doc describes an
  algorithm that "takes an input instruction and video" and mentions passing the
  SOP at runtime. Active `mga` sets `text_extensions: []` with a fixed
  `default_text`, commented *"Manual generation has no SOP: this fixed instruction
  is used for every video."* By contrast `tva`'s source config is `{}` and its
  instruction arrives from a `prepare` node — **TVA accepts a reference procedure
  at runtime and MGA deliberately does not.**
- **Context is not the binding constraint.** The doc motivates its preprocessing
  work by videos overflowing the context window. In practice the constraints are
  the output budget and the ~5-minute ceiling, at 0.15–0.6% context utilization.
- **The model is fp8** — `f1_0_35b_a3b_fp8_base_040625b4d0750b`, confirming the
  35B/3B-active MoE description and adding quantization the doc does not mention.

## Recommendations

Numbers 1 and 3 have since been implemented; they are kept with their outcome so the
list reads as a record rather than a wish.

1. ~~**Expose `max_new_tokens` on the active `mga` blueprint.**~~ ✅ **Done.** It is
   settable on the canonical blueprint. This was the single largest quality gain
   measured here: 4/11 → 8/11 reference steps, prompt untouched.
2. **Expose `prompt`.** Still open — **PLDEV-1730**, and now the binding constraint.
   The current blueprint hardcodes *"…10 steps or less"*, which caps the manual at
   10 steps by wording rather than by content, bundles up to seven actions into one
   step, and **drops two spoken safety cautions**. It was exposed once, found
   fragile because a format-specifying prompt makes the parser return zero steps,
   and reverted — a sound call at the time. Appending the format requirement after
   the caller's instruction, or failing loudly on a non-conforming response, makes
   it safe. Note the fix does not require relaxing `text_extensions: []`: keep that,
   and change `default_text` to `${values.prompt}` with the current sentence as the
   default. Full evidence in
   [`reference/COMPARISON-prompt-honoured-vs-hardcoded.md`](reference/COMPARISON-prompt-honoured-vs-hardcoded.md).
3. ~~**Raise the default `max_new_tokens` above 256.**~~ ✅ **Done** — now 2048.
   **But 2048 returns an empty manual on this video**: `job.completed`, no error,
   `results: []`. A default that silently produces nothing is worse than one that
   truncates visibly, because nothing in the status, the logs or the exit code
   distinguishes it from success.
4. **Reconsider the default instruction.** "10 steps or less" became the binding
   constraint the moment the budget was raised — exactly as predicted here — and
   nothing asks the model to cover the whole recording.
5. **Consider a `kind` field distinguishing actions from cautions**, or a notes
   section. Contiguous tiling forces spoken warnings to become numbered steps, and
   when the step budget is tight they are the first thing dropped.
6. **Emit progress events during processing.** Frame count decoded, transcript
   length, per-node timing would have made the 563 s failure, this truncation and
   the empty-result case diagnosable in one run instead of eight.
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

## Repo layout

| Path | What it is |
|---|---|
| `run_mga_example.py` | **Start here.** Single-file, stdlib-only end-to-end run: upload → bundle → run → watch `/logs` → download → print. Warns about values the blueprint will ignore. |
| `repro_truncation.py` | Minimal reproduction of the truncation, reporting each symptom as CONFIRMED / NOT REPRODUCED. `--analyse` works offline; `--blueprint` / `--max-new-tokens` / `--prompt` walk the A→B→C ladder. |
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
| 3 | Re-run and update the expected digest | ✅ `KNOWN_MD5` is the 18-step run on the current blueprint, re-validated 2026-08-13 |
| 4 | Revisit the `--max-new-tokens` default | ✅ 16384 — measured; 4096 returns an empty manual, above 16384 changes nothing |
| 5 | Decide what to do about `--prompt` | ✅ honoured again as of 2026-08-12 — **PLDEV-1730** landed |
| 6–7 | Re-frame the headline and Stage 3 | ✅ both defects fixed; the framing is now the recurring pattern |
| 8 | Delete this section | ⏳ once the ladder above has been re-run on a second video |

**`KNOWN_MD5` is live again.** It was stale for a while — the digest of the 19-step
run, which needed a caller-supplied prompt that the blueprint had stopped
accepting. With `prompt` restored it has been re-measured on the current
blueprint: `caaea09f1c19645d444fb6ce33f1b139`, 18 steps, and a matching digest
means you reproduced the run exactly. `EMPTY_MD5` is next to it so a run that came
back empty is named rather than left as a mystery.

**What should *not* change:** `repro_truncation.py` keeps its committed baselines.
Its wired-value preflight reads the blueprint document rather than hardcoding key
names, so it flipped from reporting `max_new_tokens` as `NOT WIRED` to `honoured`
with no code change — and it will do the same for `prompt`.

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

