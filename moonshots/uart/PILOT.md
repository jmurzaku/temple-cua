# UART pilot record

One cold-start episode used exact model ID `gpt-6.1-sol` through the original
OpenAI Responses adapter on October 4, 2026 (EDT).

| Measurement | Observed |
| --- | --- |
| Budget | 40 model turns; 900 policy seconds |
| Serial result | 0/8 cases; reward 0.0 |
| Desktop | Responsive |
| Policy / setup / total seconds | 368.95 / 19.37 / 405.71 |
| Input / output tokens | 123,712 / 4,441 |
| Reasoning tokens | 728 |
| API latency | 175.52 seconds |

At turn 19 the model typed a 3,253-character draft; typing took about 182 seconds.
Compilation failed on `continue`, then on the unknown `IntsOff` primitive.
No successful start was observed before the turn budget ended. All host cases
received no replies. The trace showed UART IER=0, IRQ4 masked, no delivered IRQ4
interrupts, and no UART data-register reads/writes.

Reference validation later exposed an input-timing defect: simultaneous modifier
and character delivery could mistype punctuation. The backend was fixed, but this
model episode predates that change and has not been rerun. A liveness decoder
border-handling correction confirmed the responsive desktop; serial reward
remained zero.

The raw run is local under `runs/gpt-6.1-sol-uart-pilot/` and excluded from Git.
The [reference evidence](reference/validation/) is retained. This single episode
is insufficient to estimate model performance on UART programming.
