# TempleOSBench

Computer-use tasks for [TempleOS](https://templeos.org/), created by Terry A. Davis.
An agent sees screenshots, types HolyC, and controls the mouse in a QEMU VM.
HolyC runs at ring 0 in a shared address space.[¹](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/Features.DD)

[Website](https://jmurzaku.github.io/temple-cua/) · [Tasks](tasks/) · [Recordings](examples/)

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
`anthropic/<model-id>` and `ANTHROPIC_API_KEY`. `--baseline` restores a prepared
snapshot; results, screenshots, and actions go into `runs/`.

The UART task requires a fresh boot and configures COM1:

```sh
uv run temple-cua run --provider cua --model openai/gpt-6.1-sol \
  --task uart_irq_service --output runs/uart
```

## Tasks

The site features five tasks: arithmetic, a live cursor callback, a
[live counter panel](tasks/08_interactive_counter_panel.yaml), a game sprite,
and a UART interrupt service.
The panel requires clickable controls that increment, decrement, and reset
shared state while its draw callback runs.
[Cursor callback](tasks/07_cursor_callback.yaml) asks the agent to compile a draw
function, install `Fs->draw_it=&Cross`, and follow a document link while its cross
keeps tracking the pointer. The hook runs on the guest's refresh path.[²](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Demo/Graphics/WinZBuf.HC)

[Add a sprite to Tic-Tac-Toe](tasks/09_tictactoe_sprite.yaml) asks the agent to
create a robot sprite, integrate it into the [bundled game](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Demo/Games/TicTacToe.HC),
play it with real clicks, and relaunch the saved version. Select it with
`--task tictactoe_sprite`. TempleOS supports both [sprite editing](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/Sprite.DD)
and [sprites built in HolyC](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Demo/Graphics/SpriteRaw.HC).

[Live kernel interrupt handler](tasks/10_uart_irq_service.yaml) asks the agent
to build a COM1 request/reply service using HolyC and a real interrupt handler.
Ten checks run after the model episode and yield a 0–1 reward: eight binary
serial protocol cases, an IRQ4 mask/unmask intervention, and cleanup with
state restoration. Host inputs are separate from the model replay. These
behavioral checks give partial credit; proving interrupt-only code still
requires source review. TempleOS exposes [interrupt entry installation](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Kernel/KInts.HC#L109)
and [PIC control](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Kernel/KInts.HC#L129)
directly to HolyC. Select this task with `--task uart_irq_service`.

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

GPT-6.1 Sol through Cua 0.9.0 completed the live counter panel in 26 calls
(227 seconds). It compiled the cursor callback but exhausted 40 calls
(209 seconds) without opening the document. The [featured sprite rerun](examples/cua-sprite-rerun/)
exhausted 100 calls (769 seconds) without creating a sprite or launching the
modified game. These GUI results have separate assistant trajectory reviews;
their raw manual grades remain unscored.

The [original sprite attempt](examples/cua-sprite/) stopped after 24 calls
(159 seconds) when the adapter rejected a scroll request. The rerun used
protocol v2 with recoverable input errors and scroll distances mapped to
TempleOS's eight-pixel text rows; it recorded no adapter rejections. The
earlier starter run passed six basic visual checks in 17 calls. The site shows
one featured attempt per task; earlier recordings remain in `examples/`.

```sh
uv run pytest
uv run python scripts/build_site.py --run examples/cua-cursor \
  --run examples/cua-counter --run examples/cua-sprite-rerun
```

TempleOS is public domain; this harness is [MIT licensed](LICENSE). The ISO is
downloaded from TempleOS.org and checksum-verified. VM images and API keys stay
local. See the [HolyC documentation](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/HolyC.DD) and
[graphics source](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/Gr/GrScrn.HC).
