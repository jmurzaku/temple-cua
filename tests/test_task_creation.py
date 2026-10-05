from pathlib import Path

import pytest
import yaml

from temple_cua.cli import main
from temple_cua.grading import grade
from temple_cua.tasks import create_task, load_task, load_tasks


def test_cli_creates_loadable_manual_task_and_parent_directory(tmp_path, capsys):
    folder = tmp_path / "new" / "tasks"
    assert main(["new-task", "my-task", "--tasks", str(folder)]) == 0
    path = folder / "my-task.yaml"
    created = load_task(path)
    assert created.id == "my-task"
    assert created.title == "My Task"
    assert "TempleOS" in created.prompt
    assert created.max_steps == 40 and created.timeout_seconds == 180
    assert "trajectory" in created.grader["rubric"]
    reviewed = grade(created.grader, Path("unused.png"), tmp_path)
    assert reviewed["status"] == "needs_review" and reviewed["score"] is None
    assert load_tasks(folder, ["my-task"])[0].source == path
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == str(path)
    assert len(lines) == 2 and "--task my-task" in lines[1]
    assert main(["list-tasks", "--tasks", str(folder)]) == 0
    assert "my-task" in capsys.readouterr().out


def test_cli_default_destination_is_tasks_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["new-task", "my-task"]) == 0
    assert load_tasks(tmp_path / "tasks", ["my-task"])[0].id == "my-task"


def test_cli_expected_lines_grade_real_frame_without_changing_prompt(tmp_path):
    folder = tmp_path / "tasks"
    prompt = 'Calculate 37*19-24*13 in HolyC and print the result with label BENCH_ARITH=.'
    assert main([
        "new-task", "arithmetic-copy", "--tasks", str(folder),
        "--title", 'Calculation: "quoted"', "--prompt", prompt,
        "--expect", "BENCH_ARITH=391",
    ]) == 0
    created = load_tasks(folder, ["arithmetic-copy"])[0]
    assert created.prompt == prompt
    assert "391" not in created.prompt
    assert created.title == 'Calculation: "quoted"'
    assert created.grader == {
        "type": "ocr", "crop": [8, 16, 624, 456], "case_sensitive": True,
        "match_mode": "line", "all": ["BENCH_ARITH=391"],
    }
    frame = Path(__file__).parent / "fixtures" / "temple-arithmetic.png"
    result = grade(created.grader, frame, tmp_path / "passed")
    assert result["status"] == "passed" and result["score"] == 1
    wrong_case = {**created.grader, "all": ["bench_arith=391"]}
    result = grade(wrong_case, frame, tmp_path / "failed")
    assert result["status"] == "failed" and result["score"] == 0


def test_repeated_expect_values_and_yaml_characters_are_preserved(tmp_path):
    prompt = 'Print "ANSWER=42".\nThen leave READY: yes visible.'
    assert main([
        "new-task", "special", "--tasks", str(tmp_path), "--prompt", prompt,
        "--expect", "ANSWER=42", "--expect", "READY: yes",
    ]) == 0
    created = load_task(tmp_path / "special.yaml")
    assert created.prompt == prompt
    assert created.grader["all"] == ["ANSWER=42", "READY: yes"]


def test_existing_file_is_not_changed(tmp_path, capsys):
    path = create_task("sample", tasks_dir=tmp_path)
    original = path.read_bytes()
    assert main(["new-task", "sample", "--tasks", str(tmp_path), "--prompt", "Changed"]) == 1
    assert path.read_bytes() == original
    assert "already exists" in capsys.readouterr().err


def test_existing_id_under_numbered_filename_is_not_duplicated(tmp_path):
    original = create_task("sample", tasks_dir=tmp_path)
    numbered = tmp_path / "01_sample.yaml"
    original.rename(numbered)
    before = numbered.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        create_task("sample", tasks_dir=tmp_path)
    assert numbered.read_bytes() == before
    assert not original.exists()
    assert len(load_tasks(tmp_path)) == 1


@pytest.mark.parametrize("task_id", ["../escape", "/absolute", "a/b", "..", "UPPER", "has space", "", "x" * 81])
def test_invalid_id_cannot_create_directories_or_escape(tmp_path, task_id):
    folder = tmp_path / "not-created"
    with pytest.raises(ValueError, match="Task ID"):
        create_task(task_id, tasks_dir=folder)
    assert not folder.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("option", ["--prompt", "--title", "--expect"])
def test_empty_inputs_fail_before_writing(tmp_path, capsys, option):
    folder = tmp_path / "not-created"
    assert main(["new-task", "sample", "--tasks", str(folder), option, " \t"]) == 1
    assert not folder.exists()
    assert "nonempty" in capsys.readouterr().err


def test_symlink_destination_cannot_overwrite_external_file(tmp_path):
    external = tmp_path / "external.yaml"
    external.write_text("unchanged")
    folder = tmp_path / "tasks"
    folder.mkdir()
    (folder / "sample.yaml").symlink_to(external)
    with pytest.raises(FileExistsError):
        create_task("sample", tasks_dir=folder)
    assert external.read_text() == "unchanged"


def test_generated_yaml_uses_existing_task_fields(tmp_path):
    path = create_task("sample", tasks_dir=tmp_path, prompt="Print ANSWER=42.", expect=["ANSWER=42"])
    data = yaml.safe_load(path.read_text())
    assert set(data) == {"version", "id", "title", "prompt", "max_steps", "timeout_seconds", "grader"}
    assert load_task(path).id == "sample"
