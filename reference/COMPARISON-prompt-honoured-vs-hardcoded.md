# The hardcoded instruction costs safety steps

Same video, same prompt sent, same `max_frames`. The only difference that matters is
whether the blueprint honours the caller's instruction.

## Configuration

| | run A | run B |
|---|---|---|
| agent | `agt_19h1xhqevz9mha26h7b5xaaeyc` | `agt_4kbx60eqhx9gp8r4be2kq682qv` |
| blueprint | `blp_76kyqm4vjp9pt8tvfz8tks7x6t` | `blp_5wtsdwmp2q9hm87pabye2h9c1a` |
| `prompt` | sent, **honoured** | sent, **ignored** |
| instruction in force | the caller's (below) | `"Generate a concise, ordered list of distinct steps with 10 steps or less."` |
| `max_new_tokens` | 2048 | 4096 |
| `max_frames` | 64 | 64 |
| **steps produced** | **19** | **10** |

Run B had **twice the token budget and produced half the steps**, so the ceiling is
the instruction, not the budget. At the blueprint's own default of 2048 the same
configuration returns **zero steps** with `job.completed` and no error
(`reference/mga-output-i2JWkDyg26A-NEWBP-mnt2048-EMPTY.json`, 39 bytes).

The instruction both runs sent — the example's default until 2026-08-20, kept
here as the record of what these runs used, not as current guidance:

> Generate a concise, ordered list of every distinct step performed in this video,
> covering the procedure from the first action to the last. Include brief steps and
> steps that are repeated. Use up to 20 steps. Keep each step to at most 15 words.
> Use both what is shown and what is said. Do not invent steps that are not shown
> or stated in the video.

## Safety instructions are dropped, not merged

Three steps in run A appear nowhere in run B. Two are spoken safety cautions —
content that comes from the narration, which is what the whisper stage exists to
capture:

```
DROPPED  "Never put any object or body part under the vehicle when using the jack."
DROPPED  "Do not touch the disc wheels or brakes as they can be hot."
DROPPED  "If nuts are tight, use your foot to help loosen them."
```

## Everything else is compressed, not lost

Actions bundled into a single step — run A: **1.6 average, 4 maximum**; run B: **3.2 average, 7 maximum**.

The first 24 seconds, both runs:

```
B [ 11- 35s]  Stop the car on a firm level surface, set up a warning triangle, turn on hazard lights, apply the parking brake, switch off the ignition, and ensure passengers exit.

A [ 11- 21s]  Stop the car safely on a firm level surface away from traffic.
A [ 21- 25s]  Set up a warning triangle and turn on the hazard lights.
A [ 25- 30s]  Apply the parking brake and put the gear in Park.
A [ 30- 35s]  Switch off the ignition and ensure all passengers exit the vehicle.
```

Coverage is nearly identical (A 11–139 s, B 11–135 s of 173 s), which is why this is
easy to miss: the manual looks complete. But an operator cannot tick off "apply the
parking brake" independently of "switch off the ignition", and each step gets one
frame thumbnail however many actions it contains.

## Where the limit comes from

`blp_5wtsdwmp2q9hm87pabye2h9c1a`, as authored:

```yaml
connectors:
  source:
    key: MultiModalSource
    config:
      text_extensions: []
      # Manual generation has no SOP: this fixed instruction is used for every video.
      default_text: "Generate a concise, ordered list of distinct steps with 10 steps or less."

graph:
  edges:
    # The source's fixed `default_text` instruction is fed straight into the prompt.
    - { from: source.text, to: prompt.instruction }
```

## The ask

Two lines, and the safety property is untouched:

```yaml
values:
  prompt: "Generate a concise, ordered list of distinct steps with 10 steps or less."

connectors:
  source:
    config:
      text_extensions: []          # unchanged — no uploaded file becomes the instruction
      default_text: "${values.prompt}"
```

`text_extensions: []` stays, so the concern its comment records — *"a stray text file
can't quietly become the instruction for the whole run"* — still holds. Only the
caller, who already controls `max_new_tokens` and `size`, gains control of the
instruction.

Separately: `max_new_tokens: 2048` is the blueprint's own default and produces an
empty manual on this 2:53 video. A default that silently yields nothing is worth
attention independently of the prompt question.
