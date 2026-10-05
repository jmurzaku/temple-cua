# GPT-6.1 Sol through Cua: starter suite

On 2026-10-05, the actual `cua-agent==0.9.0` ComputerAgent ran six TempleOS
starter tasks using exact model ID `openai/gpt-6.1-sol`. Each task restored a
fresh copy of the same frozen 512 MiB, 640x480 TempleOS 5.03 VM. Cua owns the
model/action loop; the harness retains QMP input, reset, budgets and grading.

| Task | Visual check | Model calls | Total seconds |
| --- | --- | ---: | ---: |
| Arithmetic | Passed | 3 | 16.923 |
| Sum of squares | Passed | 3 | 15.924 |
| Define/call a function | Passed | 3 | 19.279 |
| String statistics | Passed | 2 | 25.624 |
| Directory navigation | Passed | 3 | 13.911 |
| File round trip | Passed | 3 | 31.988 |

All six completed. Total: 17 model calls, 123.649 seconds including 7.966 seconds
of VM setup, 22,898 input tokens and 1,322 output tokens. LiteLLM estimated
$0.0491531 for this suite; this is a library price estimate, not a billing
statement. The run used a 24-call/300-second per-task budget, 90-second request
timeout, 4,096 output-token cap and two recent screenshots. No reasoning-effort
override was applied.

Review of the trajectories found the requested implementations: the actual
integer expression, a loop, a defined/called BenchTri function, StrLen plus
character counting, boot-relative Cd and Dir, and FileWrite followed by FileRead
with its returned byte-count variable. No precomputed-answer shortcuts or fake
directory listings were found in this run.

Visual graders now require standalone output lines for BENCH markers and file
text, and a structured directory listing/current-path prompt for navigation.
They reject ordinary command echoes. Screenshots alone still cannot prove every
implementation requirement or hidden filesystem state. One attempt per task is
a smoke test, not a stable performance estimate.

## Artifacts and integration checks

Measured suite: [portable results](../examples/cua-starter/results.json) and
[run report](../examples/cua-starter/report.html). The original local run is
`runs/cua-gpt-6.1-sol-starter-v2/`, excluded from Git because it contains VM disks.
Per-task directories contain prompts, numbered screenshots, JSONL actions,
usage and grader evidence. The website shows only these six starter tasks.
Portable copies omit provider response IDs, opaque response payloads and host
task source paths; the actions, usage and screenshots are retained.

Reproduce with your own OpenAI credential and a freshly prepared baseline:

```sh
temple-cua run --provider cua --model openai/gpt-6.1-sol \
  --baseline assets/baseline.qcow2 --boot-wait 1 \
  --task arithmetic --task sum_of_squares --task triangular_function \
  --task string_statistics --task directory_navigation --task file_roundtrip \
  --max-steps 24 --timeout 300 --api-timeout 90 \
  --max-output-tokens 4096 --cua-image-history 2 --output runs/cua-starter
```

An earlier integration attempt (`runs/cua-gpt-6.1-sol-starter`) rejected Cua's
documented inactive function fields before executing guest input. It is an
infrastructure failure, not a model task score. The compatibility bridge now
projects the flat schema to the selected action and validates active fields.
A live arithmetic smoke then passed before this six-task suite.

The final port passed 191 tests and an actual TempleOS/Cua deterministic fixture
check. See [CUA_AGENT.md](CUA_AGENT.md) for installation and the compatibility
bridge. API credentials were provided on hidden stdin, used only in process
memory/environment, and excluded from exported artifacts.
