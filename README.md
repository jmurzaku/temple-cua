# TempleOSBench

Computer-use tasks for [TempleOS](https://templeos.org/), created by Terry A. Davis.
An agent sees screenshots, types HolyC, and controls the mouse in a QEMU VM.
HolyC runs at ring 0 in a shared address space.[¹](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/Features.DD)

[Website](https://jmurzaku.github.io/temple-cua/) · [Tasks](tasks/) · [Recorded run](examples/cua-starter/results.json)

## Run

Requires Linux, [uv](https://docs.astral.sh/uv/getting-started/installation/), and
QEMU (`sudo apt install qemu-system-x86 qemu-utils`).

```sh
git clone https://github.com/jmurzaku/temple-cua.git
cd temple-cua
uv sync
uv run temple-cua fetch-iso
uv run temple-cua prepare --output assets/baseline.qcow2
```

Inspect `runs/prepare/ready.png`. Set `OPENAI_API_KEY`, then run a task:

```sh
uv run temple-cua run --provider cua --model openai/gpt-6.1-sol \
  --baseline assets/baseline.qcow2 --boot-wait 1 \
  --task cursor_callback --output runs/cursor
```

Use an exact model ID available to your account. Anthropic uses
`anthropic/<model-id>` and `ANTHROPIC_API_KEY`. Each task restores the same
snapshot; results, screenshots, and actions go into `runs/`.

## Tasks

The site features three tasks: arithmetic, a live cursor callback, and a
[live counter panel](tasks/08_interactive_counter_panel.yaml). The panel requires
building clickable controls, then using them
to increment, decrement, and reset shared state while its draw callback runs.
[Cursor callback](tasks/07_cursor_callback.yaml) asks the agent to compile a draw
function, install `Fs->draw_it=&Cross`, and follow a document link while its cross
keeps tracking the pointer. The hook runs on the guest's refresh path.[²](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Demo/Graphics/WinZBuf.HC)

```sh
uv run temple-cua list-tasks
uv run temple-cua new-task my-task
```

Edit the generated prompt and rubric, then run it with `--task my-task`.
For a visible-text check, create a task with an expected output:

```sh
uv run temple-cua new-task multiply \
  --prompt 'Evaluate 6 * 7 in HolyC and print ANSWER=<result>.' --expect ANSWER=42
```

Tasks are YAML files; no registry or Python edits. Text checks score visible
output. Callback and other GUI tasks require trajectory review. Edit
`site/tasks.yaml` to choose the featured tasks. Additional basics remain in
`tasks/`; extra GUI tasks can be selected with `--tasks tasks/extra`.

## Results

One GPT-6.1 Sol/Cua 0.9.0 run passed all six basic visual checks in 17 model calls.
The site shows the arithmetic recording. No model result is recorded for either
of the two interactive tasks.
Each result is one attempt scored from visible output.

```sh
uv run pytest
uv run python scripts/build_site.py
```

TempleOS is public domain; this harness is [MIT licensed](LICENSE). The ISO is
downloaded from TempleOS.org and checksum-verified. VM images and API keys stay
local. See the [HolyC documentation](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/HolyC.DD) and
[graphics source](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/Gr/GrScrn.HC).
