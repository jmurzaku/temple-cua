import json
from pathlib import Path

from PIL import Image
import pytest

from temple_cua import runner
from temple_cua.protocol import Action, Decision
from temple_cua.tasks import Task, load_tasks
from temple_cua.vm import VMConfig


class FakeVM:
    instances = []

    def __init__(self, config, work_dir):
        self.config = config
        self.closed = False
        self.actions = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def execute(self, action, *, deadline=None):
        self.actions.append(action)
        if action.kind == "key" and action.keys == ["bad-key"]:
            raise RuntimeError("unsupported guest key")
        if action.kind == "type" and action.text == "timeout":
            raise TimeoutError("Task action deadline reached")

    def screenshot(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 480), "white").save(path)
        return path


class FakeProvider:
    def __init__(self, action):
        self.action = action
        self.timeout = 90

    def decide(self, **kwargs):
        return Decision([self.action], usage={"input_tokens": 3, "output_tokens": 4})


@pytest.fixture
def fake_vm(monkeypatch):
    FakeVM.instances = []
    monkeypatch.setattr(runner, "TempleVM", FakeVM)
    return FakeVM


def task(id="sample"):
    return Task(id=id, title="A sample", prompt="Do the task", grader={"type": "manual", "rubric": "Review"}, max_steps=2)


def config():
    return VMConfig(Path("example.iso"), baseline=Path("baseline.qcow2"))


def test_done_does_not_self_grade_and_closes_vm(fake_vm, tmp_path):
    result = runner.run_task(task(), FakeProvider(Action("done", text="I passed")), config(), tmp_path / "sample", settle_seconds=0)
    assert result["status"] == "completed"
    assert result["grade"]["status"] == "needs_review"
    assert result["grade"]["score"] is None
    assert result["usage"]["input_tokens"] == 3
    assert fake_vm.instances[0].closed


def test_budget_records_each_action_and_screenshot(fake_vm, tmp_path):
    folder = tmp_path / "sample"
    result = runner.run_task(task(), FakeProvider(Action("wait", seconds=0)), config(), folder, settle_seconds=0)
    assert result["status"] == "budget_exhausted"
    assert result["steps"] == 2
    records = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines()]
    assert len(records) == 2
    assert records[-1]["executed_actions"] == [{"kind": "wait", "seconds": 0}]
    assert (folder / "0002.png").exists()


def test_partial_action_error_retains_evidence_and_cleanup(fake_vm, tmp_path):
    folder = tmp_path / "sample"
    result = runner.run_task(task(), FakeProvider(Action("key", keys=["bad-key"])), config(), folder, settle_seconds=0)
    assert result["status"] == "error"
    assert result["steps"] == 1
    assert "unsupported guest key" in result["error"]
    record = json.loads((folder / "trajectory.jsonl").read_text())
    assert record["partial_action"]["keys"] == ["bad-key"]
    assert (folder / "final.png").exists()
    assert fake_vm.instances[0].closed


def test_action_deadline_returns_timeout_with_last_frame(fake_vm, tmp_path):
    folder = tmp_path / "sample"
    result = runner.run_task(task(), FakeProvider(Action("type", text="timeout")), config(), folder, settle_seconds=0)
    assert result["status"] == "timeout"
    assert (folder / "final.png").exists()
    assert fake_vm.instances[0].closed


def test_suite_restores_fresh_vm_for_each_task(fake_vm, tmp_path):
    output = tmp_path / "run"
    envelope = runner.run_suite([task("one"), task("two")], lambda: FakeProvider(Action("done", text="done")),
                               config(), output, provider_name="scripted", model="reference")
    assert len(fake_vm.instances) == 2
    assert all(vm.closed and vm.config.baseline == Path("baseline.qcow2") for vm in fake_vm.instances)
    assert len(envelope["results"]) == 2
    assert (output / "report.html").exists()
    assert len(json.loads((output / "results.json").read_text())["results"]) == 2


def test_sample_tasks_load_and_unknown_selection_fails():
    tasks_dir = Path(__file__).resolve().parents[1] / "tasks"
    assert len(load_tasks(tasks_dir)) == 10
    with pytest.raises(ValueError, match="Unknown task"):
        load_tasks(tasks_dir, ["missing"])
