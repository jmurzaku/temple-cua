# TempleOSBench

Computer-use tasks for [TempleOS](https://templeos.org/), created by Terry A. Davis.
An agent sees screenshots, types HolyC, and controls a QEMU desktop through
[Cua](https://github.com/trycua/cua). HolyC runs at ring 0 in a shared address
space.[¹](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/Features.DD)

[Website](https://jmurzaku.github.io/temple-cua/) · [Tasks](tasks/) · [Recordings](recordings/)

## Run

Requires Linux, [uv](https://docs.astral.sh/uv/getting-started/installation/), and
QEMU (`sudo apt install qemu-system-x86 qemu-utils`).

```sh
git clone https://github.com/jmurzaku/temple-cua.git
cd temple-cua
uv sync
uv run temple-cua fetch-iso
```

Set `OPENAI_API_KEY`, then run:

```sh
uv run temple-cua run --provider cua --model openai/gpt-6.1-sol \
  --task arithmetic --output runs/arithmetic
```

Use a model ID available to your account. Anthropic uses `anthropic/<model-id>`
and `ANTHROPIC_API_KEY`. Results, actions, and screenshots go into `runs/`.
The UART task uses a fresh boot with COM1 and allows 300 model calls / one hour:

```sh
uv run temple-cua run --provider cua --model openai/gpt-6.1-sol \
  --task uart_irq_service --output runs/uart
```

Override limits with `--max-steps` and `--timeout`. Calls can contain up to four
input actions; boot and host grading are outside the policy time limit.
For other tasks, `uv run temple-cua prepare --output assets/baseline.qcow2`
creates a reusable snapshot. Inspect `runs/prepare/ready.png`, then pass
`--baseline assets/baseline.qcow2 --boot-wait 1` when running. UART requires a
fresh boot, so omit `--baseline` for that task.

## Tasks

| Task | Goal | Scoring |
| --- | --- | --- |
| [Arithmetic](tasks/01_arithmetic.yaml) | Evaluate an integer expression | Visible output |
| [Cursor callback](tasks/07_cursor_callback.yaml) | Install `Fs->draw_it`, then navigate to a document | Trajectory review |
| [Counter panel](tasks/08_interactive_counter_panel.yaml) | Build working +1, −1, and RESET buttons | Trajectory review |
| [Game sprite](tasks/09_tictactoe_sprite.yaml) | Add a robot to Tic-Tac-Toe, play, and relaunch it | Trajectory review |
| [UART service](tasks/10_uart_irq_service.yaml) | Build an interrupt-driven COM1 driver with safe cleanup | Ten host checks |

UART grading checks actual serial replies, IRQ4 mask dependence, and state
restoration after repeated stops. It awards partial credit; printed success
claims earn none. Source review is still needed to establish an interrupt-only
implementation. [TempleOS exposes interrupt installation directly to HolyC.](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Kernel/KInts.HC#L109)

Create a task without editing Python or a registry:

```sh
uv run temple-cua new-task multiply \
  --prompt 'Evaluate 6 * 7 in HolyC and print ANSWER=<result>.' --expect ANSWER=42
uv run temple-cua list-tasks
```

Edit the generated YAML, then select it with `--task multiply`. Omit `--expect`
for a task that needs trajectory review. Extra GUI tasks live in `tasks/extra/`.

## Layout

```text
src/temple_cua/  Harness, adapters, and graders
tasks/          Runnable task definitions
references/     Scripted solutions and reference validation
recordings/     Published model attempts, including earlier failures
website/        Site source, build script, and featured-run configuration
tests/          Harness and website tests
```

Local VM assets live in `assets/`; new runs go into `runs/`. Both are ignored.
Historical recordings retain their original metadata and scores.

## Website and checks

[website/config.yaml](website/config.yaml) selects the featured tasks and recordings.
The default build includes all five; generated files go into `build/website/`.

```sh
uv run python website/build.py
python -m http.server 8000 --directory build/website
uv run pytest
```

## Recorded results

GPT-6.1 Sol completed the counter panel; the cursor and sprite attempts remained
incomplete. These GUI outcomes are assistant reviews, separate from raw grades.
The [UART rerun](recordings/cua-uart-300/) compiled and started a driver, then
stopped after 32 calls / 641 seconds with **0/10 host checks**. The source suggests
a checksum-width defect; the detailed review records that uncertainty and a
cleanup text-matching limitation. The [80-call attempt](recordings/cua-uart/)
is preserved. Budget and adapter changes were combined; the rerun stayed below
the old limits, so it does not establish a benefit from extra turns.

For budget context: [original OSWorld](https://arxiv.org/html/2404.07972v1#A3.SS1)
used 15 steps, [Anthropic's OSWorld-Verified evaluation](https://www.anthropic.com/news/claude-sonnet-4-5)
uses 100, and [OSWorld 2.0](https://arxiv.org/html/2606.29537v1#S3.SS1) uses 500.
Their steps can batch actions; these are not equivalent budgets or scores.

TempleOS is public domain; this harness is [MIT licensed](LICENSE). The official
ISO is checksum-verified. See the [HolyC manual](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/HolyC.DD),
[graphics source](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/Gr/GrScrn.HC),
and [Tic-Tac-Toe source](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Demo/Games/TicTacToe.HC).
