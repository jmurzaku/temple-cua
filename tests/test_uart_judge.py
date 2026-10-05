"""External byte-level tests, including plausible false success shortcuts."""

from contextlib import contextmanager
from pathlib import Path
import socket
import threading
import time

import pytest

from temple_cua.uart_judge import ReplyParser, UARTJudge, expected_reply, request_frame, summarize_trace


class FakeUART:
    """A socket peer emulating protocol behavior, independent of the judge."""

    def __init__(self, path: Path, *, mode="correct", split_replies=False):
        self.path = path
        self.mode = mode
        self.split_replies = split_replies
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        self.listener.listen(1)
        self.listener.settimeout(1)
        self.peer = None
        self.stopped = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _write(self, frame):
        if self.split_replies:
            for part in (frame[:1], frame[1:4], frame[4:]):
                self.peer.sendall(part)
                time.sleep(0.0005)
        else:
            self.peer.sendall(frame)

    def _serve(self):
        pending = bytearray()
        count = 0
        try:
            self.peer, _ = self.listener.accept()
            self.peer.settimeout(0.05)
            while not self.stopped.is_set():
                try:
                    chunk = self.peer.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                pending.extend(chunk)
                while pending:
                    try:
                        start = pending.index(0xA5)
                    except ValueError:
                        pending.clear()
                        break
                    del pending[:start]
                    if len(pending) < 3:
                        break
                    size = pending[2]
                    if not 1 <= size <= 64:
                        del pending[:3]
                        continue
                    if len(pending) < size + 4:
                        break
                    frame = bytes(pending[:size + 4])
                    del pending[:size + 4]
                    seq, data = frame[1], frame[3:-1]
                    valid = sum(frame[1:-1]) & 255 == frame[-1]
                    if self.mode == "silence":
                        continue
                    if not valid and self.mode != "respond_bad_checksum":
                        continue
                    reply = expected_reply(seq, data)
                    count += 1
                    if self.mode == "wrong_crc" and count == 1:
                        body = bytearray(reply[1:6])
                        body[1] ^= 1
                        reply = b"\x5a" + bytes(body) + bytes((sum(body) & 255,))
                    if self.mode == "wrong_sequence" and count == 1:
                        reply = expected_reply((seq + 1) & 255, data)
                    if self.mode == "noise" and count == 1:
                        reply = b"PASS\n" + reply
                    self._write(reply)
                    if self.mode == "duplicate" and count == 1:
                        self._write(reply)
        except (OSError, TimeoutError) as exc:
            if not self.stopped.is_set():
                self.error = exc

    def close(self):
        self.stopped.set()
        if self.peer is not None:
            try:
                self.peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.peer.close()
        self.listener.close()
        self.thread.join(timeout=1)


@contextmanager
def connected_judge(tmp_path, *, mode="correct", split_replies=False, seed=81):
    peer = FakeUART(tmp_path / "uart.sock", mode=mode, split_replies=split_replies).start()
    judge = UARTJudge(peer.path, seed=seed, case_timeout=0.12, quiet_seconds=0.005)
    try:
        judge.connect(timeout=1)
        yield judge
    finally:
        judge.close()
        peer.close()
    assert peer.error is None


def test_ieee_crc_known_vector_and_wire_format():
    # CRC-32/ISO-HDLC("123456789") = CBF43926. Wire CRC is little endian.
    assert expected_reply(1, b"123456789") == bytes.fromhex("5a012639f4cb1f")
    request = request_frame(7, b"\x00\xa5\xff")
    assert request == bytes((0xA5, 7, 3, 0, 0xA5, 0xFF, (7 + 3 + 0xA5 + 0xFF) & 255))


@pytest.mark.parametrize("seq,payload", [(-1, b"a"), (256, b"a"), (True, b"a"),
                                          (1, b""), (1, b"a" * 65), (1, "a")])
def test_protocol_rejects_invalid_inputs(seq, payload):
    with pytest.raises(ValueError):
        request_frame(seq, payload)
    with pytest.raises(ValueError):
        expected_reply(seq, payload)


def test_reply_parser_preserves_corruption_then_recovers_frames():
    first = expected_reply(0x5A, b"\xa5\x00\xff")
    second = expected_reply(7, b"next")
    parser = ReplyParser()
    frames, bad = parser.feed(b"junk" + bytes.fromhex("5a010203040500") + first[:3])
    assert not frames
    assert any(item["kind"] == "unexpected_bytes" for item in bad)
    assert any(item["kind"] == "bad_reply_checksum" for item in bad)
    frames, bad = parser.feed(first[3:] + second)
    assert frames == [first, second]
    assert not bad
    assert not parser.finish()


def test_reply_parser_records_incomplete_response():
    parser = ReplyParser()
    parser.feed(b"\x5a\x19\x00")
    assert parser.finish() == [{"kind": "incomplete_reply", "hex": "5a1900"}]


def test_correct_fragmented_socket_peer_passes_all_cases(tmp_path):
    with connected_judge(tmp_path, split_replies=True) as judge:
        result = judge.run()
    assert result["status"] == "passed"
    assert result["score"] == 1.0
    assert result["total_cases"] == result["passed_cases"] == 8
    assert all(case["latency_ms"] is not None for case in result["cases"])
    burst = next(case for case in result["cases"] if case["name"] == "back_to_back_burst_8")
    assert len(burst["received_replies_hex"]) == 8
    assert all("transcript" in case for case in result["cases"])
    assert "does not prove IRQ" in result["limitations"][0]


def test_inactive_driver_earns_zero_including_rejection_cases(tmp_path):
    with connected_judge(tmp_path, mode="silence") as judge:
        result = judge.run()
    assert result["status"] == "failed"
    assert result["score"] == 0
    assert all(case["expected_replies_hex"] for case in result["cases"])


@pytest.mark.parametrize("mode", ["wrong_crc", "wrong_sequence", "noise", "duplicate"])
def test_strict_grading_rejects_false_success_but_retains_later_success(tmp_path, mode):
    with connected_judge(tmp_path, mode=mode) as judge:
        result = judge.run()
    assert result["status"] == "failed"
    assert result["score"] == 7 / 8
    assert result["cases"][0]["status"] == "failed"
    assert all(case["status"] == "passed" for case in result["cases"][1:])
    if mode == "noise":
        assert result["cases"][0]["anomalies"]
    if mode == "wrong_sequence":
        assert result["cases"][0]["received_replies_hex"]


def test_invalid_checksum_must_be_rejected_and_service_must_recover(tmp_path):
    with connected_judge(tmp_path, mode="respond_bad_checksum") as judge:
        result = judge.run()
    case = next(case for case in result["cases"] if case["name"] == "bad_checksum_then_valid")
    assert case["status"] == "failed"
    assert len(case["received_replies_hex"]) == 2
    assert result["score"] == 7 / 8


def test_seed_is_reproducible_and_other_seeds_change_hidden_cases(tmp_path):
    first = UARTJudge(tmp_path / "one", seed=42)._cases()
    same = UARTJudge(tmp_path / "two", seed=42)._cases()
    other = UARTJudge(tmp_path / "three", seed=43)._cases()
    assert first == same
    assert all(a.chunks != b.chunks for a, b in zip(first, other))
    assert UARTJudge(tmp_path / "live").seed != UARTJudge(tmp_path / "live2").seed


def test_no_connection_and_missing_socket_are_explicit_failures(tmp_path):
    judge = UARTJudge(tmp_path / "missing", seed=1)
    with pytest.raises(RuntimeError, match="Connect"):
        judge.run()
    with pytest.raises(TimeoutError, match="did not accept"):
        judge.connect(timeout=0.01)


@pytest.mark.parametrize("kwargs", [{"case_timeout": 0}, {"case_timeout": float("inf")},
                                    {"quiet_seconds": -1}, {"seed": True}, {"seed": -1}])
def test_invalid_judge_configuration_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        UARTJudge(tmp_path / "unused", **kwargs)


def test_trace_diagnostic_handles_divisor_alias_and_pic_initialization(tmp_path):
    trace = tmp_path / "uart.log"
    trace.write_text("\n".join([
        "serial_write write addr 0x03 val 0x80",  # DLAB, not RX enable
        "serial_write write addr 0x01 val 0x01",
        "serial_write write addr 0x00 val 0x01",  # divisor, not transmitted data
        "serial_write write addr 0x03 val 0x03",
        "serial_write write addr 0x01 val 0x01",  # real IER
        "serial_read read addr 0x00 val 0xa5",
        "serial_write write addr 0x00 val 0x5a",
        "pic_ioport_write master 1 addr 0x0 val 0x11",
        "pic_ioport_write master 1 addr 0x1 val 0x20",
        "pic_ioport_write master 1 addr 0x1 val 0x04",
        "pic_ioport_write master 1 addr 0x1 val 0x0d",
        "pic_ioport_write master 1 addr 0x1 val 0xe8",
        "pic_interrupt irq 4 intno 36",
        "pic_interrupt irq 0 intno 32",
        "pic_interrupt irq 4 intno 0x24",
    ]) + "\n")
    diagnostic = summarize_trace(trace)
    assert diagnostic["rx_interrupt_enabled"] is True
    assert diagnostic["irq4_unmasked"] is True
    assert diagnostic["uart_rx_register_reads"] == 1
    assert diagnostic["uart_tx_register_writes"] == 1
    assert diagnostic["delivered_irq4_vector_0x24"] == 2
    assert "score" not in diagnostic
    assert "do not prove" in diagnostic["limitations"][0]


def test_trace_diagnostic_never_confuses_divisor_write_for_rx_enable(tmp_path):
    trace = tmp_path / "uart.log"
    trace.write_text("serial_write write addr 0x03 val 0x80\n"
                     "serial_write write addr 0x01 val 0x01\n"
                     "pic_ioport_write master 1 addr 0x0 val 0x11\n"
                     "pic_ioport_write master 1 addr 0x1 val 0x20\n")
    diagnostic = summarize_trace(trace)
    assert diagnostic["rx_interrupt_enabled"] is None
    assert diagnostic["irq4_unmasked"] is None
