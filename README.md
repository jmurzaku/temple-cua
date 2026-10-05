# TempleOS computer use

Run small screenshot-based tasks in TempleOS with the real Cua ComputerAgent
loop and a QEMU/QMP computer adapter. Start with arithmetic, a loop, a function,
string operations, directory navigation, and a file round trip. VM reset,
budgets, evidence and grading remain outside the agent.

The [recorded GPT-6.1 Sol starter run](examples/cua-starter/report.html) completed
all six tasks and passed their visual checks in 17 model calls. This is one
smoke run, not a stable pass-rate estimate. See the
[measured results](docs/CUA_RERUN.md) and [Cua setup](docs/CUA_AGENT.md).

[Research website](https://ring-zero-temple-lab.yurpl.chatgpt.site) ·
[website source](site/README.md)

The port uses released `cua-agent==0.9.0`, preserving exact provider-prefixed
model IDs such as `openai/gpt-6.1-sol`. A small compatibility bridge adapts
Responses function calls to Cua's computer actions. Native OpenAI and Anthropic
adapters are also available. API keys belong in your environment or secret
manager; they are not included in this repository.

The earlier [ring-0 UART experiment](moonshots/uart/README.md) remains separate,
with its reference implementation and pilot findings. The website starts with
the six simple tasks.

Run screenshot-based computer-use tasks in a disposable TempleOS VM, using the
same actions with OpenAI or Anthropic. Every model turn produces a screenshot,
validated keyboard/mouse actions, a JSONL trajectory, and token/latency metadata.
The runner restores the same RAM+disk baseline before each task and emits an
HTML report plus machine-readable results.

Model API IDs are explicit inputs. The harness does not assume that
`gpt-6-Astra` or `Claude-opus-5.5` are available names in your API account. Supply
the exact IDs your provider exposes; inaccessible models fail visibly.

## Install

Linux, Python 3.11+, and QEMU (`qemu-system-x86_64` and `qemu-img`) are required.
The stock TempleOS font is decoded exactly; Tesseract supplies an optional OCR
fallback for other frames. Hardware virtualization is optional; the default
backend uses TCG, so it also runs in a cloud container without `/dev/kvm`.

```sh
# Debian/Ubuntu, on a machine where you administer packages:
sudo apt-get install qemu-system-x86 qemu-utils tesseract-ocr

python -m venv .venv
.venv/bin/pip install -e '.[cua,dev]'
.venv/bin/temple-cua fetch-iso
.venv/bin/temple-cua doctor
```

`fetch-iso` pins the official TempleOS 5.03 ISO SHA-256 and refuses to overwrite
an existing image with a different checksum. The upstream OS is obtained
separately; it is not bundled with this source project.

## Prepare a common starting state

```sh
.venv/bin/temple-cua prepare --output assets/baseline.qcow2
```

Inspect `runs/prepare/ready.png`: the selected shell should be maximized at the
boot drive's `/Home` with no install/tour dialog. Preparation answers `n` to the
stock live ISO's two prompts, disables autocomplete, sets one-pixel pointer
clipping, and normalizes the shell. The stock ISO provides a
writable `B:` RAM drive for task files. `--boot-wait 30` can help slower hosts.
Use `--script scripts/reference/shell_ready.json` to inspect/override trusted
preparation. For a different image, write an appropriate preparation script.

The baseline is a qcow2 file containing an internal disk+RAM snapshot. Use the
same QEMU version, ISO, memory, and device configuration when restoring it.
The `.qcow2.json` sidecar pins these settings and file hashes; keep it with the
baseline so incompatible restores fail early.
Never reuse a task's mutated session disk as the baseline.

## Validate without API credentials

```sh
.venv/bin/temple-cua list-tasks
.venv/bin/temple-cua run \
  --provider scripted --script scripts/reference/arithmetic.json \
  --baseline assets/baseline.qcow2 --task arithmetic \
  --output runs/arithmetic-smoke
```

Open `runs/arithmetic-smoke/report.html`. This is a deterministic backend and
grader smoke check, not evidence of model performance. Each task has a step
budget and a time budget; boot/reset time is recorded separately.

In the original build workspace, QEMU was extracted locally because system
installation was unavailable. Use `--qemu ./scripts/qemu-local` for that machine;
the wrapper depends on `.runtime/qemu`, which is not included in source exports.
Use the normal installed QEMU binary elsewhere.

## Run models

For the Cua starter suite, set `OPENAI_API_KEY` and run:

```sh
.venv/bin/temple-cua run \
  --provider cua --model openai/gpt-6.1-sol \
  --baseline assets/baseline.qcow2 \
  --task arithmetic --task sum_of_squares --task triangular_function \
  --task string_statistics --task directory_navigation --task file_roundtrip \
  --max-steps 24 --timeout 300 --output runs/cua-starter
```

Only these six tasks are included in that command. The four additional desktop
tasks in `tasks/` use manual review; select them explicitly when ready.

The following commands use the original provider adapters:

Set `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` in your environment or secret
manager, then set the exact model IDs available to your account:

```sh
export OPENAI_MODEL='your-openai-model-id'
export ANTHROPIC_MODEL='your-anthropic-model-id'

.venv/bin/temple-cua run \
  --provider openai --model "$OPENAI_MODEL" \
  --baseline assets/baseline.qcow2 --output runs/openai-eval

.venv/bin/temple-cua run \
  --provider anthropic --model "$ANTHROPIC_MODEL" \
  --baseline assets/baseline.qcow2 --output runs/anthropic-eval

.venv/bin/temple-cua compare runs/openai-eval runs/anthropic-eval
```

Select tasks with repeated `--task ID`. For controlled comparisons, use the same
baseline, task set, `--max-steps`, `--timeout`, `--max-output-tokens`, and
`--history-steps` for both providers. `--api-timeout` caps an API turn including
retries; `--base-url` supports a compatible gateway. `--boot-wait` also applies
after restoring a snapshot; it can usually be reduced to 1 for a prepared VM.

The adapters use standard vision inputs and a common `act` function tool through
OpenAI Responses and Anthropic Messages. They do not require vendor-specific
computer-use beta tools. Each request contains the current 640×480 screenshot
and a bounded history of prior actions. The model receives only the task prompt,
not the private grader or reference script. A model sees no host filesystem or
QMP shell. A `done` action stops a run; the model's statement never assigns a pass.

## Tasks and scoring

The ten sample tasks cover arithmetic, loops, functions, strings, directory
navigation, RAM-drive files, the editor, FileMgr, documentation, and graphics.
See [task details and rubrics](docs/TASKS.md).

Six tasks use exact text checks on the final framebuffer, with a reader for
TempleOS's stock bitmap font and a Tesseract fallback. These scores
are visual outcome evidence: they can be fooled by literal output and do not
prove execution method or guest file contents. Four GUI tasks return
`needs_review`, with concrete rubrics. Human review is separate from automatic
scores. Results retain run errors, budget exhaustion, and timeouts separately
from grading outcomes; do not report a reference script or pending review as a
model success.

Create new task YAML files in `tasks/`; `version`, `id`, `title`, `prompt`, and
`grader` are required. Optional `setup` actions are executed by the trusted
runner before the model receives the first screenshot. Graders support `ocr`
(`all`, optional `any`, `case_sensitive`, and `[x,y,width,height]` crop) or
`manual` (`rubric`). Run `list-tasks --tasks /path/to/tasks` to validate them.

## Artifacts and controls

```text
runs/<run>/
  results.json                 # task statuses, grades, budgets and usage
  report.html                  # standalone report with linked screenshots
  <task>/
    task.json                  # evaluator configuration, kept host-side
    trajectory.jsonl           # actions, execution results, notes and timings
    0000.png, 0001.png, ...     # initial and post-turn screenshots
    final.png
    grader-ocr.txt              # OCR evidence for automatic tasks
    result.json
    vm/qemu.log
    vm/session.qcow2           # disposable task disk; can be removed after review
```

Actions: `type` (US ASCII), `key` (a chord such as `["ctrl","s"]`), `move`,
`click`, `scroll`, `wait`, `done`. Screenshots are implicit after each turn.
Pointer coordinates refer to framebuffer pixels. The PS/2 backend anchors the
pointer at a screen edge and translates coordinates into relative movement;
scroll support can depend on the guest driver. Screen output is captured
directly through QMP, with no desktop server required. Guest networking is
disabled. Each run copies a source disk/baseline and leaves its source untouched.

```sh
.venv/bin/python -m pytest
.venv/bin/temple-cua report runs/openai-eval
```

The unit suite checks action bounds, model request/response handling, QMP framing,
grading, reporting, and runner lifecycle. Live API runs need credentials and
account access; they are not part of offline validation.

The [included validation report](examples/validated-run/report.html) contains
actual screenshots and trajectories from six automatic reference tasks and two
manual reference tasks, replayed in TempleOS. The GUI tasks retain review status.
These runs demonstrate the harness, not model performance.
