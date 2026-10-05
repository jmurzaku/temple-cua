import importlib.util
import json
from pathlib import Path
import time

from PIL import Image
import pytest

from temple_cua import cua_runner, uart_evaluation, uart_hardware, uart_judge
from temple_cua.cua_runner import CuaOptions
from temple_cua.protocol import Action
from temple_cua.tasks import Task
from temple_cua.vm import VMConfig, VMError


def uart_task():
    return Task(id="uart_irq_service", title="UART IRQ service", prompt="Implement COM1 service",
                grader={"type": "uart"}, setup=[Action("type", text="SETUP\n")],
                max_steps=4, timeout_seconds=30)


def fresh_config():
    return VMConfig(Path("image.iso"), boot_wait=0, extra_args=("-rtc", "base=utc"))


@pytest.fixture
def harness(monkeypatch):
    if importlib.util.find_spec("cua_agent") is None:
        pytest.skip("Install cua-agent to exercise the upstream ComputerAgent")
    cua_runner.load_cua()
    state = {"events": [], "vms": [], "judges": [], "evaluation": False, "oracle_calls": 0}

    class VM:
        def __init__(self, config, folder):
            self.config, self.folder, self.closed = config, folder, False
            self.actions, self.policy_actions = [], []
            self.final_captures = 0
            state["vms"].append(self)

        def __enter__(self):
            state["events"].append("vm-start")
            return self

        def __exit__(self, *_):
            state["events"].append("vm-close")
            self.closed = True

        def execute(self, action, *, deadline=None):
            self.actions.append(action)
            if not state["evaluation"]:
                self.policy_actions.append(action)
            if action.kind == "type" and action.text == "timeout":
                raise TimeoutError("Synthetic policy deadline")

        def screenshot(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name == "final.png":
                self.final_captures += 1
                if state.get("fail_policy_cleanup") and self.final_captures == 2:
                    raise VMError("Synthetic final policy capture failure")
            Image.new("RGB", (640, 480), "green" if state["evaluation"] else "white").save(path)
            return path

        def _events(self, events, deadline=None):
            state["events"].append(("input-events", events))

    class Judge:
        def __init__(self, path):
            self.path, self.connected, self.closed = path, False, False
            state["judges"].append(self)

        def connect(self, timeout=5):
            assert self.path.parent.is_dir()
            assert len(str(self.path).encode()) < 108
            self.connected = True
            state["events"].append("judge-connect")
            return self

        def close(self):
            self.closed = True
            state["events"].append("judge-close")

    class Probe:
        def __init__(self, vm, judge):
            assert judge.connected and not vm.closed
            self.vm = vm

        def capture_state(self):
            assert [action.text for action in self.vm.actions] == ["INIT\n", "SETUP\n"]
            state["events"].append("initial-hardware")
            return {"vector": "original", "pic_irq4_masked": True}

    def initialize_shell(vm):
        state["events"].append("initialize-shell")
        vm.execute(Action("type", text="INIT\n"))

    def evaluate(vm, judge, initial_hardware, folder):
        state["oracle_calls"] += 1
        state["events"].append("evaluate")
        assert judge.connected and not judge.closed and not vm.closed
        assert initial_hardware == {"vector": "original", "pic_irq4_masked": True}
        assert (folder / "trajectory.jsonl").is_file()
        trace = (folder / "trajectory.jsonl").read_bytes()
        final = (folder / "final.png").read_bytes()
        state["evaluation"] = True
        time.sleep(0.02)
        if state.get("evaluation_failure"):
            raise RuntimeError("Synthetic evaluator infrastructure fault")
        vm.execute(Action("type", text="HOST_BENCHSTOP\n"))
        vm.screenshot(folder / "evaluation-stop-1.png")
        grade = state.get("oracle_grade", {"type": "uart", "status": "failed", "score": 0.7,
                                            "reason": "7/10 host checks passed", "evidence": ["evaluation.json"]})
        (folder / "evaluation.json").write_text(json.dumps({"phase": "host_evaluation_after_policy", "grade": grade}))
        assert (folder / "trajectory.jsonl").read_bytes() == trace
        assert (folder / "final.png").read_bytes() == final
        return grade

    monkeypatch.setattr(cua_runner, "TempleVM", VM)
    monkeypatch.setattr(cua_runner, "initialize_shell", initialize_shell)
    monkeypatch.setattr(uart_judge, "UARTJudge", Judge)
    monkeypatch.setattr(uart_hardware, "UARTHardwareProbe", Probe)
    monkeypatch.setattr(uart_evaluation, "evaluate_uart", evaluate)
    monkeypatch.setattr(cua_runner, "grade", lambda *_: pytest.fail("UART must use its host oracle, never screenshot grading"))
    return state


def fixture(tmp_path, first=None):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([
        {"actions": first or [{"kind": "type", "text": "POLICY\n"}]},
        {"actions": [{"kind": "done", "text": "Service ready"}]},
    ]))
    return CuaOptions("openai/fixture", fixture=path)


@pytest.mark.parametrize("entrypoint", ["task", "suite"])
def test_uart_baseline_rejected_before_artifacts_or_dependency_load(tmp_path, monkeypatch, entrypoint):
    monkeypatch.setattr(cua_runner, "load_cua", lambda: pytest.fail("Reject baseline before loading model dependency"))
    config = VMConfig(Path("image.iso"), baseline=Path("baseline.qcow2"))
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="fresh VM.*omit --baseline"):
        if entrypoint == "task":
            cua_runner.run_cua_task(uart_task(), config, output, CuaOptions("openai/fixture"))
        else:
            cua_runner.run_cua_suite([uart_task()], config, output, CuaOptions("openai/fixture"))
    assert not output.exists()


def test_uart_fresh_hardware_oracle_and_policy_evidence_are_separate(harness, tmp_path):
    config = fresh_config()
    folder = tmp_path / "episode"
    result = cua_runner.run_cua_task(uart_task(), config, folder, fixture(tmp_path))
    assert result["status"] == result["policy_status"] == "completed"
    assert result["grade"]["type"] == "uart" and result["grade"]["score"] == 0.7
    assert result["steps"] == 2 and result["input_actions"] == 1
    assert result["policy_seconds"] > 0 and result["evaluation_seconds"] >= 0.01
    assert result["elapsed_seconds"] >= result["policy_seconds"] + result["evaluation_seconds"]
    assert result["hardware_profile"] == cua_runner.UART_HARDWARE_PROFILE
    assert harness["events"].index("judge-connect") < harness["events"].index("initialize-shell")
    assert harness["events"].index("initial-hardware") < harness["events"].index("evaluate")
    assert harness["events"][-2:] == ["judge-close", "vm-close"]
    vm, judge = harness["vms"][0], harness["judges"][0]
    assert vm.closed and judge.closed and not judge.path.parent.exists()
    assert config.extra_args == ("-rtc", "base=utc") and vm.config.baseline is None
    assert vm.config.extra_args[:2] == config.extra_args
    assert "-chardev" in vm.config.extra_args
    assert "isa-serial,chardev=bench_uart,index=0,iobase=0x3f8,irq=4" in vm.config.extra_args
    trace = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines()]
    assert [record["step"] for record in trace] == [1, 2]
    assert trace[0]["executed_actions"] == [{"kind": "type", "text": "POLICY\n"}]
    assert all("HOST_BENCHSTOP" not in json.dumps(record) for record in trace)
    assert json.loads((folder / "initial-hardware.json").read_text())["vector"] == "original"
    assert (folder / "evaluation-stop-1.png").exists()
    assert json.loads((folder / "result.json").read_text())["grade"] == result["grade"]


@pytest.mark.parametrize("ending", ["budget", "timeout"])
def test_host_oracle_runs_after_policy_budget_or_deadline_before_vm_closes(harness, tmp_path, ending):
    options = fixture(tmp_path, [{"kind": "type", "text": "timeout"}] if ending == "timeout" else None)
    result = cua_runner.run_cua_task(uart_task(), fresh_config(), tmp_path / "episode", options,
                                     max_steps=1 if ending == "budget" else None)
    assert result["status"] == result["policy_status"] == ("budget_exhausted" if ending == "budget" else "timeout")
    assert result["steps"] == 1 and harness["oracle_calls"] == 1
    assert result["grade"]["score"] == 0.7 and result["evaluation_seconds"] >= 0.01
    assert harness["vms"][0].closed and harness["judges"][0].closed


@pytest.mark.parametrize("fault", ["evaluator", "policy-cleanup"])
def test_policy_provider_error_survives_host_evaluator_or_cleanup_fault(harness, tmp_path, monkeypatch, fault):
    import litellm

    class ProviderFailure(RuntimeError):
        status_code = 503

    async def failed_inference(**kwargs):
        raise ProviderFailure("PRIVATE_CREDENTIAL_OR_HEADER")

    monkeypatch.setattr(litellm, "aresponses", failed_inference)
    harness["evaluation_failure" if fault == "evaluator" else "fail_policy_cleanup"] = True
    folder = tmp_path / "episode"
    result = cua_runner.run_cua_task(uart_task(), fresh_config(), folder, CuaOptions("openai/fixture-failure"))
    assert result["status"] == result["policy_status"] == "error"
    assert result["steps"] == 1 and harness["oracle_calls"] == 1
    assert result["error"] == result["policy_error"] == "ProviderFailure: HTTP 503"
    if fault == "evaluator":
        assert result["grade"]["status"] == "error" and result["grade"]["score"] is None
        assert result["evaluation_error"].startswith("RuntimeError:")
    else:
        assert result["cleanup_error"].startswith("VMError:")
        assert result["grade"]["score"] == 0.7
    assert "PRIVATE_CREDENTIAL_OR_HEADER" not in (folder / "result.json").read_text()
    assert harness["vms"][0].closed and harness["judges"][0].closed


@pytest.mark.parametrize("grade", [{"type": "ocr", "status": "passed", "score": 1},
                                   {"type": "uart", "status": "failed", "score": float("nan")}])
def test_invalid_oracle_grade_is_an_evaluator_error_not_zero_reward(harness, tmp_path, grade):
    harness["oracle_grade"] = grade
    result = cua_runner.run_cua_task(uart_task(), fresh_config(), tmp_path / "episode", fixture(tmp_path))
    assert result["policy_status"] == "completed" and result["status"] == "error"
    assert result["grade"]["type"] == "uart" and result["grade"]["status"] == "error"
    assert result["grade"]["score"] is None and result["evaluation_error"].startswith("ValueError:")


def test_uart_suite_records_fresh_hardware_and_distinct_evaluation_provenance(harness, tmp_path):
    result = cua_runner.run_cua_suite([uart_task()], fresh_config(), tmp_path / "suite", fixture(tmp_path))
    assert result["provenance"]["uart_hardware_profile"] == cua_runner.UART_HARDWARE_PROFILE
    assert "after policy" in result["provenance"]["uart_evaluation"]
    assert result["config"]["baseline"] is None
    assert result["provenance"]["execution_mode"] == "deterministic_fixture"
    assert result["results"][0]["grade"]["type"] == "uart"


def test_oracle_error_grade_retains_diagnostics_without_numeric_reward(harness, tmp_path):
    oracle = {"type": "uart", "status": "error", "score": None, "reason": "Hardware oracle unavailable",
              "evidence": ["evaluation.json"], "cases": [{"name": "uart", "status": "error"}]}
    harness["oracle_grade"] = oracle
    result = cua_runner.run_cua_task(uart_task(), fresh_config(), tmp_path / "episode", fixture(tmp_path))
    assert result["status"] == "error" and result["policy_status"] == "completed"
    assert result["grade"] == oracle
    assert result["evaluation_error"] == oracle["reason"]
