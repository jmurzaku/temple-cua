# GPT-6.1 Sol UART pilot

One screenshot-only cold-start episode ran on 2026-10-04 using the exact API
model ID `gpt-6.1-sol`, which the supplied credential could access. No credentials
are stored in this project or its exported artifacts.

| Measurement | Observed |
| --- | --- |
| Budget | 40 model turns; 900 policy seconds |
| Model result | 0/8 external UART cases; reward 0.0 |
| Desktop after evaluation | Responsive |
| Policy time | 368.95 seconds |
| Setup | 19.37 seconds |
| Total episode | 405.71 seconds |
| Input / output tokens | 123,712 / 4,441 |
| Reasoning tokens | 728 |
| API latency | 175.52 seconds |

The adapter used OpenAI Responses with a standard screenshot and `act` tool,
up to four validated actions per turn, 16 turns of text history, an 8,192-token
output cap, and no explicit reasoning-effort override. This is not an official
provider-native computer-use benchmark or a comparison against Claude/Astra.

## What happened

Early turns searched incorrect source-drive paths and guessed unsupported
help commands. At step 19 the model typed a 3,253-character driver draft into
the editor. That action took roughly 182 seconds. The draft contained an IRQ
buffer, parser, CRC worker and start/stop routines, but compilation failed on
`continue`. The model replaced that control flow, then hit the unknown
`IntsOff` primitive. It guessed alternatives and continued searching until its
turn budget ended. No successful `BenchStart` was observed.

All eight host challenges received no reply bytes. The QEMU trace ended with
UART IER=0, IRQ4 masked in PIC mask 0xF8, zero delivered IRQ4 interrupts, and
zero UART data-register reads/writes. Those observations support the narrower
finding that the driver never activated. They do not establish a general limit
on the model's systems-programming ability.

The initial desktop liveness decoder incorrectly included unknown window-border
cells around the printed nonce. Rechecking the saved screenshot with edge-only
border stripping confirmed a responsive desktop. The original result and the
calibration note are retained in `result.json`; reward remains zero because
desktop credit requires a valid UART case.

## Fixture validation and subsequent input fix

The final human-authored reference passed 8/8 external cases on the same guest,
plus independent CRC/burst/recovery checks, and shut down successfully. This
establishes that the serial fixture and judge admit a working solution.

During reference validation, simultaneous modifier/character delivery once
typed `InU89` instead of `InU8(`. The backend now sends modifiers first, waits
20 ms, sends characters for 40 ms, then releases characters and modifiers with
separate 20 ms gaps. Three live punctuation probes passed. The full suite passes
141 tests. The recorded model episode predates this input-timing fix; it has
not been rerun, and its observed compiler failures remain in the evidence.

## Artifacts and interpretation

`runs/gpt-6.1-sol-uart-pilot/` contains the result, all 41 policy screenshots,
final liveness screenshot, JSONL actions, first source draft and raw QEMU trace.
Exported research archives omit the large raw trace but retain its summary.
The replay displays recorded screenshots and actions, not a live VM.

One run is insufficient for an estimate of pass rate. Better next experiments
would fix input timing, provide a valid local API skeleton, expose bounded
intermediate hardware probes, and run multiple independent seeds under matching
budgets. These are proposed experiments, not measured improvements.
