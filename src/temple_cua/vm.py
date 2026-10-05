"""Isolated TempleOS QEMU sessions controlled through screenshots and QMP."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time
from typing import TYPE_CHECKING

from .qmp import QMPClient, QMPError

if TYPE_CHECKING:
    from .protocol import Action


class VMError(RuntimeError):
    """The emulator could not start or carry out an operation."""


class VMInputError(VMError, ValueError):
    """An input cannot be represented by the guest's keyboard or mouse."""


@dataclass(frozen=True)
class VMConfig:
    iso: Path
    disk: Path | None = None
    qemu_binary: str = "qemu-system-x86_64"
    memory_mb: int = 512
    boot_wait: float = 20.0
    baseline: Path | None = None
    extra_args: tuple[str, ...] = ()


SNAPSHOT_NAME = "temple-cua-baseline"


def baseline_metadata_path(path: Path) -> Path:
    return Path(str(path) + ".json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

_KEY_ALIASES = {
    "enter": "ret", "return": "ret", "escape": "esc", "space": "spc",
    "control": "ctrl", "left_ctrl": "ctrl", "right_ctrl": "ctrl_r",
    "left_shift": "shift", "right_shift": "shift_r", "left_alt": "alt",
    "right_alt": "alt_r", "pageup": "pgup", "page_up": "pgup",
    "pagedown": "pgdn", "page_down": "pgdn", "del": "delete",
    "ins": "insert", "capslock": "caps_lock", "numlock": "num_lock",
    "scrolllock": "scroll_lock", "super": "meta_l", "win": "meta_l",
    "printscreen": "print", "grave": "grave_accent",
    "windows": "meta_l", "backspace": "backspace",
    "-": "minus", "=": "equal", "[": "bracket_left", "]": "bracket_right",
    "\\": "backslash", ";": "semicolon", "'": "apostrophe", "`": "grave_accent",
    ",": "comma", ".": "dot", "/": "slash",
}
_QCODE_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789") | {
    "ret", "esc", "spc", "tab", "backspace", "shift", "shift_r", "ctrl",
    "ctrl_r", "alt", "alt_r", "caps_lock", "num_lock", "scroll_lock",
    "minus", "equal", "bracket_left", "bracket_right", "backslash",
    "semicolon", "apostrophe", "grave_accent", "comma", "dot", "slash",
    "insert", "delete", "home", "end", "pgup", "pgdn", "up", "down",
    "left", "right", "meta_l", "meta_r", "menu", "print", "pause", "sysrq",
    "kp_divide", "kp_multiply", "kp_subtract", "kp_add", "kp_enter",
    "kp_decimal",
} | {f"f{i}" for i in range(1, 13)} | {f"kp_{i}" for i in range(10)}
_KEY_ALIASES.update({f"kp{i}": f"kp_{i}" for i in range(10)})
_SHIFTED = dict(zip("!@#$%^&*()_+{}|:\"~<>?", "1234567890-=[]\\;'`,./"))
_MODIFIER_KEYS = {"shift", "shift_r", "ctrl", "ctrl_r", "alt", "alt_r", "meta_l", "meta_r"}


def _qcode(key: str) -> str:
    normalized = key.lower().strip()
    normalized = _KEY_ALIASES.get(normalized, normalized)
    if normalized not in _QCODE_KEYS:
        raise VMInputError(f"Unsupported keyboard key: {key!r}")
    return normalized


def text_to_chords(text: str) -> list[list[str]]:
    """Convert US ASCII to QEMU key chords before sending any partial text."""
    chords: list[list[str]] = []
    for character in text.replace("\r\n", "\n").replace("\r", "\n"):
        if character == "\n":
            chords.append(["ret"])
        elif character == "\t":
            chords.append(["tab"])
        elif character == " ":
            chords.append(["spc"])
        elif "A" <= character <= "Z":
            chords.append(["shift", character.lower()])
        elif character in _SHIFTED:
            chords.append(["shift", _qcode(_SHIFTED[character])])
        elif character.isascii() and character.isprintable():
            chords.append([_qcode(character)])
        else:
            raise VMInputError(
                f"TempleOS typing supports US ASCII only; unsupported character {character!r}"
            )
    return chords


class TempleVM:
    """A disposable disk copy, a local QMP monitor, and a networkless guest.

    Baselines are full qcow2 copies with an internal VM snapshot. Restoring
    requires the same QEMU version, ISO, memory size, and device arguments.
    Neither the source ISO, source disk, nor baseline is opened writable.
    """

    def __init__(self, config: VMConfig, work_dir: Path):
        self.config = config
        self.work_dir = Path(work_dir).resolve()
        self.process: subprocess.Popen[bytes] | None = None
        self.qmp: QMPClient | None = None
        self.disk_path = self.work_dir / "session.qcow2"
        self.width, self.height = 640, 480
        self._monitor_dir: Path | None = None
        self._stderr_file = None
        self._identity: dict | None = None

    def _environment_identity(self, qemu_binary: str) -> dict:
        try:
            result = subprocess.run(
                [qemu_binary, "--version"], capture_output=True, timeout=10, check=True
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VMError(f"Cannot identify the QEMU version: {exc}") from exc
        version_lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        if not version_lines:
            raise VMError("QEMU did not report its version")
        return {
            "iso_sha256": _sha256(Path(self.config.iso).resolve()),
            "qemu_version": version_lines[0],
            "memory_mb": self.config.memory_mb,
            "extra_args": list(self.config.extra_args),
            "device_layout_version": 1,
        }

    def _validate_baseline(self, source: Path) -> None:
        metadata_path = baseline_metadata_path(source)
        try:
            metadata = json.loads(metadata_path.read_text())
        except (OSError, ValueError) as exc:
            raise VMError(f"Baseline metadata is missing or invalid: {metadata_path}") from exc
        if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
            raise VMError("Unsupported baseline metadata format")
        if metadata.get("identity") != self._identity:
            raise VMError("Baseline requires the original ISO, QEMU version, memory, and extra arguments")
        if metadata.get("baseline_sha256") != _sha256(source):
            raise VMError("Baseline image hash differs from its metadata; recreate the baseline")

    def _qemu_img(self, qemu_binary: str) -> str:
        sibling = Path(qemu_binary).with_name("qemu-img")
        if sibling.is_file():
            return str(sibling)
        found = shutil.which("qemu-img")
        if found is None:
            raise VMError("qemu-img is missing; install the QEMU utilities package")
        return found

    def _prepare_disk(self, qemu_binary: str) -> None:
        image_tool = self._qemu_img(qemu_binary)
        if self.disk_path.is_symlink():
            raise VMError("The disposable session disk cannot be a symbolic link")
        for supplied in (self.config.iso, self.config.disk, self.config.baseline):
            if supplied is None:
                continue
            supplied_path = Path(supplied).resolve()
            if supplied_path == self.disk_path or (
                self.disk_path.exists() and supplied_path.exists()
                and self.disk_path.samefile(supplied_path)
            ):
                raise VMError("The disposable session disk must differ from every input image")
        source = self.config.baseline or self.config.disk
        if source is not None:
            source = Path(source).resolve()
            if not source.is_file():
                raise VMError(f"Disk or baseline does not exist: {source}")
            if source == self.disk_path:
                raise VMError("The source disk must differ from the disposable session disk")
            if self.config.baseline is not None:
                # Conversion drops internal snapshots, so baseline copies must
                # preserve the qcow2 file byte for byte.
                self._validate_baseline(source)
                shutil.copyfile(source, self.disk_path)
                return
            command = [image_tool, "convert", "-O", "qcow2", str(source), str(self.disk_path)]
        else:
            command = [image_tool, "create", "-f", "qcow2", str(self.disk_path), "256M"]
        try:
            result = subprocess.run(command, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VMError(f"Unable to create the isolated VM disk: {exc}") from exc
        if result.returncode:
            raise VMError(
                "qemu-img failed: " + result.stderr.decode("utf-8", errors="replace").strip()
            )

    def start(self) -> TempleVM:
        if self.process is not None:
            raise VMError("This VM session has already been started")
        if self.config.memory_mb < 128 or self.config.boot_wait < 0:
            raise VMError("Memory must be at least 128 MB and boot_wait must be nonnegative")
        iso = Path(self.config.iso).resolve()
        if not iso.is_file():
            raise VMError(f"TempleOS ISO does not exist: {iso}")
        executable = shutil.which(self.config.qemu_binary)
        if executable is None:
            raise VMError(f"QEMU executable not found: {self.config.qemu_binary}")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._identity = self._environment_identity(executable)
        self._prepare_disk(executable)
        # UNIX socket names have a short platform limit; artifact paths need
        # not share that limit.
        self._monitor_dir = Path(tempfile.mkdtemp(prefix="temple-qmp-"))
        monitor_path = self._monitor_dir / "qmp.sock"
        command = [
            executable, "-name", "temple-cua", "-machine", "pc,accel=tcg",
            "-cpu", "qemu64", "-smp", "1", "-m", str(self.config.memory_mb),
            "-vga", "std", "-display", "none", "-nic", "none",
            "-no-reboot", "-monitor", "none", "-serial", "none",
            "-qmp", f"unix:{monitor_path},server=on,wait=off",
            "-drive", f"file={str(self.disk_path).replace(',', ',,')},format=qcow2,if=ide,index=0",
            "-drive", f"file={str(iso).replace(',', ',,')},format=raw,media=cdrom,if=ide,index=2,readonly=on",
            "-boot", "order=d",
        ]
        if self.config.baseline is not None:
            command.extend(["-loadvm", SNAPSHOT_NAME])
        command.extend(self.config.extra_args)
        self._stderr_file = (self.work_dir / "qemu.log").open("wb")
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=self._stderr_file,
                stderr=self._stderr_file, start_new_session=True,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise VMError(self._startup_error())
                if monitor_path.exists():
                    candidate = QMPClient(monitor_path)
                    try:
                        candidate.connect()
                        self.qmp = candidate
                        break
                    except (ConnectionRefusedError, FileNotFoundError):
                        candidate.close()
                time.sleep(0.05)
            if self.qmp is None:
                raise VMError("Timed out waiting for the QEMU monitor")
            if self.config.baseline is not None and not self.qmp.execute("query-status").get("running"):
                self.qmp.execute("cont")
            if self.config.boot_wait:
                time.sleep(self.config.boot_wait)
            self._check_running()
            return self
        except BaseException:
            self.close()
            raise

    def _startup_error(self) -> str:
        log_path = self.work_dir / "qemu.log"
        detail = log_path.read_text(errors="replace")[-4000:] if log_path.exists() else ""
        return f"QEMU exited unexpectedly. {detail.strip()}"

    def _check_running(self) -> QMPClient:
        if self.process is None or self.process.poll() is not None:
            raise VMError(self._startup_error())
        if self.qmp is None:
            raise VMError("QMP monitor is not connected")
        return self.qmp

    def screenshot(self, path: Path) -> Path:
        from PIL import Image

        monitor = self._check_running()
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="screen-", suffix=".ppm", dir=self.work_dir)
        os.close(fd)
        try:
            monitor.execute("screendump", {"filename": temporary})
            with Image.open(temporary) as captured:
                self.width, self.height = captured.size
                captured.save(destination, format="PNG")
        finally:
            Path(temporary).unlink(missing_ok=True)
        return destination

    @staticmethod
    def _remaining(deadline: float | None) -> float:
        if deadline is None:
            return 10.0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Task action deadline reached")
        return remaining

    def _delay(self, seconds: float, deadline: float | None = None) -> None:
        remaining = self._remaining(deadline)
        time.sleep(seconds if deadline is None else min(seconds, remaining))
        self._remaining(deadline)

    def _events(self, events: list[dict], deadline: float | None = None) -> None:
        self._check_running().execute(
            "input-send-event", {"events": events}, timeout=min(10.0, self._remaining(deadline))
        )

    @staticmethod
    def _key_event(key: str, down: bool) -> dict:
        return {"type": "key", "data": {"down": down, "key": {"type": "qcode", "data": key}}}

    def _press(self, keys: list[str], deadline: float | None = None) -> None:
        if not keys:
            raise VMInputError("A key action requires a nonempty chord")
        normalized = [_qcode(key) for key in keys]
        if len(set(normalized)) != len(normalized):
            raise VMInputError("A key chord cannot contain duplicate keys")
        modifiers = [key for key in normalized if key in _MODIFIER_KEYS]
        regular = [key for key in normalized if key not in _MODIFIER_KEYS]
        held_modifiers: list[str] = []
        held_regular: list[str] = []
        try:
            # The guest must process each modifier before receiving the key.
            # Sending Shift+9 in one packet can become "9" on a busy TCG host.
            for modifier in modifiers:
                held_modifiers.append(modifier)
                self._events([self._key_event(modifier, True)], deadline)
                self._delay(0.02, deadline)
            if regular:
                held_regular.extend(regular)
                self._events([self._key_event(key, True) for key in regular], deadline)
                self._delay(0.04, deadline)
        finally:
            try:
                if held_regular:
                    self._events([self._key_event(key, False) for key in reversed(held_regular)])
                    if held_modifiers:
                        self._delay(0.02, deadline)
            finally:
                # Cleanup also runs if the deadline expires during the
                # modifier lead-in or the character release interval.
                if held_modifiers:
                    self._events([self._key_event(key, False) for key in reversed(held_modifiers)])
        self._delay(0.02, deadline)

    def _relative(self, dx: int, dy: int, deadline: float | None = None) -> None:
        self._events([
            {"type": "rel", "data": {"axis": "x", "value": dx}},
            {"type": "rel", "data": {"axis": "y", "value": dy}},
        ], deadline)
        self._delay(0.012, deadline)

    def _move(self, x: int | None, y: int | None, deadline: float | None = None) -> None:
        if x is None or y is None or not (0 <= x < self.width and 0 <= y < self.height):
            raise VMInputError(f"Mouse coordinates must be inside {self.width}x{self.height}")
        # TempleOS uses a relative PS/2 mouse with a stock 0.5 scale, not a
        # USB tablet. The prepared fixture sets ms_grid.x/ms_grid.y to 1 so
        # clipping lands exactly on the edge, rather than an 8px grid offset.
        # Anchor there before translating screenshot coordinates to packets.
        for _ in range(16):
            self._relative(-127, -127, deadline)
        remaining_x, remaining_y = x * 2, y * 2
        while remaining_x or remaining_y:
            dx, dy = min(80, remaining_x), min(80, remaining_y)
            self._relative(dx, dy, deadline)
            remaining_x -= dx
            remaining_y -= dy

    def execute(self, action: Action, *, deadline: float | None = None) -> None:
        self._check_running()
        self._remaining(deadline)
        kind = action.kind
        if kind == "type":
            if action.text is None:
                raise VMInputError("A type action requires text")
            for keys in text_to_chords(action.text):
                self._press(keys, deadline)
        elif kind == "key":
            self._press(action.keys or [], deadline)
        elif kind in {"move", "click"}:
            if kind == "click" and action.button not in {"left", "right", "middle"}:
                raise VMInputError("Mouse button must be left, right, or middle")
            if kind == "click" and not 1 <= action.clicks <= 3:
                raise VMInputError("Click count must be between one and three")
            self._move(action.x, action.y, deadline)
            if kind == "click":
                for _ in range(action.clicks):
                    self._events([{"type": "btn", "data": {"button": action.button, "down": True}}], deadline)
                    try:
                        self._delay(0.04, deadline)
                    finally:
                        self._events([{"type": "btn", "data": {"button": action.button, "down": False}}])
                    self._delay(0.08, deadline)
        elif kind == "scroll":
            if action.direction not in {"up", "down"} or not 1 <= action.amount <= 50:
                raise VMInputError("Scroll needs direction up/down and amount between 1 and 50")
            for _ in range(action.amount):
                button = "wheel-up" if action.direction == "up" else "wheel-down"
                self._events([{"type": "btn", "data": {"button": button, "down": True}}], deadline)
                self._events([{"type": "btn", "data": {"button": button, "down": False}}])
                self._delay(0.02, deadline)
        elif kind == "wait":
            if not 0 <= action.seconds <= 30:
                raise VMInputError("Wait duration must be between 0 and 30 seconds")
            self._delay(action.seconds, deadline)
        elif kind == "done":
            return
        else:
            raise VMInputError(f"Unsupported action kind: {kind!r}")

    def save_baseline(self, path: Path) -> Path:
        """Freeze RAM and disk into a copy of the session's qcow2 image.

        The guest resumes afterward. A baseline must not replace an input
        ISO/disk/baseline, or the active session disk. QEMU internal snapshots
        are tied to the QEMU/device configuration used to create them.
        """
        monitor = self._check_running()
        destination = Path(path).resolve()
        protected = {self.disk_path, Path(self.config.iso).resolve()}
        protected.update(Path(p).resolve() for p in (self.config.disk, self.config.baseline) if p)
        if destination in protected:
            raise VMError("A baseline output must differ from all input and active VM images")
        destination.parent.mkdir(parents=True, exist_ok=True)
        was_running = monitor.execute("query-status").get("running", False)
        monitor.execute("stop")
        temporary: Path | None = None
        metadata_temporary: Path | None = None
        try:
            result = monitor.execute(
                "human-monitor-command", {"command-line": f"savevm {SNAPSHOT_NAME}"}, timeout=120
            )
            if result and any(token in result.lower() for token in ("error", "failed", "cannot", "does not")):
                raise VMError(f"QEMU could not save its baseline: {result.strip()}")
            fd, name = tempfile.mkstemp(prefix="baseline-", suffix=".qcow2", dir=destination.parent)
            os.close(fd)
            temporary = Path(name)
            shutil.copyfile(self.disk_path, temporary)
            metadata = {
                "schema_version": 1, "identity": self._identity,
                "snapshot_name": SNAPSHOT_NAME, "baseline_sha256": _sha256(temporary),
            }
            fd, name = tempfile.mkstemp(prefix="baseline-", suffix=".json", dir=destination.parent)
            os.close(fd)
            metadata_temporary = Path(name)
            metadata_temporary.write_text(json.dumps(metadata, indent=2) + "\n")
            temporary.replace(destination)
            metadata_temporary.replace(baseline_metadata_path(destination))
            return destination
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if metadata_temporary is not None:
                metadata_temporary.unlink(missing_ok=True)
            if was_running:
                monitor.execute("cont")

    def close(self) -> None:
        if self.qmp is not None:
            try:
                self.qmp.execute("quit", timeout=2)
            except (QMPError, OSError):
                pass
            self.qmp.close()
            self.qmp = None
        if self.process is not None:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None
        if self._stderr_file is not None:
            self._stderr_file.close()
            self._stderr_file = None
        if self._monitor_dir is not None:
            shutil.rmtree(self._monitor_dir, ignore_errors=True)
            self._monitor_dir = None

    def __enter__(self) -> TempleVM:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()
