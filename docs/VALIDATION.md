# Validation

Checked in the build workspace on 2026-10-04 with Python 3.12.14, QEMU
10.0.13, 512 MiB guest RAM, and the official TempleOS 5.03 ISO:
`5d0fc944e5d89c155c0fc17c148646715bc1db6fa5750c0b913772cfec19ba26`.

The unit suite passes 108 tests. It covers constrained input validation,
provider request/response formats and retries, QMP framing and deadlines,
snapshot compatibility, conservative bitmap text decoding, grading, HTML
escaping, comparison metadata, and runner lifecycle/error artifacts.
Regression fixtures include real TempleOS screenshots.

Live checks confirmed ISO boot, install/tour dismissal, B: RAM drive access,
snapshot save and restore, real keyboard typing, pointer positioning at exactly
`100,100`, mouse clicks that change FileMgr UI, and interruption of long typing
with held keys released. The CLI preparation command and subsequent arithmetic
reference run were tested as documented.

The [included report](../examples/validated-run/report.html) contains actual
reference execution screenshots and action trajectories:

| Task | Visible result | Grade |
| --- | --- | --- |
| Arithmetic | `BENCH_ARITH=391` | Passed visual text check |
| Sum of squares | `BENCH_SUM=650` | Passed visual text check |
| Triangular function | `BENCH_TRI=325` | Passed visual text check |
| String statistics | Length 17 and four lowercase e characters | Passed visual text check |
| Directory navigation | `/Demo/Graphics` and `Blot` listing | Passed visual text check |
| File roundtrip | 17 bytes and alpha/beta/gamma contents | Passed visual text check |
| Editor program | Saved source and `BENCH_CUBE=343` | Review pending |
| Drawing | Red circle, blue rectangle, green line, label | Review pending |

Native stock-font extraction replaced Tesseract for 640×480 TempleOS frames
after live testing exposed repeated glyph misreads. The reader matches actual
bitmap cells without replacing characters to satisfy expected answers.
Underlining destroys some punctuation distinctions, so ambiguous cells remain
unknown; directory scoring checks the filename stem. Tesseract remains available
as a fallback when stock-font text is unavailable.

These runs use deterministic reference scripts. They establish harness behavior
and sample-task feasibility, not model performance. OpenAI and Anthropic adapters
were checked using mocked HTTP responses; no live model comparison was run because
API credentials were not configured. Exact model API IDs and access must be
supplied when conducting the comparison.
