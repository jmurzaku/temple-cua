"""Hardware observations and causal masking, including plausible false passes."""

from contextlib import contextmanager
from copy import deepcopy
import socket
import threading
from types import SimpleNamespace

import pytest

from temple_cua.uart_hardware import (
    COM1, IRQ4_BIT, PIC_MASK, UARTHardwareError, UARTHardwareProbe, compare_restoration,
)
from temple_cua.uart_judge import expected_reply


class Monitor:
    """Model register aliases, not the implementation's parsing or decisions."""

    def __init__(self, *, running=True):
        self.running = running
        self.lcr = 0x83
        self.ier = 0x05
        self.divisor = (0x34, 0x12)
        self.mask = 0xE4
        self.descriptor = bytes(range(16))
        self.calls = []
        self.fail_command = None
        self.on_unmask = None

    def execute(self, command, arguments=None, *, timeout=None):
        self.calls.append((command, arguments))
        if command == "query-status":
            return {"running": self.running}
        if command in {"stop", "cont"}:
            self.running = command == "cont"
            return {}
        assert command == "human-monitor-command"
        text = arguments["command-line"]
        if text == self.fail_command:
            raise RuntimeError("injected monitor failure")
        if text == "info registers":
            return "CPU#0\nIDT=     0000000000100000 00000fff\n"
        if text.startswith("xp "):
            assert text == "xp /16bx 0x100240"
            return "\n".join(f"{0x100240 + start:016x}: " + " ".join(f"0x{x:02x}" for x in self.descriptor[start:start+8]) for start in (0, 8)) + "\n"
        parts = text.split()
        operation, fmt, port = parts[:3]
        assert fmt == "/b"
        port = int(port, 16)
        if operation == "i":
            if port == PIC_MASK:
                value = self.mask
            elif port == COM1 + 3:
                value = self.lcr
            elif port == COM1 + 1:
                value = self.divisor[1] if self.lcr & 0x80 else self.ier
            elif port == COM1:
                assert self.lcr & 0x80, "A destructive RX FIFO read must never occur during capture"
                value = self.divisor[0]
            else:
                value = {COM1 + 2: 0xC1, COM1 + 4: 0x0B, COM1 + 7: 0xA7}[port]
            return f"portb[0x{port:04x}] = 0x{value:02x}\r\n"
        assert operation == "o"
        value = int(parts[3], 16)
        if port == PIC_MASK:
            was_masked = bool(self.mask & IRQ4_BIT)
            self.mask = value
            if was_masked and not value & IRQ4_BIT and self.on_unmask:
                self.on_unmask()
        else:
            assert port == COM1 + 3
            self.lcr = value
        return ""


def probe(monitor, connection=None):
    return UARTHardwareProbe(SimpleNamespace(qmp=monitor), SimpleNamespace(_socket=connection))


def test_snapshot_reads_descriptor_and_divisor_aliases_without_consuming_rx():
    monitor = Monitor()
    state = probe(monitor).capture_state()
    assert state["interrupt_vector"]["bytes_hex"] == bytes(range(16)).hex()
    assert state["pic"] == {"master_mask": 0xE4, "irq4_masked": False}
    assert state["uart"] == {
        "lcr": 0x83, "mcr": 0x0B, "ier": 5, "divisor_low": 0x34,
        "divisor_high": 0x12, "fifo_enabled": True, "scratch": 0xA7,
    }
    assert monitor.lcr == 0x83
    assert monitor.running
    assert [command for command, _ in monitor.calls if command != "human-monitor-command"] == ["query-status", "stop", "cont"]


def test_snapshot_keeps_an_already_paused_vm_paused():
    monitor = Monitor(running=False)
    probe(monitor).capture_state()
    assert not monitor.running
    assert not any(command in {"stop", "cont"} for command, _ in monitor.calls)


def test_capture_failure_restores_lcr_and_resumes_guest():
    monitor = Monitor()
    monitor.lcr = 3
    monitor.fail_command = "i /b 0x3f8"
    with pytest.raises(RuntimeError, match="injected"):
        probe(monitor).capture_state()
    assert monitor.lcr == 3
    assert monitor.running


def test_malformed_monitor_observations_fail_instead_of_becoming_zero():
    monitor = SimpleNamespace(execute=lambda *args, **kwargs: "unknown command")
    with pytest.raises(UARTHardwareError, match="parse"):
        probe(monitor).read_port(PIC_MASK)


def test_restoration_catches_descriptor_and_readable_uart_damage():
    before = probe(Monitor()).capture_state()
    assert compare_restoration(before, deepcopy(before))["status"] == "passed"
    for group, field, value in [("interrupt_vector", "bytes_hex", "00"*16), ("pic", "irq4_masked", True),
                                ("uart", "ier", 0), ("uart", "divisor_low", 1), ("uart", "fifo_enabled", False)]:
        after = deepcopy(before)
        after[group][field] = value
        result = compare_restoration(before, after)
        assert result["status"] == "failed"
        assert any(c["field"] == f"{group}.{field}" and c["status"] == "failed" for c in result["checks"])
    after = deepcopy(before)
    after["pic"]["master_mask"] ^= 2
    assert compare_restoration(before, after)["status"] == "passed"
    assert compare_restoration({}, {})["status"] == "failed"


@contextmanager
def serial_peer(mode="irq", *, running=True):
    monitor = Monitor(running=running)
    client, peer = socket.socketpair()
    client.settimeout(0.7)
    pending = []
    requests = []
    stopped = threading.Event()
    lock = threading.Lock()
    errors = []

    def emit(frame):
        seq, data = frame[1], frame[3:-1]
        reply = expected_reply(seq, data)
        if mode == "wrong_crc":
            reply = expected_reply(seq, data + b"wrong")
        peer.sendall(reply + (reply if mode == "duplicate" else b""))

    def release():
        with lock:
            for frame in pending:
                emit(frame)
            pending.clear()

    monitor.on_unmask = release

    def serve():
        data = bytearray()
        peer.settimeout(0.03)
        try:
            while not stopped.is_set():
                try:
                    chunk = peer.recv(128)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                data.extend(chunk)
                while len(data) >= 3 and len(data) >= data[2] + 4:
                    count = data[2] + 4
                    frame = bytes(data[:count])
                    del data[:count]
                    requests.append(frame)
                    assert sum(frame[1:-1]) & 255 == frame[-1]
                    if mode == "silence":
                        continue
                    if mode == "interference":
                        monitor.mask &= ~IRQ4_BIT
                    with lock:
                        if monitor.mask & IRQ4_BIT and mode != "polling":
                            pending.append(frame)
                        else:
                            emit(frame)
        except OSError as exc:
            if not stopped.is_set():
                errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield probe(monitor, client), monitor, requests
    finally:
        stopped.set()
        client.close()
        peer.close()
        thread.join(timeout=1)
    assert not thread.is_alive()
    assert not errors


def run_case(hardware):
    return hardware.irq4_dependence(seed=81, masked_seconds=0.04, timeout=0.12, quiet_seconds=0.01)


def test_irq_dependent_peer_replies_only_after_restore_without_another_send():
    with serial_peer() as (hardware, monitor, requests):
        result = run_case(hardware)
        assert result["status"] == "passed"
        assert result["received_masked_hex"] == ""
        assert result["received_after_restore_hex"] == result["expected_reply_hex"]
        assert len(requests) == 1
        assert len(requests[0]) == result["request_bytes"] == 10
        assert monitor.mask == result["original_master_mask"] == 0xE4
        assert hardware.judge._socket.gettimeout() == 0.7
        assert sum(t["direction"] == "host_to_guest" for t in result["transcript"]) == 1


@pytest.mark.parametrize("mode", ["polling", "wrong_crc", "duplicate", "silence", "interference"])
def test_masking_rejects_false_positive_services_and_restores_original_mask(mode):
    with serial_peer(mode) as (hardware, monitor, requests):
        result = run_case(hardware)
        assert result["status"] == "failed"
        assert result["infrastructure_error"] is False
        assert len(requests) == 1
        assert monitor.mask == 0xE4
        assert result["pic_mask_restored"]
        if mode == "polling":
            assert result["received_masked_hex"]
        if mode == "interference":
            assert not result["irq4_mask_stayed_set"]


def test_already_masked_driver_fails_without_host_enabling_it():
    with serial_peer() as (hardware, monitor, requests):
        monitor.mask |= IRQ4_BIT
        result = run_case(hardware)
        assert result["status"] == "failed"
        assert "already masked" in result["reason"]
        assert result["infrastructure_error"] is False
        assert not requests
        assert monitor.mask == 0xF4


def test_port_and_probe_validation():
    hardware = probe(Monitor())
    for value in (True, -1, 0x10000):
        with pytest.raises(ValueError):
            hardware.read_port(value)
    for value in (True, -1, 256):
        with pytest.raises(ValueError):
            hardware.write_port(COM1, value)
    with pytest.raises(ValueError):
        hardware.irq4_dependence(masked_seconds=0)
    result = hardware.irq4_dependence()
    assert result["status"] == "error"
    assert result["infrastructure_error"]
    assert "Connect" in result["reason"]


def test_monitor_failure_is_infrastructure_error_not_zero_behavior_score():
    with serial_peer() as (hardware, monitor, requests):
        monitor.fail_command = "i /b 0x21"
        result = run_case(hardware)
        assert result["status"] == "error"
        assert result["infrastructure_error"]
        assert "monitor failure" in result["reason"]
        assert not requests
        assert monitor.mask == 0xE4


def test_monitor_failure_after_mask_write_still_restores_pic_and_socket(monkeypatch):
    with serial_peer() as (hardware, monitor, requests):
        execute = monitor.execute
        failed = False

        def fail_once(command, arguments=None, *, timeout=None):
            nonlocal failed
            if (command == "human-monitor-command" and arguments["command-line"] == "i /b 0x21"
                    and monitor.mask & IRQ4_BIT and not failed):
                failed = True
                raise RuntimeError("masked read failed")
            return execute(command, arguments, timeout=timeout)

        monkeypatch.setattr(monitor, "execute", fail_once)
        result = run_case(hardware)
        assert failed
        assert result["status"] == "error" and result["infrastructure_error"]
        assert result["pic_mask_restored"]
        assert monitor.mask == 0xE4
        assert hardware.judge._socket.gettimeout() == 0.7
        assert not requests
