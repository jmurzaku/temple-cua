# Harness reference

The default Cua integration uses `cua-agent==0.9.0`, `ComputerAgent`, and LiteLLM.
A custom computer handler exposes the TempleOS framebuffer and QMP keyboard/mouse
input. The OpenAI compatibility adapter translates Responses function calls into
Cua computer actions. It retains upstream inference and dispatch.

## Providers

`--provider cua` accepts an exact `openai/<model-id>` or `anthropic/<model-id>`.
OpenAI needs `OPENAI_API_KEY`; Anthropic needs `ANTHROPIC_API_KEY`. Live Anthropic
compatibility through Cua has not been tested. `--base-url` overrides the API root.

The original adapters remain available as `--provider openai` and
`--provider anthropic`, with unprefixed model IDs. They use a shared `act` tool.
`--history-steps` controls their text history; Cua uses `--cua-image-history`.

`--provider scripted --script PATH` replays a reference against one selected task.
A Cua fixture exercises its agent loop without making a model request:

```sh
.venv/bin/temple-cua run --provider cua --model openai/fixture \
  --cua-fixture scripts/reference/arithmetic.json --task arithmetic \
  --baseline assets/baseline.qcow2 --boot-wait 1 \
  --max-steps 8 --timeout 120 --output runs/cua-fixture
```

## Budgets and artifacts

`--max-steps` and `--timeout` override task defaults. Cua counts each inference
attempt as a step, disables automatic retries, and allows at most four computer
calls in a response. The task timer starts after VM setup. `--api-timeout` caps
a request; `--max-output-tokens` caps output; `--cua-image-history` retains 1–12
recent screenshots. Cua's standard wait lasts one second.

Each run writes `results.json` and `report.html`. Task folders contain the task
configuration, `trajectory.jsonl`, initial and numbered PNGs, `final.png`, grader
evidence, and a disposable VM disk. Usage comes from provider callbacks; recorded
costs are LiteLLM estimates. Output directories must be new.

```sh
.venv/bin/temple-cua report runs/cua-starter
.venv/bin/temple-cua compare runs/cua-starter runs/another-run
```

For comparisons, keep task definitions, baseline, budgets, and history settings
equal. The comparison output lists scored coverage and mismatched metadata.
Manual review and execution errors are reported separately from visual grades.

## VM and screenshots

Preparation dismisses the stock ISO's install/tour prompts, maximizes the shell
at `::/Home`, disables autocomplete, and sets one-pixel pointer clipping. The
boot drive is read-only; task files use the stock `B:` RAM drive. Snapshot
sidecars pin the ISO, QEMU, memory, devices, and snapshot hash.

QEMU runs with guest networking disabled. The model gets screenshots and input
actions, without a host shell, guest file API, or accessibility API. The transport
tag is `linux` because Cua has no TempleOS tag; commands still execute HolyC.

QMP captures the framebuffer as PPM, then the harness saves PNG. Raw observation
pixels are preserved. OCR uses separate cropped/resized images; `final.png` is
a later capture and may differ as the clock, cursor, or guest animation changes.

On the original build machine, `--qemu ./scripts/qemu-local` uses an unbundled
local QEMU installation. Use installed QEMU on other machines. The
[TEST ONLY callback demo](CURSOR_CALLBACK.md) changes the guest's drawing callback;
it is not a host-side alteration of recorded images.
