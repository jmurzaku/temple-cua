# One moonshot: resilient ring-0 UART telemetry

Build a COM1 service in HolyC through screenshots and keyboard input. The agent
must install a real IRQ4 handler, receive into a bounded ring, parse binary
requests in a background worker, return IEEE CRC32 replies, and preserve the
desktop. `BenchStop` must restore the prior interrupt and device state.

The task uses ring 0 directly: UART I/O ports, the interrupt descriptor table,
the PIC mask, interrupt acknowledgment, and scheduler cooperation. This is an
isolated QEMU guest with no network and a scratch RAM drive.

The exact prompt and wire contract are in [task.yaml](task.yaml). This task uses
a custom host serial grader, so it is not part of the default visual task suite.
Fresh challenges are generated after the policy finishes; challenge seeds and
transcripts must stay hidden from the policy until evaluation ends.

## Implemented reward

`0.9 * (passing UART cases / 8) + 0.1 * desktop_responsive`

Desktop credit requires at least one passing UART case. The judge checks short
and maximum-length payloads, fragmented requests, an eight-frame burst, corrupt
checksum rejection followed by recovery, continued recovery, invalid lengths,
and framing bytes inside binary payloads. Corruption cases include a subsequent
valid request; silence is not rewarded. Unexpected and out-of-order replies fail.

The screenshot liveness check requires an exact, standalone host-generated
nonce printed by the HolyC shell. A typed command echo does not pass. Unknown
font cells are removed only at row edges to accommodate window borders.

Behavior proves the protocol works; it does not prove IRQ-only operation or all
state restoration requirements. QEMU serial/PIC traces are diagnostic. A causal
test that suppresses IRQ delivery while leaving UART data available, plus
trusted cleanup checks, remains future work.

## Reference and actual model trial

The [reference implementation](reference/Telemetry.HC) passed 8/8 live judge
cases, canonical IEEE CRC32, binary payloads, malformed-frame recovery, and a
20-frame burst. It reported 146 interrupts and no buffer overflow. Shutdown
completed and further host input received no reply. See
[reference validation](reference/README.md).

The first GPT-6.1 Sol trial scored 0/8 and reward 0.0. Its driver never started;
compiler errors consumed the 40-turn budget. The desktop remained responsive.
See [PILOT.md](PILOT.md) for measurements and limitations.

## Run another trial

From the project root, with the official ISO fetched and dependencies installed:

```sh
.venv/bin/python scripts/run_uart_trial.py \
  --model gpt-6.1-sol --qemu qemu-system-x86_64 \
  --max-steps 40 --timeout 900 --output runs/uart-new-trial
```

The script requests the API key on hidden stdin. It keeps it in process memory
and writes screenshots, action trajectories, usage, hardware diagnostics and
the host serial verdict. The current custom UART trial script uses OpenAI;
the general harness also supports Anthropic, but this moonshot's Anthropic
episode orchestration has not been implemented or tested.

## RL curriculum proposal

Keep one task and vary the starting support: a valid HolyC skeleton and local
API map first, then empty functions, then a blank editor. Add bounded host probes
for compilation/device setup/first valid replies. Later vary fragmentation,
malformed frames and bursts, and add causal IRQ-dependence and restoration tests.
The present reward remains sparse until a valid serial response exists.
Hosted API trials provide evaluation data; this harness does not train the
closed model weights.
