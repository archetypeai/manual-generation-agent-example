# Manual Generation Agent — End-to-End Example

An end-to-end example of running the **Manual Generation Agent (MGA)** on the
Archetype AI Platform (Dev): feed it a procedure video, get back an ordered,
timestamped manual, and score it against ground truth.

MGA V1 is zero-shot. The design doc states there are no build-time optimization
steps, and the platform agrees: the `mga` blueprint pins its own models
(`newton-fusion:1.0` and `whisper:large-v3`), so unlike the
[RED](https://github.com/archetypeai/rare-event-detection-agent-example) and
[OSM](https://github.com/archetypeai/operational-state-monitoring-agent-example)
examples there is **no classifier to fit and no `artifacts` map to pass**. A run
is upload → bundle → run → download.

```
video ─► sample frames ─┐
                        ├─► newton-fusion f1-0 ─► parse ─► steps + timestamps
        whisper ASR ────┘
```

## Headline: MGA works. The shipped configuration is what's broken.

On a 173-second tire-change video with 11 annotated reference steps, the **active
canonical blueprint** produces 6 steps covering 16–85 s, missing 7 of 11 reference
steps, with the last step severed mid-clause. That looks like a model limitation.
It isn't.

The cause is an output budget of **`max_new_tokens: 256`**, hardcoded because the
active blueprint no longer exposes the value. Raising it — and then lifting the
step ceiling in the instruction — fixes the agent completely:

| | A — active blueprint | B — `max_new_tokens: 2048` | C — B + coverage prompt |
|---|---|---|---|
| Steps emitted | 6 | 10 | **19** |
| Coverage | 16–85 s | 16–120 s | **11–139 s** |
| Reference steps covered | **4 / 11** | **10 / 11** | 10 / 11 |
| …at IoU ≥ 0.5 | 3 / 11 | 4 / 11 | 5 / 11 |
| Words generated | 83 | 152 | 219 |
| Final step | cut mid-clause | clean | clean |
| md5 | `080e551a…` | `3b8152e7…` | `4067a03b…` |

**A is what ships. C is what the model can do.** Everything between them is
configuration: two values that a superseded version of this same blueprint
exposed, and the current one doesn't.

Note where the gain actually comes from: **`max_new_tokens` alone takes reference
recall from 4/11 to 10/11.** The prompt adds breadth — an earlier start, spoken
safety notes, finer segmentation — but not reference-step recall. And more steps is
not strictly better by IoU: C's finer segmentation produces shorter spans scored
against coarse reference intervals, so its mean IoU (0.46) is *lower* than A's 0.60
across the four steps A found.

Recall here is "some non-advisory prediction overlaps the reference interval". Be
careful with it: MGA's output tiles the timeline with no gaps, so every reference
interval necessarily overlaps *something*. An early version of this table claimed
11/11 for C because a purely temporal match credited *"Do not touch the disc wheels
or brakes"* as the match for `unscrew wheel`. [`evaluate.py`](evaluate.py) excludes
advisories from candidacy for that reason, and reports IoU ≥ 0.3 / 0.5 alongside,
because bare overlap is the most generous reading available.

Run A is confirmed across **three runs on two machines**, byte-identical each time
(generation is deterministic at `temperature: 0.0`).

**Scope: one video, by design.** This example demonstrates the mechanics and
characterises a configuration defect; it is not a benchmark. Every figure below is a
single observation. The 4/11 → 10/11 result is large and has a mechanical
explanation, so it generalises with some confidence; the IoU figures are much more
video-specific and should not be quoted as MGA's accuracy.

All three outputs are committed under [`reference/`](reference), so the whole
comparison is checkable offline with no API key:

```sh
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf64.jsonl                       # 3/3 symptoms
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf64-max_new_tokens2048.jsonl    # 1/3
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf64-mnt2048-coverage-prompt.jsonl  # 0/3
```

## Quick start

```sh
# .env in the repo root
#   ATAI_API_KEY=<dev API key>
#   ATAI_API_ENDPOINT=https://api.dev.u1.archetypeai.app

python3 run_mga_example.py          # fetches the video if needed, then runs
```

Stdlib only — no virtualenv, no install step. Budget **~15 minutes**: about
7.5 min of model download and load on *every* run (nothing is cached between
runs), then roughly 2.5× realtime processing. Dev runs these one at a time.

> ### The defaults are pinned to a superseded blueprint — read this
>
> `run_mga_example.py` defaults to **`blp_76kyqm4vjp9pt8tvfz8tks7x6t`** with
> `max_new_tokens: 2048` and a coverage-oriented instruction, because the **active**
> blueprint caps output at a hardcoded `max_new_tokens: 256` and returns a truncated
> manual — 6 steps covering half the video. The superseded blueprint exposes the
> generation parameters, so the example produces a complete manual today.
>
> **This is a temporary pin.** That blueprint is `is_active: false`: it is not a
> supported target and could be removed at any time. When the generation parameters
> are restored on the active blueprint, flip `BLUEPRINT_DEFAULT` back to
> `BLUEPRINT_ACTIVE` and update `KNOWN_MD5`. Both are marked in the script.
>
> To see what the shipped configuration does instead:
>
> ```sh
> python3 run_mga_example.py --blueprint mga       # the active blueprint, truncated
> ```
>
> `--show` recognises both digests and tells you which one you are looking at.

```sh
python3 run_mga_example.py --dry-run           # print the payloads, call nothing
python3 run_mga_example.py --blueprint mga     # the active blueprint (truncated output)
python3 run_mga_example.py --max-frames 32     # a different sampling density
python3 run_mga_example.py --show reference/mga-output-i2JWkDyg26A-mf64-mnt2048-coverage-prompt.jsonl
```

### The video

[CrossTask](https://github.com/DmZhukov/CrossTask) task 40567 "Change a Tire",
video [`i2JWkDyg26A`](https://www.youtube.com/watch?v=i2JWkDyg26A) — **173.2 s**,
640×360, 25 fps, 4,329 frames, with all 11 reference steps annotated across 28
segments by two independent annotators.

Chosen deliberately: **MGA fails outright on videos longer than about 5 minutes**,
and this is 58% of that ceiling. Source resolution barely matters because the
pipeline resizes every frame to 224×224, so 360p is no worse than 1080p — only
smaller.

Either script fetches it automatically when missing. `--download-only` gets it
without starting a run, so it works before anyone has an API key:

```sh
python3 repro_truncation.py --download-only
#   fetching the video (5.7 MB) -> data/clips/tire_i2JWkDyg26A.mp4 ...
#   video md5 5771f46d637314fdad86d6c4c934dbb5  (matches the reference input)
```

The input digest is checked because an output digest only means something if the
input bytes match. If YouTube offers different formats and the video md5 differs,
the scripts say so and note that the output digest legitimately will not match.

**Videos are never committed.** `data/` is gitignored, with `*.mp4`/`*.mkv`/`*.webm`
as a backstop. Each video is copyrighted by its uploader; CrossTask distributes
annotations and YouTube IDs only, not the media.

## What a run produces

One JSONL record per video:

```json
{"id":"tire_i2JWkDyg26A","results":[
  {"step":0,"instruction":"Set up a warning triangle and turn on the hazard lights.",
   "frame_start":400.0,"frame_end":625.0,
   "timestamp_start":16.0,"timestamp_end":25.0}, …]}
```

Four properties, none of them documented in the blueprint:

- **Timestamps in seconds come free.** The blueprint exposes only
  `parser_output_frame_indices`; `timestamp_start`/`timestamp_end` are emitted
  anyway, so no frames→seconds conversion is needed. `manual-gen-jos` needs a
  second model pass to get `[MM:SS]` reliably — MGA gets it in one.
- **Frame indices are SOURCE frames, not sampled frames.** Max observed index
  2,125 against 4,329 source frames; sampled space would have capped at 63.
  `frame = timestamp × fps` held exactly on every row of every run.
- **`step` is 0-based.** A 10-step manual reports `step` 0–9. CrossTask reference
  steps are 1-based (1–11), so a scorer that joins them naively is off by one —
  and it produces plausible-looking wrong alignments rather than an error.
- **The output tiles the timeline contiguously.** Every step's `timestamp_end` is
  exactly the next step's `timestamp_start`, on integer-second boundaries, in all
  three runs. **MGA performs segmentation, not event detection.** There is no
  "nothing happening here" option, so every span must be labelled — which is why
  a spoken safety caution gets its own step (see
  [Reading the output as a manual](#reading-the-output-as-a-manual)).

## The token cap

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

### This is a regression, not a missing feature

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

## Proof: what the model does when the cap is lifted

`blueprints/mga-tuned.yaml` proposes restoring these values on the active
blueprint. Until that happens, the superseded blueprint can be targeted by **id**
rather than key:

```sh
python3 run_mga_example.py --blueprint blp_76kyqm4vjp9pt8tvfz8tks7x6t \
    --max-new-tokens 2048
```

**Diagnosis only** — that blueprint is `is_active: false`, so nothing built on it
can ship and it may be removed without notice. The scripts print a warning when
you point them at it.

### Run B — raising the budget alone

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

### Run C — budget plus a coverage-oriented instruction

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

## Reading the output as a manual

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

## Evaluating against CrossTask

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

## Reproducing

[`repro_truncation.py`](repro_truncation.py) isolates the defect and reports each
symptom as `CONFIRMED` / `NOT REPRODUCED`. Single file, stdlib only, with the 11
reference intervals embedded so **no CrossTask download is needed**.

### 1. Offline — instant, no API key, no GPU

```sh
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf64.jsonl
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

### 2. Live against dev — ~15 minutes

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
keeps working after a fix: once `max_new_tokens` is restored it reports `honoured`
with no code change, which makes it a regression test too.

### 3. The full ladder

```sh
# A — the no-op on the active blueprint
python3 repro_truncation.py --max-new-tokens 1024

# B — 10 steps, 10/11
python3 repro_truncation.py --blueprint blp_76kyqm4vjp9pt8tvfz8tks7x6t \
    --max-new-tokens 2048

# C — 19 steps, 11/11
python3 repro_truncation.py --blueprint blp_76kyqm4vjp9pt8tvfz8tks7x6t \
    --max-new-tokens 2048 \
    --prompt "Generate a concise, ordered list of every distinct step performed in this video, covering the procedure from the first action to the last. Include brief steps and steps that are repeated. Use up to 20 steps. Keep each step to at most 15 words. Use both what is shown and what is said. Do not invent steps that are not shown or stated in the video."
```

## Platform behavior worth knowing

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
- **Videos over ~5 minutes fail.** A 563 s video at default settings ran 5 min 13 s
  then died: `pipeline execution failed: Newton generation event stream closed
  without a terminal event`, exit 1. Note this is *not* a context limit — at
  `max_frames: 16` the video occupies 392 visual tokens, **0.15% of the 256K
  window**.
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

1. **Expose `max_new_tokens` on the active `mga` blueprint.** It is an integer that
   cannot affect output format, so it carries none of the parser fragility that
   caused `prompt` to be rolled back — run B raised it with the default prompt
   untouched and the parser was fine. Until it is exposed, this repo's example has
   to pin a superseded blueprint to produce a usable manual.
2. **Treat `prompt` separately.** It was exposed, found fragile because a
   format-specifying prompt makes the parser return zero steps, and reverted — a
   sound call. Appending the format requirement after the user instruction, or
   failing loudly on a non-conforming response, would make it safe to expose.
   `blueprints/mga-tuned.yaml` has both wired for reference.
3. **Raise the default `max_new_tokens` above 256.** At 256 the agent emits ~6
   verbose steps for any video, which reads as a product limitation rather than a
   deliberate choice.
4. **Reconsider the default instruction.** "10 steps or less" becomes the binding
   constraint the moment the budget is raised, and nothing asks the model to cover
   the whole recording.
5. **Consider a `kind` field distinguishing actions from cautions**, or a notes
   section. Contiguous tiling forces spoken warnings to become numbered steps.
6. **Emit progress events during processing.** Frame count decoded, transcript
   length, per-node timing would have made both the 563 s failure and this
   truncation diagnosable in one run instead of eight.
7. **Document the ~5-minute ceiling**, and consider whether 90-second overlapping
   chunks — what `manual-gen-jos` already does, with timestamp offsetting and dedup
   — belong inside the blueprint rather than in each caller.
8. **Fix the endpoint drift in the RED and OSM example repos.** Their committed
   clients call `POST /agents/bundle` and fail for anyone following those READMEs.

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
  11/11 recall while dropping the 4 caution entries is untested.
- **Chunking for long videos**, for the ~5-minute ceiling.

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
