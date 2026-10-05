# Sample TempleOS tasks

The ten tasks are a starter suite for exercising screenshot observation,
keyboard input, mouse interaction, and HolyC execution. Six have automatic
OCR outcome checks; four require review of the screenshots and action log.
These examples help validate a computer-use harness. They are not a validated
benchmark of general computer-use ability.

| Task ID | Interaction | Difficulty | Scoring |
| --- | --- | --- | --- |
| `arithmetic` | Evaluate an integer expression | Easy | OCR: `BENCH_ARITH=391` |
| `sum_of_squares` | Execute a loop | Medium | OCR: `BENCH_SUM=650` |
| `triangular_function` | Define and call a function | Medium | OCR: `BENCH_TRI=325` |
| `string_statistics` | String length and character count | Medium | OCR: two output lines |
| `directory_navigation` | Boot-drive directory navigation | Easy | OCR: path and `Blot` filename stem |
| `file_roundtrip` | Write/read a text file on RAM drive | Medium | OCR: byte count and text |
| `editor_program` | Create, save, and execute an editor program | Hard | Manual trajectory review |
| `file_manager` | Browse a tree and open source through FileMgr | Medium | Manual trajectory review |
| `documentation` | Follow built-in documentation links | Easy | Manual trajectory review |
| `draw_shapes` | Draw a persistent colored scene | Hard | Manual final-image and trajectory review |

## Baseline

Use the TempleOS 5.03 live ISO with 640×480 VGA output and US keyboard mapping.
Start every task from the same prepared, restored VM state. The selected user
terminal should be maximized, have input focus, be at the boot drive's `/Home`
directory, and have a clean command line. Models must see the task prompt and
screenshots, while the grader configuration and reference solutions stay in the
host-side evaluator.

Live-ISO startup asks whether to install onto the hard drive and whether to take
the tour. Answer `n` to each. The default startup creates two terminal windows;
the first terminal receives these prompts. Do not blindly send Escape once at
the command line: TempleOS may use Escape to activate a document entry.

The boot drive is read-only. The stock live ISO configures a writable `B:` RAM
drive, used for file and editor task scratch space without installing TempleOS.
At the shell, trusted preparation normalizes the selected terminal and closes
the autocomplete overlay, and selects one-pixel mouse-grid spacing:

```c
Cd("::/Home");
WinMax;
AutoComplete(OFF);
ms_grid.x=ms_grid.y=1;
DocClear;
```

Enter this preparation before saving the common checkpoint. It provides no task
answer. Each file task creates its own `B:/Bench` directory. `::` denotes the boot
drive, so tasks do not hardcode its letter. RAM files disappear when the VM exits
or the baseline is restored; their visible creation and readback are preserved
in the run artifacts. If you use a custom ISO without the stock `B:` drive, mount
a writable RAM drive before saving the baseline and update the task paths.

## Evaluation and limits

An OCR task passes when its required strings appear in the **final framebuffer**.
The task's natural-language completion claim does not affect that score. Review
the saved image and extracted text if a run fails. The grader first reads exact
8×8 glyphs from TempleOS's public-domain stock font, preserving unknown cells
rather than guessing; Tesseract provides a fallback for other visual layouts.
Custom fonts, line wrapping, and window layout can still cause text-recognition
mistakes. A marker that appears only in an earlier frame does not satisfy a
final-frame check.

Directory links underline the bottom glyph row, which makes some punctuation
visually ambiguous. The exact reader preserves those cells as unknown. The
directory task therefore checks `/Demo/Graphics` and the unambiguous `Blot`
filename stem; its prompt still asks for the real file listing.

OCR measures visible outcome evidence. It does not prove that a loop ran, that a
function was used, that a file was saved, or that the current directory is correct.
A model can type a literal marker or imitate a listing. The task prompts require
the intended operations, but those instructions are not an enforcement mechanism.
The expected results are static and visible in these public task files. The
automatic score should therefore be reported as `OCR outcome success`, not as
verified semantic or filesystem success. For method-sensitive research, review
trajectories, add trusted hidden state checks, or randomize task inputs and
expected outputs before comparing models.

Manual tasks deliberately avoid converting a model's own statement into a pass.
Use each task's rubric against the recorded actions and screenshots. The grader
should return a review-pending result until a human has assessed it. Keep manual
pass rates separate from automatic OCR pass rates, and report sample count,
timeouts, action limit, model identifier, provider API, and failures for each
comparison. Do not treat a review-pending result as a failure or success.

The file-manager, documentation, and editor tasks have explicit interaction
requirements. Even if a direct shell command reaches the same final page, that
does not meet those manual rubrics. There is no such enforcement in the six OCR
tasks; this distinction is intentional and must remain visible in reports.

## Task format

Every YAML task has `version: 1`, an `id`, `title`, `category`, `difficulty`,
`prompt`, `max_steps`, `timeout_seconds`, and a `grader`. Optional `setup` is a
list of trusted preparation actions. It is empty for these samples, because all
tasks use the common baseline and the agent performs the task-specific work.

Automatic examples use `grader.type: ocr` with `all` strings that must all match.
Manual examples use `grader.type: manual` with a concrete `rubric`. A task's
`done` action only ends its interaction loop; it is not a success assertion.

Actions use the harness's common vocabulary, for example:

```json
{"kind":"type","text":"Dir;\n"}
{"kind":"key","keys":["ctrl","s"]}
{"kind":"wait","seconds":0.5}
{"kind":"done","text":"The requested output is visible."}
```

The JSON files in [`scripts/reference`](../scripts/reference/) are host-side
deterministic turn sequences for testing the keyboard/backend and graders.
Each turn contains an `actions` list of up to four action objects and a `note`.
They are reference solutions, not model-generated benchmark runs. Never include
their contents in the model prompt or use their completion as evidence of model
performance. Run task references only from the prepared baseline and restore the
VM before the next task. `shell_ready.json` is the exception: it is a full trusted
live-ISO preparation script that answers the install and tour prompts and then
normalizes the terminal. Use it with `prepare --script` only on a fresh stock-ISO
boot, and inspect the resulting ready screenshot. The GUI tasks still require
screenshot-driven interaction; fixed
coordinates are deliberately omitted from their reference solutions.

## TempleOS API verification

The sample commands were checked against the public TempleOS source mirror at
commit [`c26482bb6ad3f80106d28504ec5db3c6a360732c`](https://github.com/cia-foundation/TempleOS/tree/c26482bb6ad3f80106d28504ec5db3c6a360732c).
The stock `B:` RAM drive, shell preparation, and all six OCR reference outcomes
were also executed against the pinned live ISO, SHA-256
`5d0fc944e5d89c155c0fc17c148646715bc1db6fa5750c0b913772cfec19ba26`.
The editor reference was checked
through its save, close, and execution sequence; the graphics reference produced
the requested visible scene. These are deterministic harness validation runs,
not model evaluation results. The file-manager and documentation navigation
samples have source-checked instructions and manual rubrics; their complete GUI
trajectories still need human review. Check task execution again when adopting
the tasks or changing the VM baseline.

When executing an include from the interactive command line, terminate it with
a semicolon, for example `#include "B:/Bench/Cube.HC";`. The saved editor program
can appear to load without executing its output if that terminator is omitted.

- [`Once.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Once.HC): install/tour startup prompts.
- [`Misc/DoDistro.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Misc/DoDistro.HC): stock ISO `B:` RAM-drive configuration.
- [`Doc/CmdLineOverview.DD`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/CmdLineOverview.DD): HolyC command-line execution, `::` paths, editor and include syntax.
- [`Kernel/BlkDev/DskDirB.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Kernel/BlkDev/DskDirB.HC): `Cd`, `Dir`, `DirMk`, and `Cd(path,TRUE)` directory creation.
- [`Kernel/BlkDev/DskFile.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Kernel/BlkDev/DskFile.HC): `FileWrite(filename,buffer,size)` and `FileRead(filename,&size)`; returned reads are NUL terminated.
- [`Kernel/StrA.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Kernel/StrA.HC): `StrOcc` character counting; `StrLen` is declared in `Kernel/KernelB.HH`.
- [`Adam/DolDoc/DocPutKey.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/DolDoc/DocPutKey.HC): Ctrl+S calls `DocWrite`.
- [`Adam/ABlkDev/FileMgr.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/ABlkDev/FileMgr.HC) and [`Doc/FileMgrPullDown.DD`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Doc/FileMgrPullDown.DD): graphical manager and edit commands.
- [`Demo/Graphics/WinZBuf.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Demo/Graphics/WinZBuf.HC): persistent `Fs->draw_it` callback pattern.
- [`Adam/Gr/GrPrimatives.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/Gr/GrPrimatives.HC) and [`Adam/Gr/GrBitMap.HC`](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/Gr/GrBitMap.HC): `GrCircle`, `GrLine`, and filled `GrRect`.
