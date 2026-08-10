# Manual Generation Agent — End-to-End Example

An end-to-end example of running the **Manual Generation Agent (MGA)** on the
Archetype AI Platform (Dev): feed it a procedure video, get back an ordered list
of steps with timestamps, and score those steps against ground truth.

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

## Status — read this first

**The example is not finished, and the reason is a finding rather than a gap in
the work.** MGA currently truncates its output. On a 173-second video with 11
annotated reference steps it emits **6 steps covering 16–85 s**, leaving 51% of
the video with no output and 7 reference steps entirely uncovered. The last step
is cut mid-clause.

The cause is an output-token cap that **cannot be changed from a bundle**. See
[The token cap](#the-token-cap) below. Until `max_new_tokens` is exposed,
end-to-end quality numbers from this repo describe a truncated agent, so the
evaluation stage (a step-alignment and temporal-IoU scorer) is deliberately not
built yet — it would measure the cap, not the model.

What *is* verified and usable today: a working run path, the real output format,
exact timestamps, and a set of platform behaviours that cost real time to
discover.

To reproduce the truncation yourself — instantly, with no API key or GPU:

```sh
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf64.jsonl
```

That checks the three symptoms against the committed output and prints
`3/3 symptoms confirmed` — no API key, no GPU, no video download.

```sh
python3 repro_truncation.py                       # reproduce it live on dev (~15 min)
python3 repro_truncation.py --max-new-tokens 1024 # show that value has no effect (~15 min)
```

See [Reproducing the truncation](#reproducing-the-truncation) for what each one
prints and how to read it.

## Quick start

```sh
# .env in the repo root
#   ATAI_API_KEY=<dev API key>
#   ATAI_API_ENDPOINT=https://api.dev.u1.archetypeai.app

python3 run_mga_example.py --download
```

Stdlib only — no virtualenv, no install step. `--download` fetches the example
video with `yt-dlp`; drop the flag if it is already on disk. Budget **~15
minutes**: about 7.5 min of model download and load on *every* run (nothing is
cached between runs), then roughly 2.5× realtime processing.

Other entry points:

```sh
python3 run_mga_example.py --max-frames 16    # the blueprint default (this repo uses 64)
python3 run_mga_example.py --dry-run          # print the payloads, call nothing
python3 run_mga_example.py --show reference/mga-output-i2JWkDyg26A-mf64.jsonl
```

Generation is **deterministic** — two independent runs produced byte-identical
output — so the script verifies its result against the reference digest
`080e551a88f0bdcd857e48557366ae60`. A match means you reproduced the run exactly.

### The video

[CrossTask](https://github.com/DmZhukov/CrossTask) task 40567 "Change a Tire",
video [`i2JWkDyg26A`](https://www.youtube.com/watch?v=i2JWkDyg26A) — 173 s,
640×360, 25 fps, all 11 reference steps annotated across 28 segments from two
independent annotators.

It was chosen deliberately. **MGA fails outright on videos longer than about 5
minutes**, and this one is 58% of that ceiling. Source resolution is nearly
irrelevant because the pipeline resizes every frame to 224×224, so 360p is no
worse than 1080p here — only smaller.

Get the video with either script — `--download-only` fetches it and stops, making
no API calls, so it works before anyone has a key:

```sh
python3 repro_truncation.py --download-only
#   fetching the video (5.7 MB) -> data/clips/tire_i2JWkDyg26A.mp4 ...
```

A bare `python3 repro_truncation.py` also fetches it automatically when missing,
so `--download-only` is only for getting the file without starting a run.

It lands in **`data/clips/tire_i2JWkDyg26A.mp4`**, creating the directory — you do
not have to pick a location. Override with `--video <path>` if you already have a
copy elsewhere. If YouTube answers 403 (it rate-limits repeated pulls), the script
says so and points at the offline `--analyse` path, which needs no video. Without `yt-dlp` installed,
the scripts print the URL and the exact path to save to.

**Videos are never committed.** `data/` is gitignored, with `*.mp4`/`*.mkv`/`*.webm`
as a backstop against a stray download landing in the repo root. Each video is
copyrighted by its uploader; CrossTask distributes annotations and YouTube IDs
only, not the media.

## What a run produces

One JSONL record per video:

```json
{"id":"tire_i2JWkDyg26A","results":[
  {"step":0,"instruction":"Set up a warning triangle and turn on the hazard lights.",
   "frame_start":400.0,"frame_end":625.0,
   "timestamp_start":16.0,"timestamp_end":25.0}, …]}
```

Two things the blueprint does not advertise:

- **Timestamps in seconds come free.** The blueprint exposes only
  `parser_output_frame_indices`; `timestamp_start`/`timestamp_end` are emitted
  anyway, so no frames→seconds conversion is needed.
- **Frame indices are SOURCE frames, not sampled frames.** Max observed index
  2,125 against 4,329 source frames — sampled space would have capped at 63.
  `frame = timestamp × fps` held exactly on every row.

The full reference output is committed at
[`reference/mga-output-i2JWkDyg26A-mf64.jsonl`](reference/mga-output-i2JWkDyg26A-mf64.jsonl),
alongside a snapshot of the blueprint document that produced it.

### Accuracy where it does predict

| # | Reference step | Interval | MGA prediction | Predicted | IoU |
|---|---|---|---|---|---|
| 1 | brake on | 14.8–28.9 | Set up a warning triangle and turn on the hazard lights. | 16.0–25.0 | 0.64 |
| 2 | get things out | 35.7–41.8 | Retrieve the spare wheel and tools from the trunk. | 35.0–46.0 | 0.56 |
| 3 | start loose | 46.5–63.9 | Use the wrench to loosen the wheel nuts one half to one turn… | 50.0–66.0 | **0.71** |
| 4 | jack up | 73.8–88.9 | Place the jack under the vehicle's jacking point and raise the car | 66.0–85.0 | 0.49 |
| 5–11 | unscrew wheel → put things back | 89.0–132.2 | — no prediction — | — | 0.00 |

**IoU** (Intersection over Union) measures how much the predicted and true time
spans agree: `overlap / total time covered by either`. 1.0 is an exact match, 0.0
is no overlap, and **0.5 is the conventional threshold for "counts as a correct
detection"** in temporal action localization. Worked example, step 3:

```
reference   46.5 ─────────────────────── 63.9        (17.4 s)
prediction        50.0 ──────────────────────── 66.0 (16.0 s)
overlap           50.0 ─────────────── 63.9         (13.9 s)
union       46.5 ────────────────────────── 66.0    (19.5 s)
                              IoU = 13.9 / 19.5 = 0.71
```

It penalizes both error directions, which is why it beats comparing midpoints — a
prediction centred correctly but three times too long would score well on
midpoint distance. Two caveats: IoU alone does not tell you *which* direction the
error goes (step 1 is too short, step 2 too long, both score ~0.6), and reference
intervals here are merged across two annotators, which inflates their duration
and so slightly deflates every score.

**Recall 4/11 = 36%; mean IoU 0.60 over matched steps, 0.22 over all 11.** Where
MGA predicts, it is good. It simply stops.

### `max_frames` changes the words, not the outcome

Running the same video at `max_frames=32` reproduces all three symptoms and,
tellingly, stops at the same token budget:

| | `max_frames=64` | `max_frames=32` |
|---|---|---|
| Steps emitted | 6 | 6 |
| Coverage | 16.0–85.0 s | 16.0–81.0 s |
| **Words generated** | **83** | **83** |
| Characters | 441 | 449 |
| Final step cut at | `…and raise the car` | `…Locate the jacking point under the` |
| Reference steps missed | 5–11 | 5–11 |
| md5 | `080e551a…` | `7d3ed9c3…` |

Two entirely different generations — different wording, different boundaries —
landing on the **same word count**, each cut mid-clause. That is a hard cap, and
the blueprint history says what it is: `max_new_tokens: 256`.

It also shows **verbosity and coverage trade off directly**: at `mf=32` the
descriptions are richer ("Stop the car on a firm level surface away from
traffic", "including the jack and wrench") and coverage is 4 s *shorter*. So
raising `max_new_tokens` alone is not sufficient — the instruction must also ask
for brevity per step, or a larger budget buys richer prose instead of more steps.
That is why [`blueprints/mga-tuned.yaml`](blueprints/mga-tuned.yaml) changes both.

Both outputs are committed under `reference/` so this comparison can be checked
offline:

```sh
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf32.jsonl
```

Note that five of the seven missed steps are 2.6–4.6 s long. At `max_frames=64`
on a 173 s video the sampling interval is 2.7 s, so even with a larger token
budget the short steps will remain hard; the two long misses (jack down 13.4 s,
tight wheel 23.6 s) should recover as soon as generation is not cut off.

Two predictions have no reference match — *"Apply the parking brake, put the car
in park…"* and *"remove the hubcap if the wheel has one"*. Neither is a
hallucination; CrossTask's taxonomy has no hubcap step and collapses what MGA
splits. **A scorer has to separate taxonomy gaps from model errors** or it will
penalize correct output.

## The token cap

**The cap is `max_new_tokens: 256`.** That is not inferred — it is the documented
default of the *previous* version of this blueprint, which exposed the value and
annotated it: `# Maximum number of tokens the model may generate in the response.
`..32768``. The active blueprint no longer exposes it, so 256 is now effectively
hardcoded.

The output-side evidence is punctuation: steps 0–4 all end in a full stop, step 5
ends `…and raise the car` with none. (An earlier version of this README estimated
"~169 tokens" from character counts. That estimate was too low — it missed the
per-step scaffolding the model emits before the parser strips it. Trust the 256.)

**It cannot be raised from a bundle on the active blueprint.** Three runs across
two machines all produced byte-identical output, `1146 bytes`, md5
`080e551a88f0bdcd857e48557366ae60` — with and without `max_new_tokens: 1024`:

| Run | Bundle values | Output | md5 |
|---|---|---|---|
| `agt_37vrxv2q…` | `max_frames: 64` | 1146 B, 6 steps | `080e551a88f0…` |
| `agt_3kqp9y2n…` | `max_frames: 64`, `max_new_tokens: 1024` | 1146 B, 6 steps | `080e551a88f0…` |

The blueprint's fusion node config is the whole explanation:

```yaml
fusion:
  key: NewtonFusionModelNode
  config:
    model: ${models.fusion}     # ← that is the entire config
```

Nothing references `${values.max_new_tokens}`, so a value by that name is stored
on the bundle and wired to nothing. Both workarounds fail *silently*:
`values: {max_new_tokens: …}` returns 201 and is echoed back with no effect, and
`nodes: {fusion: {config: …}}` returns 201 with the `nodes` key absent from the
response entirely.

**These parameters were exposed and then removed.** A superseded version of this
same blueprint — `blp_76kyqm4vjp9pt8tvfz8tks7x6t`, `blueprint_key: mga`,
`is_canonical: true`, **`is_active: false`** — wires nine of them straight into the
fusion node:

```yaml
fusion:
  key: NewtonFusionModelNode
  config:
    model, max_new_tokens, enable_thinking, temperature,
    top_k, top_p, repetition_penalty, seed, stop
```

and takes its instruction from a value too: `default_text: ${values.prompt}`.
Bundles built on it carry real settings — `bnd_6ks6k012458mg9a648kkf7tebk` has
`max_new_tokens: 1024`, `temperature: 0.2`, `enable_thinking: true` and a full
custom prompt. Its `temperature: 0.0` default also explains the determinism noted
above: that YAML says `0.0` "decodes greedily and makes every sampling knob below
inert".

So the question for the algorithm author is not whether these can exist, but:
**why were nine generation parameters dropped from the active `mga` blueprint, and
can they be restored?**

Two platform notes that made this easy to miss:

- **`GET /agents/blueprints` only returns `is_active: true` blueprints.** An
  earlier version of this README claimed no generation parameter existed anywhere,
  based on surveying all 35 node config keys across "all 8 blueprints". The
  listing had silently excluded the one that had them.
- **Blueprints are soft-deprecated, not deleted.** The superseded version still
  resolves by `blueprint_id`, still accepts new bundles, and still runs. Only the
  *key* `mga` resolves to the active version.

### `blueprints/mga-tuned.yaml`

A ready-to-register blueprint that wires the fix through. Graph, models and
artifacts are copied verbatim from canonical `mga` (verified identical
edge-for-edge), with two changes: generation parameters on the fusion node, and
the instruction exposed as a value instead of a hardcoded `default_text`.

Registering it **requires the admin role**. Once registered:

```sh
curl -X POST -H "Authorization: Bearer $ATAI_API_KEY" -H "Content-Type: application/json" \
  "$ATAI_API_ENDPOINT/agents/bundles" \
  -d '{"blueprint":"mga-tuned","values":{"max_frames":64,"max_new_tokens":2048}}'
```

The fusion config **key names are confirmed**, not guessed: they are exactly what
the superseded `blp_76kyqm…` wired into the same node, so this file is closer to
"restore what was removed" than a new proposal. It omits `seed` and `stop`, which
that version also exposed and which are worth adding back too.

One judgement call: `enable_thinking` defaults to `false` here even though the
bundle referenced above sets it `true`, because thinking tokens plausibly count
against `max_new_tokens` and would *reduce* the steps that fit. Raise the budget
before enabling it.

> **Do not fix this by editing the active `mga` in place.** Prefer registering a
> new version and flipping `is_active`, which is how `blp_76kyqm…` was superseded
> — bundles pin `blueprint_id`, so an existing bundle keeps working when a new
> version becomes active. (An earlier version of this README claimed updating a
> canonical blueprint orphans every bundle built on it, citing the dev console
> bundle `bnd_3ve9qfkve99e980rd858tm9dfb` returning 404. That was wrong twice
> over: the 404 came from using the singular `/agents/bundle/{id}` — which 404s
> for everything — and the bundle in fact returns 200 on `/agents/bundles/{id}`.)

## Reproducing the truncation

[`repro_truncation.py`](repro_truncation.py) isolates the defect and reports each
symptom as `CONFIRMED` or `NOT REPRODUCED`, so there is no need to eyeball the
output and know what it should have said. It is single-file and stdlib-only, and
the 11 reference step intervals are embedded, so **no CrossTask download is
needed**.

There are three levels, in increasing cost.

### 1. Verify the analysis offline — instant, no API key, no GPU

```sh
python3 repro_truncation.py --analyse reference/mga-output-i2JWkDyg26A-mf64.jsonl
```

This reads the run output committed to this repo, so it proves the finding is
reproducible from published data before anyone spends GPU time. Abridged output:

```
  6 steps emitted:
    [  16.0 -   25.0s]  Set up a warning triangle and turn on the hazard lights.
    ...
    [  66.0 -   85.0s]  Place the jack under the vehicle's jacking point and raise the car

  SYMPTOM 1 — coverage stops well before the video ends
    last prediction ends   : 85.0s
    video duration         : 173.2s
    uncovered tail         : 88.2s (51% of the video)
    => CONFIRMED

  SYMPTOM 2 — reference steps 5-11 get no prediction
    step  5 unscrew wheel      89.0-91.7    NO PREDICTION
    ... (steps 6-11 likewise)
    missed: [5, 6, 7, 8, 9, 10, 11]  (7/11 reference steps)
    => CONFIRMED (exactly steps 5-11, as filed)

  SYMPTOM 3 — final step is cut mid-clause (the token-cap signature)
    step 4: ends '.'
    step 5: ends 'r'  <-- no terminal punctuation
    => CONFIRMED (all earlier steps terminate cleanly; the last does not)

  generated volume: 6 steps, 83 words, 441 chars ~= 167 tokens

3/3 symptoms confirmed.
```

Symptom 3 is the one that identifies the *cause*. A model that had finished would
end with a full stop like the five steps before it; one that ran out of budget
stops mid-clause. Swap in `reference/mga-output-i2JWkDyg26A-mf32.jsonl` to see the
same three symptoms from a different generation.

### 2. Reproduce it live against dev — ~15 minutes

```sh
printf 'ATAI_API_KEY=<dev key>\nATAI_API_ENDPOINT=https://api.dev.u1.archetypeai.app\n' > .env
python3 repro_truncation.py
```

It fetches the video if missing, uploads, creates the bundle, runs, streams
`/logs`, downloads the output and analyses it. Every run opens by stating exactly
what it is sending, so a pasted log is unambiguous:

```
CONFIGURATION
  video           data/clips/tire_i2JWkDyg26A.mp4
  blueprint       mga  (canonical)
  max_frames      64          (blueprint default is 16; 64 is the reader/preprocessor batch size)
  max_new_tokens  not sent   (reproduces the as-shipped behaviour; the mga
                             blueprint exposes no generation parameter at all)

  bundle values sent: {"max_frames": 64}
```

Because generation is deterministic, a correct reproduction returns the committed
baseline digest `080e551a88f0bdcd857e48557366ae60` and the analysis is identical
to level 1.

The script also checks the **input** first, because an output digest only means
something if the input bytes match:

```
using data/clips/tire_i2JWkDyg26A.mp4 (5.7 MB)
  video md5 5771f46d637314fdad86d6c4c934dbb5  (matches the reference input)
```

If YouTube ever offers different formats and the video md5 differs, the script
says so and notes that the three symptoms should still reproduce while the output
digest legitimately will not — so a digest mismatch is not mistaken for a failed
reproduction.

### 3. Show that `max_new_tokens` has no effect — ~15 minutes

```sh
python3 repro_truncation.py --max-new-tokens 1024
```

One run is enough, because the baseline is committed and generation is
deterministic: if the digest still matches the baseline that omitted the value,
the value changed nothing.

```
Did max_new_tokens=1024 change anything?
  this run   md5 080e551a88f0bdcd857e48557366ae60
  baseline   md5 080e551a88f0bdcd857e48557366ae60   (no max_new_tokens)
  => IDENTICAL. max_new_tokens was accepted by the API (HTTP 201, echoed back in
     the bundle values) and had NO effect.
```

If a blueprint change later wires the value through, this prints
`OUTPUTS DIFFER` instead — so it doubles as the regression check for the fix.

### Other flags

```sh
python3 repro_truncation.py --download-only     # fetch the video, make no API calls
python3 repro_truncation.py --max-frames 32     # a different sampling density
python3 repro_truncation.py --video path.mp4    # use your own clip
```

Note that `--max-frames` other than 64 skips the digest comparison in level 3 with
an explanation, since the committed baseline was produced at 64.

## Platform behavior worth knowing

- **The bundle endpoint is plural.** `POST /agents/bundle` → **404**; use
  `POST /agents/bundles`. The clients committed in the RED and OSM example repos
  use the singular form and **fail against current dev**.
- **Starting a run returns 202, not 201.** Treat only 201 as success and you will
  report a failure while the agent runs unattended, burning GPU with nothing
  collecting its output.
- **Poll `/logs`, not `/events`.**
  `GET /agents/instances/{id}/logs` is the stream the console shows — model load
  timings, per-input progress, the real error. `/events` returns only
  "run started" and "dispatched to JOS".
- **The instance `status` field lies in both directions.** It stayed `running`
  20+ minutes after a pod terminated with `exit=1`, and `running` after a job had
  completed. Judge terminality from the log stream. (RED documents the inverse:
  `failed` hiding a success.)
- **Cold start is ~7.5 min per run and is never cached.** whisper ~35 s download
  + 4 s load; **newton-fusion ~5 min download + 1 min 40 s load**. Measured on all
  6 runs. Total wall clock was 877–883 s for a 173 s video.
- **Dev serializes these jobs.** Four concurrent submissions produced queue waits
  of **799–817 s**, not parallelism. Budget a sweep as the sum of its runs.
- **Videos over ~5 minutes fail.** A 563 s video at default settings ran 5 min
  13 s then died: `pipeline execution failed: Newton generation event stream
  closed without a terminal event`, exit 1. Note this is *not* a context limit —
  at `max_frames: 16` the video occupies 392 visual tokens, **0.15% of the 256K
  window**.
- **`max_frames` has no validation at all.** 16, 64, 256, 501, 512, 1024 and `-1`
  (keep every frame) were all accepted with `status: ready`. The arithmetic
  ceiling from `preprocessor_max_pixels` (24 Mi at `size: 224`) is 501 frames and
  the API will not enforce it.
- **No progress events during processing.** Between `input_started` and the
  terminal event there are zero log rows across 5–7 minutes — no frame counts, no
  transcript length, no per-node timing. Neither the failure nor the time spent
  can be attributed.

## Where the deployment diverges from the design doc

Observations for the author, not defects.

- **Audio is in V1.** The doc says F1-0 adds audio in a later version; the
  blueprint runs `whisper:large-v3` into `prompt.audio`. Audio is used — via ASR,
  not fusion-native. The generated step *"remove the hubcap if the wheel has one"*
  reads like narration, suggesting the transcript does reach the model.
- **There is no runtime instruction.** The doc describes an algorithm that "takes
  an input instruction and video" and mentions passing the SOP at runtime. `mga`
  sets `text_extensions: []` with a fixed `default_text`, and its own comment
  reads *"Manual generation has no SOP: this fixed instruction is used for every
  video."* By contrast `tva`'s source config is `{}` and its instruction arrives
  from a `prepare` node — **TVA accepts a reference procedure at runtime and MGA
  deliberately does not.**
- **Context is not the binding constraint.** The doc motivates its preprocessing
  work by videos overflowing the context window. In practice the constraints are
  the output budget and the ~5-minute ceiling, at 0.15–0.6% context utilization.
- **The model is fp8** — `f1_0_35b_a3b_fp8_base_040625b4d0750b`, confirming the
  35B/3B-active MoE description and adding quantization the doc does not mention.

## Repo layout

| Path | What it is |
|---|---|
| `repro_truncation.py` | **Minimal reproduction** of the missing predictions for reference steps 5-11, reporting each symptom as CONFIRMED / NOT REPRODUCED. Self-contained (reference intervals embedded). `--analyse` works offline; `--max-new-tokens 1024` shows that value has no effect. See [Reproducing the truncation](#reproducing-the-truncation). |
| `run_mga_example.py` | **Start here.** Single-file, stdlib-only end-to-end run: upload → bundle → run → watch `/logs` → download → print. Verifies the reference digest. |
| `blueprints/mga-tuned.yaml` | The blueprint that exposes `max_new_tokens` and the instruction. Needs an admin to register. |
| `reference/` | The committed reference output and a snapshot of the `mga` blueprint document that produced it. |
| `mga_api.py` | Shared stdlib client (streaming upload for large videos, network retries, `/logs` polling). |
| `inspect_bundle.py` | Discovery: dumps a bundle, its blueprint, its runs, their logs and outputs. How the contract in this README was established. |
| `probe_max_frames.py` | Creates bundles across a `max_frames` ladder and optionally runs them. Phase 1 is free and needs no GPU. |
| `watch_runs.py` | Watches runs via `/logs` with log-based terminality. Use this rather than polling `status`. |
| `poll_probe.py` | Attaches to already-launched runs by agent id and compares their outputs. |
| `docs/` | The MGA design doc this example implements. |

## Not yet built

- **The scorer.** Step alignment (semantic, since MGA emits prose against terse
  reference labels) plus temporal IoU, reported against the sampling floor.
  Blocked on the token cap — see Status.
- **A `max_frames` sweep.** 16 / 32 / 64 / 128 / 256, to measure how sampling
  density affects localization. Deferred on evidence, not guesswork: 32 vs 64
  changed the wording but produced the same 6 steps, the same 4 covered reference
  steps and the same 7 missed, so the sweep cannot measure anything until the
  token cap is lifted.
- **Chunking for long videos.** `manual-gen-jos` splits into 90-second
  overlapping chunks with timestamp offsetting and dedup across overlaps. Worth
  deciding whether that belongs in the blueprint rather than in every caller.

## Data attribution

Annotations and step taxonomies come from **CrossTask**:

> D. Zhukov, J.-B. Alayrac, R. G. Cinbis, D. Fouhey, I. Laptev, J. Sivic.
> "Cross-task weakly supervised learning from instructional videos", CVPR 2019.
> https://github.com/DmZhukov/CrossTask · https://arxiv.org/abs/1903.08225

The CrossTask repository is **BSD-3-Clause**. The dataset ships annotations and
YouTube IDs — 2,750 annotated videos across 18 primary tasks, with segments as
`<step>,<start_sec>,<end_sec>`. **The videos themselves are not distributed by
the authors and are copyrighted by their uploaders**; this repo commits neither
the videos nor the annotation dump, only the code that fetches them. For videos
YouTube has since removed, the authors host a
[mirror](https://www.rocq.inria.fr/cluster-willow/dzhukov/missing_videos.tar.gz).

If you select your own videos, filter for duration: at least 26% of CrossTask
exceeds the ~5-minute ceiling by annotated span alone, which is a lower bound
since 72% of each video is background.
