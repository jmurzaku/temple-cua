"""Verify host evaluation reward, cleanup evidence, and false-success guards."""

from copy import deepcopy
import json
from pathlib import Path
import re

from PIL import Image
import pytest

from temple_cua.bitmap_text import FONT_ASCII
from temple_cua import uart_evaluation as evaluation


class FakeJudge:
    def __init__(self, passed=8, *, no_evidence=False, error=False):
        self.seed = 123
        self.passed = passed
        self.no_evidence = no_evidence
        self.error = error
        self.seed_at_run = None

    def run(self):
        self.seed_at_run = self.seed
        if self.error:
            raise ConnectionError("serial peer disconnected")
        cases = []
        for index, name in enumerate(evaluation.SERIAL_CASE_NAMES):
            passed = index < self.passed
            expected = [] if self.no_evidence else [f"5a{index:02x}00000000{index:02x}"]
            cases.append({"name": name, "status": "passed" if passed else "failed",
                          "expected_replies_hex": expected,
                          "received_replies_hex": expected if passed else [], "anomalies": [],
                          "reason": "Exact replies" if passed else "No response", "transcript": []})
        # An aggregate claim is deliberately not sufficient for scoring.
        return {"status": "passed", "score": 1.0, "passed_cases": 8,
                "total_cases": 8, "cases": cases, "seed_hex": format(self.seed, "x")}


def _render_line(image, line, row):
    pixels = image.load()
    for column, character in enumerate(line[:80]):
        if not 32 <= ord(character) <= 126:
            continue
        bits = FONT_ASCII[ord(character) - 32]
        for bit in range(64):
            if bits & 1 << bit:
                pixels[column * 8 + bit % 8, row * 8 + bit // 8] = (0, 0, 170)


class FakeVM:
    def __init__(self, *, output="correct"):
        self.actions = []
        self.lines = []
        self.stop_calls = 0
        self.output = output
        self.model_history = [{"step": 1, "actions": [{"kind": "done"}]}]

    def execute(self, action, *, deadline=None):
        self.actions.append(action.to_dict())
        if action.kind != "type":
            return
        self.lines.append("B:/Home>" + action.text.strip())
        if "BenchStop();" in action.text:
            self.stop_calls += 1
        assert action.text.startswith("{DocClear;")
        assert action.text.endswith("};\n")
        match = re.search(r'"\\n  (UA_[A-Z0-9]+_[0-9a-f]+)=%d\\n",(\d+)\*(\d+)\+(\d+);', action.text)
        assert match, action.text
        label, a, b, c = match.groups()
        value = int(a) * int(b) + int(c)
        if self.output == "echo_only":
            return
        if self.output == "first_stop_error" and self.stop_calls == 1:
            self.lines.append("Exception: stop failed")
            return
        if self.output == "wrong_result":
            value += 1
        line = f"{label}={value}"
        self.lines.append("  " + line)
        if self.output == "duplicate":
            self.lines.append("  " + line)

    def screenshot(self, path):
        image = Image.new("RGB", (640, 480), "white")
        for row, line in enumerate(self.lines[-50:]):
            _render_line(image, line, row + 1)
        image.save(path)
        return path


class FakeProbe:
    def __init__(self, vm, *, irq=True, restored=True):
        self.vm = vm
        self.irq = irq
        self.restored = restored
        self.irq_calls = 0
        self.capture_calls = 0

    def irq4_dependence(self):
        self.irq_calls += 1
        return {"status": "passed" if self.irq else "failed", "reason": "Mask/unmask probe",
                "masked_rx_hex": "", "unmasked_reply_hex": "5a010000000001"}

    def capture_state(self):
        self.capture_calls += 1
        return {"idt_gate_hex": "old" if self.restored else "changed", "pic_irq4": True}


@pytest.fixture
def backend(monkeypatch):
    # Failed observations must remain bounded without spending five seconds
    # per simulated shell error in the unit suite.
    monkeypatch.setattr(evaluation, "CHALLENGE_OBSERVATION_SECONDS", 0.01)
    probes = []

    def install(vm, *, irq=True, restored=True):
        probe = FakeProbe(vm, irq=irq, restored=restored)
        probes.append(probe)
        monkeypatch.setattr(evaluation, "_make_probe", lambda *args: probe)
        monkeypatch.setattr(evaluation, "_compare_restoration",
                            lambda before, after: {"status": "passed" if before == after else "failed",
                                                   "checks": {"same": before == after}})
        return probe
    return install


INITIAL = {"idt_gate_hex": "old", "pic_irq4": True}


def test_saved_multiwindow_ocr_contains_unique_fresh_outputs_and_no_computed_echo():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "uart-multiwindow-ocr.json").read_text())
    assert [case["purpose"] for case in fixture["cases"]] == ["STOP1", "STOP2", "ALIVE"]
    for case in fixture["cases"]:
        expected = case["expected_line"]
        assert not any(line.strip() == expected for line in case["observed_text"].splitlines())
        assert evaluation._output_token_matches(case["observed_text"], expected) == 1
        assert evaluation._output_token_matches(case["command"], expected) == 0


@pytest.mark.parametrize("surrounding", [
    "{token}", "  {token}  ", "OTHER PANE  {token}", "{token}  OTHER PANE",
    "\ufffd damaged pane  {token}\n", "\n\t{token}\t\n",
])
def test_output_token_accepts_complete_whitespace_bounded_output(surrounding):
    token = "UA_ALIVE_0123456789abcdef=42"
    assert evaluation._output_token_matches(surrounding.format(token=token), token) == 1


@pytest.mark.parametrize("surrounding", [
    "X{token}", "_{token}", '"{token}"', "[{token}]", "{token}0", "{token}x",
    "{token}_", "{token};", "{token}.5", "{token}\ufffd", "\ufffd{token}",
    'Print("{token}");',
])
def test_output_token_rejects_attached_prefixes_suffixes_and_literal_echoes(surrounding):
    token = "UA_ALIVE_0123456789abcdef=42"
    assert evaluation._output_token_matches(surrounding.format(token=token), token) == 0


def test_output_token_keeps_exact_nonce_value_and_counts_duplicates_on_one_row():
    token = "UA_ALIVE_0123456789abcdef=42"
    for incorrect in (token.replace("0123", "0124"), token.replace("=42", "=41"),
                      token.replace("=42", "= 42"), token.replace("ALIVE", "AL\ufffdVE"),
                      token.replace("=42", "=%d")):
        assert evaluation._output_token_matches(incorrect, token) == 0
    assert evaluation._output_token_matches(f"{token}  {token}", token) == 2
    assert evaluation._output_token_matches(f"{token}\n{token}", token) == 2


def test_cleanup_accepts_output_beside_another_pane_without_changing_reward_checks(tmp_path, backend):
    class MultiWindowVM(FakeVM):
        def execute(self, action, *, deadline=None):
            super().execute(action, deadline=deadline)
            if action.kind == "type":
                self.lines[-1] = "OTHER PANE CONTENT" + self.lines[-1]

    vm = MultiWindowVM()
    backend(vm)
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["score"] == 1 and grade["passed_cases"] == 10
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert report["cleanup"]["status"] == "passed"
    for command in report["cleanup"]["host_commands"]:
        assert command["output_matcher"] == "unique_whitespace_delimited_nonce_value"
        assert command["observations"][-1]["exact_token_matches"] == 1
        assert not any(line.strip() == command["expected_line"] for line in command["observed_text"].splitlines())


def test_all_ten_checks_pass_and_host_actions_remain_separate(tmp_path, backend):
    vm, judge = FakeVM(), FakeJudge()
    original_history = deepcopy(vm.model_history)
    probe = backend(vm)
    grade = evaluation.evaluate_uart(vm, judge, INITIAL, tmp_path)
    assert grade["type"] == "uart"
    assert grade["status"] == "passed"
    assert grade["score"] == 1
    assert grade["passed_cases"] == grade["total_cases"] == 10
    assert all(case["score"] == 0.1 for case in grade["cases"])
    assert judge.seed_at_run != 123  # regenerated after the policy phase
    assert probe.irq_calls == 1 and probe.capture_calls == 2
    assert vm.stop_calls == 2
    assert vm.model_history == original_history
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert report["phase"] == "host_evaluation_after_policy"
    assert report["model_actions_added"] == 0
    assert report["command_deadline_seconds"] == evaluation.COMMAND_DEADLINE_SECONDS
    assert "budget_seconds" not in report
    assert report["grade"] == grade
    assert all(item["status"] == "passed" for item in report["cleanup"]["host_commands"])
    assert set(grade["evidence"]) == {"evaluation.json", "evaluation-stop-1.png",
                                     "evaluation-stop-2.png", "evaluation-liveness.png"}


def test_partial_protocol_reward_is_interpretable_not_aggregate_claim(tmp_path, backend):
    vm = FakeVM()
    backend(vm)
    grade = evaluation.evaluate_uart(vm, FakeJudge(passed=3), INITIAL, tmp_path)
    assert grade["status"] == "failed"
    assert grade["score"] == 0.5  # three wire cases plus IRQ and cleanup
    assert grade["passed_cases"] == 5
    assert [case["status"] for case in grade["cases"][:8]] == ["passed"] * 3 + ["failed"] * 5


@pytest.mark.parametrize("no_evidence", [False, True])
def test_idle_or_empty_claimed_success_gets_no_extra_credit(tmp_path, backend, no_evidence):
    vm = FakeVM()
    probe = backend(vm)
    judge = FakeJudge(passed=8 if no_evidence else 0, no_evidence=no_evidence)
    grade = evaluation.evaluate_uart(vm, judge, INITIAL, tmp_path)
    assert grade["score"] == 0
    assert probe.irq_calls == 0
    assert vm.stop_calls == 2  # cleanup still attempted despite gated credit
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert report["irq4_dependence"]["status"] == "skipped"
    assert report["cleanup"]["status"] == "passed"
    assert "requires" in grade["cases"][-1]["reason"]


def test_failed_mask_probe_costs_only_its_one_check(tmp_path, backend):
    vm = FakeVM()
    backend(vm, irq=False)
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["score"] == 0.9
    assert grade["cases"][8]["status"] == "failed"
    assert grade["cases"][9]["status"] == "passed"


@pytest.mark.parametrize("output", ["echo_only", "wrong_result", "duplicate", "first_stop_error"])
def test_cleanup_needs_fresh_unique_correct_output_not_echoes(tmp_path, backend, output):
    vm = FakeVM(output=output)
    backend(vm)
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["score"] == 0.9
    assert grade["cases"][9]["status"] == "failed"
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert any(command["status"] == "failed" for command in report["cleanup"]["host_commands"])


def test_cleanup_needs_hardware_restored_after_both_stops(tmp_path, backend):
    vm = FakeVM()
    backend(vm, restored=False)
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["score"] == 0.9
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert all(check["status"] == "failed" for check in report["cleanup"]["restoration_checks"])


def test_judge_exception_retains_unverified_evaluation_and_attempts_cleanup(tmp_path, backend):
    vm = FakeVM()
    backend(vm)
    grade = evaluation.evaluate_uart(vm, FakeJudge(error=True), INITIAL, tmp_path)
    assert grade["status"] == "error"
    assert grade["score"] is None
    assert vm.stop_calls == 2
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert "serial peer disconnected" in report["errors"][0]


def test_hardware_inspection_error_preserves_protocol_reward_and_evidence(tmp_path, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("hardware inspection unavailable")
    monkeypatch.setattr(evaluation, "_make_probe", unavailable)
    vm = FakeVM()
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["status"] == "error"
    assert grade["score"] is None
    assert grade["passed_cases"] == 8
    assert vm.stop_calls == 2
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert "hardware inspection unavailable" in report["errors"][0]


def test_reported_irq_instrumentation_error_is_not_a_model_failure(tmp_path, backend):
    vm = FakeVM()
    probe = backend(vm)
    probe.irq4_dependence = lambda: {"status": "failed", "infrastructure_error": True,
                                     "reason": "QMP did not provide a port value"}
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["status"] == "error"
    assert grade["score"] is None
    assert grade["cases"][8]["status"] == "error"


def test_hardware_capture_exception_retains_evidence_without_numeric_reward(tmp_path, backend):
    vm = FakeVM()
    probe = backend(vm)
    def unavailable():
        raise RuntimeError("register capture unavailable")
    probe.capture_state = unavailable
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["status"] == "error"
    assert grade["score"] is None
    assert vm.stop_calls == 2
    assert (tmp_path / "evaluation-liveness.png").exists()


def test_malformed_oracle_return_is_not_zero_reward(tmp_path, backend):
    vm = FakeVM()
    backend(vm)
    judge = FakeJudge()
    judge.run = lambda: {"score": 1, "cases": ["not a case"]}
    grade = evaluation.evaluate_uart(vm, judge, INITIAL, tmp_path)
    assert grade["status"] == "error"
    assert grade["score"] is None


def test_border_unknown_cells_do_not_hide_real_output(tmp_path, backend, monkeypatch):
    vm = FakeVM()
    backend(vm)
    original_reader = evaluation.read_bitmap_text
    def bordered(path):
        return "\n".join("\ufffd " + line + " \ufffd" for line in original_reader(path).splitlines())
    monkeypatch.setattr(evaluation, "read_bitmap_text", bordered)
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["status"] == "passed"
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert "\ufffd" in report["cleanup"]["host_commands"][0]["observed_text"]


def test_interior_unknown_cells_are_never_repaired_into_challenge_output(tmp_path, backend, monkeypatch):
    vm = FakeVM()
    backend(vm)
    original_reader = evaluation.read_bitmap_text
    def damaged(path):
        return original_reader(path).replace("UA_STOP1_", "UA_ST\ufffdP1_")
    monkeypatch.setattr(evaluation, "read_bitmap_text", damaged)
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["score"] == 0.9
    assert grade["cases"][-1]["status"] == "failed"


def test_serial_case_transport_error_is_not_a_numeric_model_zero(tmp_path, backend):
    vm = FakeVM()
    backend(vm)
    judge = FakeJudge(passed=0)
    original_run = judge.run
    def broken_transport():
        report = original_run()
        report["cases"][0].update(infrastructure_error=True, reason="BrokenPipeError: serial transport closed")
        return report
    judge.run = broken_transport
    grade = evaluation.evaluate_uart(vm, judge, INITIAL, tmp_path)
    assert grade["status"] == "error"
    assert grade["score"] is None
    assert grade["cases"][0]["status"] == "error"
    assert vm.stop_calls == 2


def test_challenge_waits_for_completed_output_before_hardware_snapshot(tmp_path, backend, monkeypatch):
    class DelayedVM(FakeVM):
        def execute(self, action, *, deadline=None):
            super().execute(action, deadline=deadline)
            if action.kind == "type":
                self.pending_output = self.lines.pop()
                self.pending_frames = 2

        def screenshot(self, path):
            if self.pending_frames:
                self.pending_frames -= 1
            elif self.pending_output:
                self.lines.append(self.pending_output)
                self.pending_output = None
            return super().screenshot(path)

    vm = DelayedVM()
    probe = backend(vm)
    monkeypatch.setattr(evaluation, "CHALLENGE_OBSERVATION_SECONDS", 1)
    original_capture = probe.capture_state
    def capture_after_output():
        assert vm.pending_output is None
        return original_capture()
    probe.capture_state = capture_after_output
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["status"] == "passed"
    report = json.loads((tmp_path / "evaluation.json").read_text())
    assert all(len(command["observations"]) == 3 for command in report["cleanup"]["host_commands"])


def test_native_client_crop_removes_recognized_ascii_border_only(tmp_path, backend):
    class BorderedVM(FakeVM):
        def screenshot(self, path):
            super().screenshot(path)
            with Image.open(path) as original:
                image = original.copy()
            for row in range(1, 60):
                _render_line(image, "r", row)
            image.save(path)
            return path
    vm = BorderedVM()
    backend(vm)
    grade = evaluation.evaluate_uart(vm, FakeJudge(), INITIAL, tmp_path)
    assert grade["status"] == "passed"
    report = json.loads((tmp_path / "evaluation.json").read_text())
    for command in report["cleanup"]["host_commands"]:
        assert command["extraction_crop_box"] == [8, 16, 632, 472]
        assert any(line.startswith("r UA_") for line in command["observed_full_frame_text"].splitlines())
        assert not any(line.startswith("r UA_") for line in command["observed_text"].splitlines())
