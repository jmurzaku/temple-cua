# Run TempleOS with the real Cua agent

This optional port uses released `cua-agent==0.9.0`, its `ComputerAgent` loop,
and upstream LiteLLM inference. The existing TempleOS QEMU/QMP backend supplies
the computer interface. Task setup, frozen resets, time/step budgets, artifacts,
and independent visual grading remain in this harness.
The pinned optional dependency supports Python 3.11 through 3.13.

## Install and run

```sh
.venv/bin/pip install -e '.[cua,dev]'

# Set OPENAI_API_KEY in your environment or secret manager first.
.venv/bin/temple-cua run \
  --provider cua --model openai/gpt-6.1-sol \
  --baseline assets/cli-baseline.qcow2 \
  --task arithmetic --max-steps 24 --timeout 300 \
  --output runs/cua-arithmetic-live
```

Use `assets/baseline.qcow2` if that is your prepared baseline. In the original
workspace add `--qemu ./scripts/qemu-local`; elsewhere use installed QEMU.
Select more tasks with repeated `--task ID`. Every task restores a fresh copy
of the same snapshot. The exact model ID is preserved: the harness never
substitutes a model or implies that an account has access to a particular ID.

For Anthropic use `--model anthropic/<exact-model-id>` and `ANTHROPIC_API_KEY`.
The port exposes the same backend through Cua's upstream Anthropic loop; live
Anthropic compatibility has not been verified. `--base-url` overrides the API
root. `--api-timeout` caps each request; the episode timeout also cancels the
async inference loop. `--cua-image-history 2` bounds screenshot context.

## Reproduce the local smoke without credentials

```sh
.venv/bin/temple-cua run \
  --provider cua --model openai/fixture \
  --cua-fixture scripts/reference/arithmetic.json \
  --baseline assets/cli-baseline.qcow2 \
  --task arithmetic --max-steps 8 --timeout 120 \
  --output runs/cua-arithmetic-fixture
```

This runs the actual `ComputerAgent`, computer handler, action dispatch,
screenshots, TempleOS VM and grader, while replacing only the upstream network
inference function with deterministic Responses-format output. It sends no
model requests. Results explicitly say `deterministic_fixture` and `no model
API`, with zero tokens; these artifacts are backend evidence, not a model score.
Cua 0.9.0's standard wait action lasts one second, so fixture wait durations use
that behavior. The arithmetic smoke passed in the build workspace on 2026-10-05:
three fixture turns, observed `BENCH_ARITH=391`, independent visual grade passed.

## Compatibility port

The released Cua OpenAI routing pattern does not include `gpt-6.1-sol`. Its
wildcard fallback is a Qwen-oriented loop with additional dependencies and
normalized coordinates. This port registers a compatibility subclass of the
existing upstream `OpenAIComputerUseConfig` for provider-prefixed OpenAI models.
It translates Cua's canonical computer actions and screenshot results into
Responses function calls, function results, and separate screenshot messages,
then translates returned function calls into Cua computer actions. Upstream
Cua and LiteLLM still perform inference and dispatch. Native
`computer-use-preview` models retain their upstream routing.
The compatibility parser projects recognized fields from the upstream flat
function schema onto the selected action, because Responses can include default
values for inactive fields. Unknown fields and invalid active arguments fail
before input is dispatched.

An explicit `CustomComputerHandler` wraps the async computer dictionary so
upstream schema generation sees the real 640x480 dimensions. Coordinates are
absolute screenshot pixels; the guest prompt explains HolyC and TempleOS.
Cua requires a `linux`, `windows`, `mac`, or `browser` environment tag, so the
adapter uses `linux` as a transport tag and records that the guest is TempleOS.
No guest daemon, host shell, file API, accessibility service or hidden execution
tool is exposed. Vertical scroll distances map to bounded PS/2 wheel notches.
Drag/mouse button state is released during errors, timeout and shutdown.

Automatic Cua retries are disabled. A step counts an actual inference attempt,
and at most four computer calls are allowed in a response. Separate limits bound
backend input actions and the episode's elapsed time. A paired-history callback
removes old screenshots with their corresponding call IDs, including action
batches, so screenshot pruning preserves valid provider message sequences.

## Evidence and limitations

Runs retain `results.json`, per-task `result.json`, `task.json`,
`trajectory.jsonl`, `0000.png`, one numbered frame per inference turn, detailed
`observations/` screenshots, `final.png`, and an HTML report. Provenance records
the installed distribution version, compatibility loop, exact model, dimensions,
execution mode, budget definitions and frozen baseline identity. The Python
module's stale `__version__` value is not used.

Token totals come from upstream usage callbacks once per request, including
attempts that end in action errors. Prices supplied by LiteLLM are estimates;
unknown or zero price entries are omitted rather than claimed as a zero-dollar
model charge. Product telemetry is disabled before importing Cua and when
constructing the agent. API arguments and credentials are never persisted.

Model completion text does not determine success. Existing text tasks use
heuristic framebuffer grading; that can match printed constants or source text
and does not prove guest execution. GUI tasks requiring subjective evidence
remain explicitly unscored until reviewed. The independent UART experiment is
separate from this simple-task port.

The optional tests exercise the real released `ComputerAgent` with mocked
network inference, exact model routing, valid tool history, coordinate mapping,
budget enforcement, failure cleanup and report provenance. No stock Cua VM
configuration, cloud sandbox, reinforcement learning, or closed-model training
support is implied by this port.
