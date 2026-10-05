"""Host-owned behavioral grading for the TempleOS ring-0 UART task.

Challenges are created after model actions end. This module observes bytes from
QEMU's serial socket; guest text and counters are never evidence of success.
The seed and byte transcript belong in host artifacts, not model observations.
Passing this judge demonstrates the protocol, not an interrupt-only driver.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import random
import re
import socket
import time
import zlib


def request_frame(seq: int, payload: bytes) -> bytes:
    """Encode A5, sequence, length, payload, and an additive checksum."""
    if type(seq) is not int or not 0 <= seq <= 255:
        raise ValueError("sequence must be an integer in 0..255")
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= 64:
        raise ValueError("payload must be 1..64 bytes")
    body = bytes((seq, len(payload))) + payload
    return b"\xa5" + body + bytes((sum(body) & 255,))


def expected_reply(seq: int, payload: bytes) -> bytes:
    """Encode 5A, sequence, little-endian IEEE CRC32, and checksum."""
    # Validate both values against the published request contract.
    request_frame(seq, payload)
    body = bytes((seq,)) + zlib.crc32(payload).to_bytes(4, "little")
    return b"\x5a" + body + bytes((sum(body) & 255,))


class ReplyParser:
    """Incrementally recover seven-byte replies without hiding bad wire data.

    Valid frames and anomalies are both returned. In particular, an unexpected
    sequence is returned to the grader; it is never silently filtered out.
    """

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> tuple[list[bytes], list[dict]]:
        self.buffer.extend(data)
        frames: list[bytes] = []
        anomalies: list[dict] = []
        while self.buffer:
            try:
                header = self.buffer.index(0x5A)
            except ValueError:
                anomalies.append({"kind": "unexpected_bytes", "hex": self.buffer.hex()})
                self.buffer.clear()
                break
            if header:
                anomalies.append({"kind": "unexpected_bytes", "hex": self.buffer[:header].hex()})
                del self.buffer[:header]
            if len(self.buffer) < 7:
                break
            candidate = bytes(self.buffer[:7])
            if sum(candidate[1:6]) & 255 != candidate[6]:
                anomalies.append({"kind": "bad_reply_checksum", "hex": candidate.hex()})
                # Advance one byte, since a valid header may occur inside the
                # malformed candidate or immediately following it.
                del self.buffer[0]
                continue
            frames.append(candidate)
            del self.buffer[:7]
        return frames, anomalies

    def finish(self) -> list[dict]:
        if not self.buffer:
            return []
        anomaly = {"kind": "incomplete_reply", "hex": self.buffer.hex()}
        self.buffer.clear()
        return [anomaly]


@dataclass(frozen=True)
class _Case:
    name: str
    chunks: tuple[bytes, ...]
    replies: tuple[bytes, ...]
    fragment_gap: float = 0.0


class UARTJudge:
    """Grade one frozen model solution through QEMU's Unix serial socket.

    ``seed=None`` uses an unpredictable OS-generated 256-bit seed. Explicit
    seeds are intended for regression tests and replay, not a fixed RL dataset.
    Any unsolicited bytes, duplicate replies, bad CRCs, or missing replies fail
    the case. The score is the fraction of eight complete cases that passed.
    """

    def __init__(self, socket_path: Path, *, case_timeout: float = 2.0,
                 seed: int | None = None, quiet_seconds: float = 0.05):
        for name, value in (("case_timeout", case_timeout), ("quiet_seconds", quiet_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        if seed is not None and (type(seed) is not int or seed < 0):
            raise ValueError("seed must be a nonnegative integer or None")
        self.socket_path = Path(socket_path)
        self.case_timeout = float(case_timeout)
        self.quiet_seconds = float(quiet_seconds)
        self.seed = seed if seed is not None else int.from_bytes(os.urandom(32), "big")
        self._socket: socket.socket | None = None

    def connect(self, timeout: float = 5.0) -> UARTJudge:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("connection timeout must be a positive finite number")
        if self._socket is not None:
            return self
        deadline = time.monotonic() + timeout
        while True:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                connection.connect(str(self.socket_path))
                self._socket = connection
                return self
            except (FileNotFoundError, ConnectionRefusedError):
                connection.close()
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"UART socket did not accept a connection: {self.socket_path}")
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            except BaseException:
                connection.close()
                raise

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> UARTJudge:
        return self.connect()

    def __exit__(self, *_: object) -> None:
        self.close()

    def _cases(self) -> list[_Case]:
        rng = random.Random(self.seed)
        sequences = iter(rng.sample(range(256), 17))

        def payload(size: int) -> bytes:
            return bytes(rng.randrange(256) for _ in range(size))

        def valid(name: str, data: bytes) -> _Case:
            seq = next(sequences)
            return _Case(name, (request_frame(seq, data),), (expected_reply(seq, data),))

        cases = [valid("valid_short", payload(1)), valid("random_payload_64", payload(64))]
        seq, data = next(sequences), payload(31)
        frame = request_frame(seq, data)
        cases.append(_Case("fragmented_request", (frame[:1], frame[1:3], frame[3:17], frame[17:]),
                           (expected_reply(seq, data),), 0.015))
        burst = [(next(sequences), payload(rng.randint(1, 64))) for _ in range(8)]
        cases.append(_Case("back_to_back_burst_8", (b"".join(request_frame(s, p) for s, p in burst),),
                           tuple(expected_reply(s, p) for s, p in burst)))
        seq, data = next(sequences), payload(19)
        bad = bytearray(request_frame(seq, data))
        bad[-1] ^= 1
        recovery_seq, recovery_data = next(sequences), payload(13)
        cases.append(_Case("bad_checksum_then_valid", (bytes(bad), request_frame(recovery_seq, recovery_data)),
                           (expected_reply(recovery_seq, recovery_data),), 0.015))
        cases.append(valid("valid_after_bad_checksum", payload(23)))
        bad_seq, seq, data = next(sequences), next(sequences), payload(11)
        cases.append(_Case("invalid_length_then_valid", (bytes((0xA5, bad_seq, rng.randint(65, 255)))
                                                        + request_frame(seq, data),),
                           (expected_reply(seq, data),)))
        cases.append(valid("embedded_header_payload", b"\xa5\x00\xa5\x5a\xff\x00" + payload(37)))
        return cases

    def _run_case(self, case: _Case) -> dict:
        assert self._socket is not None
        connection = self._socket
        started = time.monotonic()
        deadline = started + self.case_timeout
        parser = ReplyParser()
        received: list[bytes] = []
        anomalies: list[dict] = []
        transcript: list[dict] = []
        first_reply_ms: float | None = None
        error = None
        infrastructure_error = False
        try:
            for index, chunk in enumerate(case.chunks):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Request transmission exceeded case deadline")
                connection.settimeout(remaining)
                connection.sendall(chunk)
                transcript.append({"direction": "host_to_guest", "hex": chunk.hex(),
                                   "at_ms": round((time.monotonic() - started) * 1000, 3)})
                if index < len(case.chunks) - 1 and case.fragment_gap:
                    time.sleep(min(case.fragment_gap, max(0, deadline - time.monotonic())))
            # Valid cases have a brief quiet tail to detect duplicate replies.
            # Rejection cases wait the whole deadline to observe delayed output.
            receive_deadline = deadline
            while time.monotonic() < receive_deadline:
                connection.settimeout(max(0.001, receive_deadline - time.monotonic()))
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    raise ConnectionError("UART peer closed during grading")
                at_ms = round((time.monotonic() - started) * 1000, 3)
                transcript.append({"direction": "guest_to_host", "hex": chunk.hex(), "at_ms": at_ms})
                frames, bad_data = parser.feed(chunk)
                received.extend(frames)
                anomalies.extend(bad_data)
                if frames and first_reply_ms is None:
                    first_reply_ms = at_ms
                if case.replies and len(received) >= len(case.replies):
                    receive_deadline = min(deadline, time.monotonic() + self.quiet_seconds)
        except (OSError, TimeoutError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            # A normal receive timeout is handled above as absent behavioral
            # evidence. Transport loss or inability to finish transmitting a
            # challenge leaves the host oracle incomplete, not a model zero.
            infrastructure_error = True
        anomalies.extend(parser.finish())
        passed = not error and not anomalies and received == list(case.replies)
        if passed:
            reason = "Exact host-validated replies" if case.replies else "No response to invalid request"
        elif error:
            reason = error
        elif anomalies:
            reason = "Malformed or unsolicited UART output"
        else:
            reason = "Replies differ from the expected ordered byte stream"
        return {"name": case.name, "status": "passed" if passed else "failed", "reason": reason,
                "latency_ms": first_reply_ms,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                "expected_replies_hex": [frame.hex() for frame in case.replies],
                "received_replies_hex": [frame.hex() for frame in received],
                "anomalies": anomalies, "transcript": transcript,
                "infrastructure_error": infrastructure_error}

    def run(self) -> dict:
        if self._socket is None:
            raise RuntimeError("Connect UARTJudge before running the evaluation")
        cases = [self._run_case(case) for case in self._cases()]
        passed = sum(case["status"] == "passed" for case in cases)
        return {"type": "host_uart", "status": "passed" if passed == len(cases) else "failed",
                "score": passed / len(cases), "passed_cases": passed, "total_cases": len(cases),
                "seed_hex": format(self.seed, "x"), "case_timeout_seconds": self.case_timeout,
                "reason": f"{passed}/{len(cases)} host UART cases passed",
                "cases": cases,
                "limitations": ["Behavioral serial protocol test; does not prove IRQ-only processing.",
                                "Seed and byte transcript must remain hidden from the policy until grading ends."]}


def summarize_trace(path: Path) -> dict:
    """Summarize single-UART QEMU trace configuration as diagnostics only.

    UART offsets 0/1 alias divisor registers while LCR.DLAB is set. PIC data
    writes during initialization are configuration words, not interrupt masks.
    Neither an enabled IER nor a delivered IRQ proves that the handler parsed
    requests. Traces from multiple UARTs are ambiguous because QEMU's serial
    events do not identify the device.
    """
    serial = re.compile(r"\bserial_(read|write)\s+\w+\s+addr\s+(0x[\da-fA-F]+|\d+)\s+val\s+(0x[\da-fA-F]+|\d+)")
    pic = re.compile(r"\bpic_ioport_(read|write)\s+master\s+1\s+addr\s+(0x[\da-fA-F]+|\d+)\s+val\s+(0x[\da-fA-F]+|\d+)")
    interrupt = re.compile(r"\bpic_interrupt\s+irq\s+4\s+intno\s+(0x[\da-fA-F]+|\d+)")
    lcr = 0
    ier = None
    master_mask = None
    init_remaining = 0
    rx_reads = tx_writes = irq4 = lines = 0
    with Path(path).open(errors="replace") as source:
        for line in source:
            lines += 1
            match = serial.search(line)
            if match:
                operation, address, value = match.groups()
                address, value = int(address, 0), int(value, 0)
                if address == 3:
                    lcr = value
                elif address == 1 and not lcr & 0x80:
                    ier = value
                elif address == 0 and not lcr & 0x80:
                    rx_reads += operation == "read"
                    tx_writes += operation == "write"
                continue
            match = pic.search(line)
            if match:
                operation, address, value = match.groups()
                address, value = int(address, 0), int(value, 0)
                if operation == "write" and address == 0 and value & 0x10:
                    init_remaining = 1 + (not value & 2) + bool(value & 1)
                    master_mask = None
                elif address == 1:
                    if operation == "write" and init_remaining:
                        init_remaining -= 1
                    elif not init_remaining:
                        master_mask = value
                continue
            match = interrupt.search(line)
            if match and int(match.group(1), 0) == 0x24:
                irq4 += 1
    return {"type": "qemu_uart_trace_diagnostic", "lines": lines,
            "last_uart_ier": ier, "rx_interrupt_enabled": None if ier is None else bool(ier & 1),
            "last_master_pic_mask": master_mask,
            "irq4_unmasked": None if master_mask is None else not bool(master_mask & 0x10),
            "delivered_irq4_vector_0x24": irq4, "uart_rx_register_reads": rx_reads,
            "uart_tx_register_writes": tx_writes,
            "limitations": ["Configuration and delivered interrupts do not prove IRQ-only processing.",
                            "Serial trace offsets assume this VM has one UART and tracing covers its setup."]}
