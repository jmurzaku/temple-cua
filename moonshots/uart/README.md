# UART experiment

A separate HolyC task implements a COM1 binary request/reply service with IRQ4
input, a bounded receive ring, a worker, IEEE CRC32 replies, and shutdown.
[task.yaml](task.yaml) defines the wire contract. It is outside the default
visual task suite and uses an independent host serial judge.

Reward: `0.9 * (passing cases / 8) + 0.1 * desktop_responsive`. Desktop credit
requires at least one passing serial case. The judge covers payload bounds,
fragmentation, bursts, checksum/framing recovery, and unexpected replies. A
standalone host nonce checks shell responsiveness.

The [reference](reference/README.md) passed all eight cases, additional CRC/burst
checks, and shutdown silence. The [GPT-6.1 Sol pilot](PILOT.md) scored 0/8; its
driver never started. Serial behavior and hardware traces do not prove IRQ-only
processing or complete state restoration.

Run another trial from the project root after fetching the ISO:

```sh
.venv/bin/python scripts/run_uart_trial.py \
  --model gpt-6.1-sol --qemu qemu-system-x86_64 \
  --max-steps 40 --timeout 900 --output runs/uart-trial
```

The script takes the OpenAI key on hidden stdin. It saves actions, screenshots,
usage, hardware diagnostics, and serial verdicts under the output directory.
