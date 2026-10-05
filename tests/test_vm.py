"""Transport and keyboard tests; guest behavior is covered by the smoke run."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import socket
import threading
import time

import pytest

from temple_cua.qmp import QMPClient, QMPError, QMPTimeout
from temple_cua.vm import TempleVM, VMConfig, VMError, VMInputError, text_to_chords


def test_ascii_keyboard_covers_punctuation_without_pasting():
    for value in range(32, 127):
        assert text_to_chords(chr(value))
    assert text_to_chords('Print("Hi!\\n");\n') == [
        ["shift", "p"], ["r"], ["i"], ["n"], ["t"], ["shift", "9"],
        ["shift", "apostrophe"], ["shift", "h"], ["i"], ["shift", "1"],
        ["backslash"], ["n"], ["shift", "apostrophe"], ["shift", "0"],
        ["semicolon"], ["ret"],
    ]


def test_keyboard_rejects_non_ascii_before_sending_any_text():
    with pytest.raises(VMInputError, match="US ASCII"):
        text_to_chords("partial input then \u00e9")
    assert text_to_chords("one\r\ntwo\r")[-1] == ["ret"]
    assert len(text_to_chords("\r\n")) == 1


def test_invalid_chord_is_rejected_before_any_keys_are_pressed(tmp_path, monkeypatch):
    vm = TempleVM(VMConfig(iso=tmp_path / "guest.iso"), tmp_path / "run")
    events = []
    monkeypatch.setattr(vm, "_events", lambda payload, deadline=None: events.extend(payload))
    with pytest.raises(VMInputError):
        vm._press(["ctrl", "unsupported"])
    assert events == []


@pytest.mark.parametrize("timeout_phase", [1, 2, 3])
def test_deadline_releases_all_held_keys(tmp_path, monkeypatch, timeout_phase):
    vm = TempleVM(VMConfig(iso=tmp_path / "guest.iso"), tmp_path / "run")
    events = []
    phases = 0

    def delay(seconds, deadline=None):
        nonlocal phases
        phases += 1
        if phases == timeout_phase:
            raise TimeoutError("Task action deadline reached")

    monkeypatch.setattr(vm, "_events", lambda payload, deadline=None: events.extend(payload))
    monkeypatch.setattr(vm, "_delay", delay)
    with pytest.raises(TimeoutError, match="Task action deadline reached"):
        vm._press(["shift", "a"], deadline=time.monotonic() + 0.001)
    if timeout_phase == 1:
        assert [(e["data"]["key"]["data"], e["data"]["down"]) for e in events] == [
            ("shift", True), ("shift", False),
        ]
    else:
        assert [(e["data"]["key"]["data"], e["data"]["down"]) for e in events] == [
            ("shift", True), ("a", True), ("a", False), ("shift", False),
        ]


@pytest.mark.parametrize("keys", [["shift", "9"], ["9", "shift"], ["right_shift", "9"]])
def test_modifier_has_guest_processing_time_before_character(tmp_path, monkeypatch, keys):
    vm = TempleVM(VMConfig(iso=tmp_path / "guest.iso"), tmp_path / "run")
    clock = 0.0
    shift_ready = None
    typed = []
    transitions = []

    def events(payload, deadline=None):
        nonlocal shift_ready
        for event in payload:
            key = event["data"]["key"]["data"]
            down = event["data"]["down"]
            transitions.append((clock, key, down))
            if key in {"shift", "shift_r"}:
                # Emulate a guest that needs one processing interval to
                # latch its modifier state. A simultaneous packet loses it.
                shift_ready = clock + 0.02 if down else None
            elif key == "9" and down:
                typed.append("(" if shift_ready is not None and clock >= shift_ready else "9")

    def delay(seconds, deadline=None):
        nonlocal clock
        clock += seconds

    monkeypatch.setattr(vm, "_events", events)
    monkeypatch.setattr(vm, "_delay", delay)
    vm._press(keys)
    assert typed == ["("]
    assert transitions[1][0] - transitions[0][0] == pytest.approx(0.02)
    assert transitions[3][0] - transitions[2][0] == pytest.approx(0.02)


def test_baseline_rejects_changed_environment_or_disk(tmp_path):
    baseline = tmp_path / "baseline.qcow2"
    baseline.write_bytes(b"snapshot fixture")
    vm = TempleVM(VMConfig(iso=tmp_path / "guest.iso"), tmp_path / "run")
    vm._identity = {"memory_mb": 512}
    metadata = {
        "schema_version": 1, "identity": {"memory_mb": 512},
        "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
    }
    Path(str(baseline) + ".json").write_text(json.dumps(metadata))
    vm._validate_baseline(baseline)
    vm._identity = {"memory_mb": 1024}
    with pytest.raises(VMError, match="original ISO"):
        vm._validate_baseline(baseline)
    vm._identity = {"memory_mb": 512}
    baseline.write_bytes(b"changed snapshot fixture")
    with pytest.raises(VMError, match="hash differs"):
        vm._validate_baseline(baseline)


class FakeMonitor:
    def __init__(self, path: Path, *, stall: bool = False):
        self.path = path
        self.stall = stall
        self.error: BaseException | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def serve(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(self.path))
                listener.listen(1)
                self.ready.set()
                with listener.accept()[0] as connection:
                    connection.settimeout(2)
                    connection.sendall(b'{"QMP":{"version":{}}}\r\n')
                    with connection.makefile("rb") as reader:
                        capabilities = json.loads(reader.readline())
                        connection.sendall(json.dumps({"return": {}, "id": capabilities["id"]}).encode() + b"\n")
                        request = json.loads(reader.readline())
                        if self.stall:
                            time.sleep(0.3)
                            return
                        reply = json.dumps({"return": {"running": True}, "id": request["id"]}).encode() + b"\r\n"
                        # QMP can place an event and a split response in the
                        # same read. Framing must retain the response tail.
                        connection.sendall(b'{"event":"RESUME"}\r\n' + reply[:8])
                        connection.sendall(reply[8:])
                        failure = json.loads(reader.readline())
                        connection.sendall(json.dumps({"error": {"class": "CommandNotFound", "desc": "bad command"}, "id": failure["id"]}).encode() + b"\n")
        except BaseException as exc:
            self.error = exc

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(2)
        return self

    def __exit__(self, *_):
        self.thread.join(timeout=3)
        assert not self.thread.is_alive()
        if self.error is not None:
            raise self.error


def test_qmp_handles_interleaved_events_partial_frames_and_command_errors(tmp_path):
    with FakeMonitor(tmp_path / "qmp.sock"):
        with QMPClient(tmp_path / "qmp.sock") as client:
            assert client.execute("query-status") == {"running": True}
            with pytest.raises(QMPError, match="CommandNotFound: bad command"):
                client.execute("unknown")


def test_qmp_deadline_closes_connection(tmp_path):
    with FakeMonitor(tmp_path / "qmp.sock", stall=True):
        with QMPClient(tmp_path / "qmp.sock", timeout=0.05) as client:
            with pytest.raises(QMPTimeout):
                client.execute("query-status")
            with pytest.raises(QMPError, match="closed"):
                client.execute("query-status")
