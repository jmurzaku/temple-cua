# TempleOSBench

Screenshot-based computer-use tasks for TempleOS 5.03. The harness runs QEMU,
restores the same VM snapshot before each task, and records model actions,
screenshots, usage, and grades. The Python package and CLI remain `temple-cua`.

[Tasks](docs/TASKS.md) · [Recorded results](docs/CUA_RERUN.md) ·
[Harness reference](docs/CUA_AGENT.md) · [Website source](site/)

GitHub Pages target: https://jmurzaku.github.io/temple-cua/

## Install

Use Linux, Python 3.11–3.13, and QEMU. Tesseract is optional for nonstock fonts.

```sh
sudo apt-get install qemu-system-x86 qemu-utils tesseract-ocr
python -m venv .venv
.venv/bin/pip install -e '.[cua,dev]'
.venv/bin/temple-cua fetch-iso
.venv/bin/temple-cua doctor
```

The ISO download is checksum-pinned. VM images and API credentials are not
included in the repository. Set `OPENAI_API_KEY` in your environment to run
an OpenAI model.

## Prepare and run

```sh
.venv/bin/temple-cua prepare --output assets/baseline.qcow2
```

Inspect `runs/prepare/ready.png` before proceeding. Keep the snapshot's
`.qcow2.json` sidecar; restores require matching QEMU, ISO, memory, and devices.

Run the six automatically graded tasks with `cua-agent==0.9.0`:

```sh
.venv/bin/temple-cua run \
  --provider cua --model openai/gpt-6.1-sol \
  --baseline assets/baseline.qcow2 --boot-wait 1 \
  --task arithmetic --task sum_of_squares --task triangular_function \
  --task string_statistics --task directory_navigation --task file_roundtrip \
  --max-steps 24 --timeout 300 --api-timeout 90 \
  --max-output-tokens 4096 --cua-image-history 2 --output runs/cua-starter
```

Use an exact model ID available to your account. Open `runs/cua-starter/report.html`
to inspect the result. The four additional desktop tasks require manual review.
Automatic scores check visible output, not hidden guest state or computation method.

## Recorded run and checks

The [recorded GPT-6.1 Sol run](examples/cua-starter/results.json) completed all
six tasks and passed their visual checks in 17 model calls. Screenshots and
sanitized trajectories are in [examples/cua-starter](examples/cua-starter/).

Run an offline backend check or the test suite:

```sh
.venv/bin/temple-cua run --provider scripted \
  --script scripts/reference/arithmetic.json --task arithmetic \
  --baseline assets/baseline.qcow2 --output runs/arithmetic-smoke
.venv/bin/python -m pytest
```

The [cursor callback demo](docs/CURSOR_CALLBACK.md) is an opt-in test, outside
the measured suite. [Cua compatibility research](research/cua/README.md) and
[UART experiments](moonshots/uart/README.md) are separate from the starter tasks.
