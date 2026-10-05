# UART reference implementation

`Telemetry.HC` is a host-only reference, excluded from the task prompt. It is
intended for validating the fixture and judge, not as model training feedback.

Compile its declarations and functions in the TempleOS HolyC shell, then call
`BenchStart;`. Preserve the `#define` directive lines. Strip `//` comments and
collapse each complete top-level function to one line before typing it into the shell; an Enter key
submits a statement to the compiler. Each function is shorter than 4096
characters and can be loaded in a separate keyboard action. Call `BenchStop;` before unloading
the source or ending the shell that owns it. Repeated start/stop calls are
safe within that shell.

The IRQ4 handler drains the receive FIFO into a 4096-byte bounded ring. A
spawned worker parses packets, computes standard IEEE CRC32, and transmits
seven-byte replies with a bounded transmitter-ready wait. It yields after a
bounded parsing batch. The driver enables only UART receive interrupts;
transmitter interrupts are unnecessary for the short replies.

Shutdown restores the full saved interrupt descriptor, the original IRQ4
mask bit, the UART divisor, line control, modem control, interrupt enable,
and prior FIFO-enabled status. The UART FIFO control register is write-only;
its previous trigger threshold cannot be recovered, and pending input is
discarded during startup. The evaluation fixture starts with unused COM1.

`bench_irqs` and `bench_overflows` are diagnostic counters, not trusted
grading evidence. Correct responses are verified externally over the QEMU
serial socket. Host verification does not by itself prove interrupt-driven
implementation; that requires trusted emulator instrumentation or review.

## Live validation

Validated in a fresh TempleOS QEMU session on 2026-10-04, using actual keyboard
input with separately delivered modifier and character key events. The final
source compiled successfully and passed all eight external UART judge cases.
Additional checks passed the canonical `123456789` IEEE CRC32 vector, binary
64-byte payloads, malformed-request recovery, and a 20-frame burst. The driver
reported 146 interrupt entries and zero ring overflows. `BenchStop` returned
with `bench_active=FALSE`; a subsequent host request received no reply, and the
shell remained responsive.

Results and final screenshots are in `validation/`. These prove the reference
fixture works; they are not model evaluation results.
