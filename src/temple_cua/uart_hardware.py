"""Host-owned UART/PIC probes for the disposable TempleOS VM.

These probes run after policy input has ended. Their fresh challenges and raw
hardware observations belong in host artifacts, never in policy observations.
PIC masking demonstrates IRQ4 dependence, not a formal proof that no polling
code exists. Register snapshots omit unreadable FIFO contents/trigger state.
"""

from __future__ import annotations

import math
import os
import random
import re
import socket
import time
from typing import TYPE_CHECKING

from .uart_judge import expected_reply, request_frame

if TYPE_CHECKING:
    from .uart_judge import UARTJudge
    from .vm import TempleVM

COM1 = 0x3F8
PIC_MASK = 0x21
IRQ4_BIT = 0x10
IRQ4_VECTOR = 0x24


class UARTHardwareError(RuntimeError):
    """A host hardware observation could not be obtained safely."""


class _BehaviorFailure(RuntimeError):
    """A valid hardware observation establishes a behavioral failure."""


def _positive(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return float(value)


class UARTHardwareProbe:
    """Inspect COM1 without reading its RX data register.

    ``judge`` must own an already connected serial socket. Capturing hardware
    temporarily stops guest CPUs and preserves their original running state.
    Reading divisor aliases uses DLAB and restores LCR even on failure.
    """

    def __init__(self, vm: TempleVM, judge: UARTJudge, *, command_timeout: float = 2.0):
        self.vm = vm
        self.judge = judge
        self.command_timeout = _positive(command_timeout, "command_timeout")
        self.commands: list[dict] = []

    def _qmp(self, command: str, arguments: dict | None = None):
        if self.vm.qmp is None:
            raise UARTHardwareError("VM has no connected QMP monitor")
        return self.vm.qmp.execute(command, arguments, timeout=self.command_timeout)

    def _hmp(self, command: str) -> str:
        record = {"command": command}
        self.commands.append(record)
        result = self._qmp("human-monitor-command", {"command-line": command})
        if not isinstance(result, str):
            raise UARTHardwareError(f"HMP returned a non-text response to {command!r}")
        record["output"] = result
        return result

    def read_port(self, port: int) -> int:
        if type(port) is not int or not 0 <= port <= 0xFFFF:
            raise ValueError("port must be an integer in 0..65535")
        result = self._hmp(f"i /b 0x{port:x}")
        match = re.search(r"portb\[\s*(0x[\da-f]+)\s*\]\s*=\s*(0x[\da-f]+)", result, re.I)
        if not match or int(match[1], 16) != port or not 0 <= int(match[2], 16) <= 255:
            raise UARTHardwareError(f"Could not parse byte port read: {result!r}")
        return int(match[2], 16)

    def write_port(self, port: int, value: int) -> None:
        if type(port) is not int or not 0 <= port <= 0xFFFF:
            raise ValueError("port must be an integer in 0..65535")
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("value must be an integer in 0..255")
        result = self._hmp(f"o /b 0x{port:x} 0x{value:x}")
        if result.strip():
            raise UARTHardwareError(f"Unexpected byte port write response: {result!r}")

    def _idt_entry(self) -> dict:
        registers = self._hmp("info registers")
        match = re.search(r"\bIDT\s*=\s*([\da-f]+)\s+([\da-f]+)", registers, re.I)
        if not match:
            raise UARTHardwareError("CPU register report contains no IDT base/limit")
        base, limit = (int(part, 16) for part in match.groups())
        offset = IRQ4_VECTOR * 16
        if limit < offset + 15:
            raise UARTHardwareError("IDT limit does not include interrupt vector 0x24")
        memory = self._hmp(f"xp /16bx 0x{base + offset:x}")
        values: list[int] = []
        for line in memory.splitlines():
            if ":" in line:
                values.extend(int(value, 16) for value in re.findall(r"\b0x([\da-f]{1,2})\b", line.split(":", 1)[1], re.I))
        if len(values) != 16:
            raise UARTHardwareError("Could not read exactly 16 IDT descriptor bytes")
        return {"number": IRQ4_VECTOR, "idt_base": base, "idt_limit": limit, "bytes_hex": bytes(values).hex()}

    def capture_state(self) -> dict:
        """Snapshot readable restoration state while the guest is stopped."""
        first_command = len(self.commands)
        running = bool(self._qmp("query-status")["running"])
        stopped = False
        old_lcr: int | None = None
        try:
            if running:
                self._qmp("stop")
                stopped = True
            vector = self._idt_entry()
            mask = self.read_port(PIC_MASK)
            old_lcr = self.read_port(COM1 + 3)
            uart = {
                "lcr": old_lcr,
                "mcr": self.read_port(COM1 + 4),
                "fifo_enabled": bool(self.read_port(COM1 + 2) & 0xC0),
                "scratch": self.read_port(COM1 + 7),
            }
            self.write_port(COM1 + 3, old_lcr & 0x7F)
            uart["ier"] = self.read_port(COM1 + 1)
            self.write_port(COM1 + 3, old_lcr | 0x80)
            uart["divisor_low"] = self.read_port(COM1)
            uart["divisor_high"] = self.read_port(COM1 + 1)
        finally:
            try:
                if old_lcr is not None:
                    self.write_port(COM1 + 3, old_lcr)
            finally:
                if stopped:
                    self._qmp("cont")
        return {
            "interrupt_vector": vector,
            "pic": {"master_mask": mask, "irq4_masked": bool(mask & IRQ4_BIT)},
            "uart": uart,
            "host_commands": self.commands[first_command:],
            "limitations": [
                "TempleOS identity mapping is assumed when reading the IDT through physical memory.",
                "FIFO contents, RX trigger selection, and other write-only/transient state cannot be compared.",
                "IIR is read only for FIFO-enable bits; its transient interrupt reason is not graded.",
            ],
        }

    def irq4_dependence(self, *, seed: int | None = None, masked_seconds: float = 0.25,
                        timeout: float = 2.0, quiet_seconds: float = 0.05) -> dict:
        """Send one fresh frame while IRQ4 is masked, then restore its mask.

        The request is ten bytes, below the enabled 16550 FIFO's 16-byte
        capacity. A passing service stays silent under masking and returns the
        exact reply after restoration, without another send. Unsolicited or
        duplicate bytes fail rather than being discarded.
        """
        masked_seconds = _positive(masked_seconds, "masked_seconds")
        timeout = _positive(timeout, "timeout")
        quiet_seconds = _positive(quiet_seconds, "quiet_seconds")
        if seed is not None and (type(seed) is not int or seed < 0):
            raise ValueError("seed must be a nonnegative integer or None")
        connection = self.judge._socket
        if connection is None or self.vm.qmp is None:
            return {
                "type": "host_irq4_dependence", "status": "error", "infrastructure_error": True,
                "reason": "Connect UARTJudge and the VM QMP monitor before probing IRQ4",
                "transcript": [], "host_commands": [],
            }
        chosen_seed = seed if seed is not None else int.from_bytes(os.urandom(32), "big")
        rng = random.Random(chosen_seed)
        sequence = rng.randrange(256)
        payload = bytes(rng.randrange(256) for _ in range(6))
        frame = request_frame(sequence, payload)
        expected = expected_reply(sequence, payload)
        began = time.monotonic()
        first_command = len(self.commands)
        transcript: list[dict] = []
        samples: list[dict] = []
        prior = masked = after = b""
        original_mask: int | None = None
        mask_attempted = False
        mask_stayed_set = True
        restored = False
        error: str | None = None
        infrastructure_error = False
        old_timeout = connection.gettimeout()

        def collect(seconds: float, phase: str, *, check_mask: bool = False, reply_tail: bool = False) -> bytes:
            nonlocal mask_stayed_set
            received = bytearray()
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if check_mask:
                    sample = self.read_port(PIC_MASK)
                    samples.append({"master_mask": sample, "at_ms": round((time.monotonic() - began) * 1000, 3)})
                    mask_stayed_set &= bool(sample & IRQ4_BIT)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                connection.settimeout(min(0.02, remaining))
                try:
                    data = connection.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError("UART peer closed during IRQ4 probe")
                received.extend(data)
                transcript.append({"direction": "guest_to_host", "phase": phase, "hex": data.hex(),
                                   "at_ms": round((time.monotonic() - began) * 1000, 3)})
                if reply_tail and len(received) >= len(expected):
                    deadline = min(deadline, time.monotonic() + quiet_seconds)
            return bytes(received)

        try:
            prior = collect(quiet_seconds, "before_mask")
            original_mask = self.read_port(PIC_MASK)
            if original_mask & IRQ4_BIT:
                raise _BehaviorFailure("IRQ4 was already masked before the dependence probe")
            mask_attempted = True
            self.write_port(PIC_MASK, original_mask | IRQ4_BIT)
            applied_mask = self.read_port(PIC_MASK)
            mask_stayed_set &= bool(applied_mask & IRQ4_BIT)
            samples.append({"master_mask": applied_mask, "at_ms": round((time.monotonic() - began) * 1000, 3)})
            connection.settimeout(timeout)
            connection.sendall(frame)
            transcript.append({"direction": "host_to_guest", "phase": "masked", "hex": frame.hex(),
                               "at_ms": round((time.monotonic() - began) * 1000, 3)})
            masked = collect(masked_seconds, "masked", check_mask=True)
        except _BehaviorFailure as exc:
            error = str(exc)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            infrastructure_error = True
        finally:
            if mask_attempted and original_mask is not None:
                try:
                    self.write_port(PIC_MASK, original_mask)
                    restored = self.read_port(PIC_MASK) == original_mask
                except Exception as exc:
                    error = (error + "; " if error else "") + f"PIC restoration failed: {type(exc).__name__}: {exc}"
                    infrastructure_error = True
            try:
                if mask_attempted and restored:
                    after = collect(timeout, "after_restore", reply_tail=True)
            except Exception as exc:
                error = (error + "; " if error else "") + f"{type(exc).__name__}: {exc}"
                infrastructure_error = True
            finally:
                try:
                    connection.settimeout(old_timeout)
                except OSError as exc:
                    error = (error + "; " if error else "") + f"Socket state restoration failed: {exc}"
                    infrastructure_error = True
        passed = not error and not prior and not masked and mask_stayed_set and restored and after == expected
        if passed:
            reason = "Silent with IRQ4 masked; exact reply after restoring the PIC mask without resending"
        elif error:
            reason = error
        elif prior:
            reason = "Unsolicited UART bytes before the probe"
        elif masked:
            reason = "UART replied while IRQ4 was masked"
        elif not mask_stayed_set:
            reason = "IRQ4 mask did not remain set during the masked phase"
        elif not restored:
            reason = "Host could not confirm PIC mask restoration"
        else:
            reason = "Reply after PIC restoration differed from the exact expected bytes"
        return {
            "type": "host_irq4_dependence",
            "status": "error" if infrastructure_error else ("passed" if passed else "failed"),
            "infrastructure_error": infrastructure_error, "reason": reason,
            "seed_hex": format(chosen_seed, "x"), "request_hex": frame.hex(), "request_bytes": len(frame),
            "expected_reply_hex": expected.hex(), "received_before_hex": prior.hex(),
            "received_masked_hex": masked.hex(), "received_after_restore_hex": after.hex(),
            "irq4_mask_stayed_set": mask_stayed_set, "original_master_mask": original_mask,
            "pic_mask_restored": restored, "mask_samples": samples, "transcript": transcript,
            "host_commands": self.commands[first_command:],
            "elapsed_ms": round((time.monotonic() - began) * 1000, 3),
            "limitations": ["IRQ4-dependent behavior is not a formal proof that the implementation contains no polling.",
                            "Mask checks sample host observations; they do not continuously monitor every guest port write."],
        }


def compare_restoration(before: dict, after: dict) -> dict:
    """Compare the required readable state; unrelated PIC bits are diagnostic."""
    paths = [
        ("interrupt_vector", "bytes_hex"),
        ("pic", "irq4_masked"),
        *(("uart", field) for field in ("lcr", "mcr", "ier", "divisor_low", "divisor_high", "fifo_enabled", "scratch")),
    ]
    checks = []
    missing = object()
    for group, field in paths:
        initial = before.get(group, {}).get(field, missing)
        final = after.get(group, {}).get(field, missing)
        passed = initial is not missing and final is not missing and type(initial) is type(final) and initial == final
        checks.append({"field": f"{group}.{field}", "status": "passed" if passed else "failed",
                       "before": None if initial is missing else initial, "after": None if final is missing else final})
    passed = all(check["status"] == "passed" for check in checks)
    return {"type": "host_uart_restoration", "status": "passed" if passed else "failed", "checks": checks,
            "reason": "Readable interrupt/PIC/UART state restored" if passed else "Required hardware state differs or is missing",
            "limitations": ["Only the saved PIC IRQ4 bit is compared; other IRQ masks belong to other devices.",
                            "Write-only FIFO trigger configuration and FIFO contents cannot be verified by these reads."]}
