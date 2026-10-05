# Tasks and grading

The default task directory contains six automatic tasks and four manual tasks.
All start from a restored TempleOS 5.03 snapshot: 640×480, US keyboard mapping,
maximized shell at the boot drive's `/Home`. File tasks use the writable `B:` RAM
drive. `::` means the boot drive, whose letter can vary.

| ID | Task | Grade |
| --- | --- | --- |
| `arithmetic` | Evaluate `(37 * 19) - (24 * 13)` | Output `BENCH_ARITH=391` |
| `sum_of_squares` | Sum twelve squares with a loop | Output `BENCH_SUM=650` |
| `triangular_function` | Define and call `BenchTri(25)` | Output `BENCH_TRI=325` |
| `string_statistics` | Measure a string and count uppercase E | Two output lines |
| `directory_navigation` | List `/Demo/Graphics` | Listing, Blot row, final directory prompt |
| `file_roundtrip` | Write/read three lines on RAM drive | Byte count and file text |
| `editor_program` | Save and execute a HolyC program | Manual |
| `file_manager` | Browse a tree and open source in FileMgr | Manual |
| `documentation` | Follow built-in documentation links | Manual |
| `draw_shapes` | Draw a persistent colored scene | Manual |

Automatic checks read the final framebuffer using exact stock 8×8 glyphs, with
Tesseract as a fallback. BENCH outputs and file text must occupy standalone
lines. Directory scoring also checks the listing structure and final prompt.
Underlined punctuation can be ambiguous; the glyph reader keeps it unknown.

These checks reject ordinary command echoes but accept fabricated output.
They do not establish computation method or hidden filesystem state. The four
manual tasks remain `needs_review` until their screenshot/action rubrics are
reviewed; a model's completion statement does not assign a pass.

## Task files

YAML files require `version: 1`, `id`, `title`, `prompt`, and `grader`. Optional
fields are `category`, `difficulty`, `max_steps` (default 40), `timeout_seconds`
(default 180), and `setup` actions performed before model access.

```yaml
version: 1
id: example
title: Print a computed value
prompt: Evaluate 6 * 7 in HolyC and print ANSWER=<result> on its own line.
grader:
  type: ocr
  all: ["ANSWER=42"]
  match_mode: line
  case_sensitive: true
  crop: [8, 16, 624, 456]
```

OCR requires nonempty `all`. Optional `any` requires at least one alternative.
`match_mode` is `substring` by default or `line` for exact normalized lines.
`line_patterns` adds full-line regex checks; `last_line_pattern` checks the last
visible line. `crop` is `[x,y,width,height]`. Whitespace is normalized and matching
is case-insensitive unless configured otherwise. Manual graders use `type: manual`
and a `rubric`. Validate definitions with `temple-cua list-tasks --tasks PATH`.

[`scripts/reference`](../scripts/reference/) contains backend replay scripts.
Use a fresh snapshot for each replay. `shell_ready.json` is a preparation script
for a fresh ISO boot. To execute saved HolyC from the command line, terminate
includes with a semicolon: `#include "B:/Bench/Cube.HC";`.

TempleOS API references are pinned to
[`c26482bb6ad3f80106d28504ec5db3c6a360732c`](https://github.com/cia-foundation/TempleOS/tree/c26482bb6ad3f80106d28504ec5db3c6a360732c).
See `Doc/CmdLineOverview.DD` for shell/path syntax, `Kernel/BlkDev/DskFile.HC`
for file APIs, and `Demo/Graphics/WinZBuf.HC` for task drawing callbacks.
The separate [TEST ONLY cursor callback](CURSOR_CALLBACK.md) is not a default task.
