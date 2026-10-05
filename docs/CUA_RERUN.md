# Recorded starter run

Exact model: `openai/gpt-6.1-sol`, using `cua-agent==0.9.0`. Started at
`2026-10-05T00:27:11.588954+00:00` (October 4, EDT). Each task restored the same
512 MiB, 640×480 TempleOS 5.03 VM.

| Task | Visual check | Model calls | Seconds including setup |
| --- | --- | ---: | ---: |
| Arithmetic | Passed | 3 | 16.923 |
| Sum of squares | Passed | 3 | 15.924 |
| Define/call a function | Passed | 3 | 19.279 |
| String statistics | Passed | 2 | 25.624 |
| Directory navigation | Passed | 3 | 13.911 |
| File round trip | Passed | 3 | 31.988 |

Total: 17 model calls, 123.649 seconds (7.966 setup), 22,898 input tokens,
1,322 output tokens. LiteLLM cost estimate: $0.0491531. The per-task budget was
24 calls/300 seconds, with 90-second requests, 4,096 output tokens, and two recent
screenshots. No reasoning-effort override was set.

Trajectory review found expression evaluation, a summation loop, a defined/called
function, `StrLen` plus character counting, `Cd`/`Dir`, and `FileWrite`/`FileRead`
using the returned size. No printed-answer shortcuts were found. This is one
attempt per task; the automatic grades measure visible output.

[Results JSON](../examples/cua-starter/results.json) and
[recordings/actions](../examples/cua-starter/) retain the evidence. Portable copies
omit provider response IDs, opaque payloads, and host task paths. ISO and baseline
hashes are recorded in the results. The six-task command in the
[root README](../README.md) reproduces the run configuration with your own baseline.
