import importlib.util
import json
from html.parser import HTMLParser
from pathlib import Path
import sys
import zipfile

import pytest
import yaml

from temple_cua.tasks import load_tasks


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "examples/cua-starter"
CURATED = ["arithmetic", "cursor_callback", "interactive_counter_panel"]
spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts/build_site.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def read_json(path):
    return json.loads(path.read_text())


def archive_files(output):
    with zipfile.ZipFile(output / "assets/starter-run.zip") as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


class ArtifactLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        self.targets.extend(value for key, value in attrs if key in {"href", "src"})


@pytest.fixture
def source_results():
    envelope = read_json(RUN / "results.json")
    assert envelope["provider"] == "cua"
    assert envelope["provenance"]["execution_mode"] == "live_model"
    assert len(envelope["results"]) == 6
    return {record["task_id"]: record for record in envelope["results"]}


def test_default_catalog_uses_curated_tasks_and_preserves_real_task_snapshot(tmp_path, source_results):
    assert yaml.safe_load((ROOT / "site/tasks.yaml").read_text()) == CURATED
    summary = builder.build(RUN, tmp_path)
    catalog = read_json(tmp_path / "assets/starter-data.json")
    tasks = catalog["tasks"]
    assert [task["id"] for task in tasks] == CURATED
    assert [task["difficulty"] for task in tasks] == ["easy", "medium", "hard"]
    assert summary["tasks"] == 3
    assert catalog["run"]["model"] == read_json(RUN / "results.json")["model"]
    assert tasks[0]["result"] == source_results["arithmetic"]
    snapshot = read_json(RUN / "arithmetic/task.json")
    assert tasks[0]["prompt"] == snapshot["prompt"]
    assert tasks[0]["grader"] == snapshot["grader"]
    assert "do not just type a precomputed answer" in tasks[0]["prompt"]
    exported = yaml.safe_load((tmp_path / "assets/tasks/arithmetic.yaml").read_text())
    assert exported == {"version": 1, **snapshot}
    definitions = {task.id: task for task in load_tasks(ROOT / "tasks")}
    for task in tasks[1:]:
        assert task["result"] is None
        assert task["frames"] == []
        assert task["prompt"] == definitions[task["id"]].prompt
        assert task["grader"] == definitions[task["id"]].grader
    assert sorted(path.stem for path in (tmp_path / "assets/tasks").glob("*.yaml")) == sorted(CURATED)
    frames = tasks[0]["frames"]
    assert len(frames) == source_results["arithmetic"]["steps"] + 1
    assert summary["frames"] == len(frames)
    for frame in frames:
        image = tmp_path / frame["src"]
        assert image.read_bytes() == (RUN / "arithmetic" / image.name).read_bytes()


def test_downloads_contain_only_curated_recorded_result_and_its_artifacts(tmp_path, source_results):
    builder.build(RUN, tmp_path)
    download = read_json(tmp_path / "assets/starter-results.json")
    assert download["results"] == [source_results["arithmetic"]]
    archive = archive_files(tmp_path)
    assert json.loads(archive["starter-run/results.json"]) == download
    required = {
        "starter-run/report.html", "starter-run/results.json",
        "starter-run/arithmetic/task.json", "starter-run/arithmetic/result.json",
        "starter-run/arithmetic/trajectory.jsonl", "starter-run/arithmetic/final.png",
        "starter-run/arithmetic/grader-ocr.txt",
    }
    assert required <= archive.keys()
    assert {Path(name).parts[1] for name in archive if len(Path(name).parts) > 2} == {"arithmetic"}
    assert json.loads(archive["starter-run/arithmetic/task.json"]) == read_json(RUN / "arithmetic/task.json")
    assert json.loads(archive["starter-run/arithmetic/result.json"]) == source_results["arithmetic"]
    for name, content in archive.items():
        if name.endswith(".png"):
            assert content == (RUN / Path(name).relative_to("starter-run")).read_bytes()
    html = archive["starter-run/report.html"].decode()
    assert "Task arithmetic" in html
    assert html.count("<article>") == 1
    for excluded in source_results.keys() - {"arithmetic"}:
        assert excluded not in html
        assert source_results[excluded]["title"] not in html
        assert not (tmp_path / "assets/starter-frames" / excluded).exists()
    links = ArtifactLinks()
    links.feed(html)
    assert "arithmetic/final.png" in links.targets
    assert "arithmetic/trajectory.jsonl" in links.targets
    assert "arithmetic/grader-ocr.txt" in links.targets
    assert all("starter-run/" + target in archive for target in links.targets)


def test_repeated_builds_produce_identical_download_archive(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    builder.build(RUN, first)
    builder.build(RUN, second)
    assert (first / "assets/starter-run.zip").read_bytes() == (second / "assets/starter-run.zip").read_bytes()
    assert read_json(first / "assets/starter-data.json") == read_json(second / "assets/starter-data.json")


def test_rebuilding_existing_output_removes_excluded_catalog_assets(tmp_path, source_results):
    builder.build(RUN, tmp_path, selected=list(source_results))
    assert (tmp_path / "assets/starter-frames/file_roundtrip/0000.png").is_file()
    builder.build(RUN, tmp_path)
    assert sorted(path.stem for path in (tmp_path / "assets/tasks").glob("*.yaml")) == sorted(CURATED)
    assert {path.name for path in (tmp_path / "assets/starter-frames").iterdir()} == {"arithmetic"}
    builder.build(RUN, tmp_path, selected=["cursor_callback"])
    assert sorted(path.stem for path in (tmp_path / "assets/tasks").glob("*.yaml")) == ["cursor_callback"]
    assert not (tmp_path / "assets/starter-frames").exists()


def test_explicit_selection_overrides_catalog_order_and_recorded_subset(tmp_path, source_results):
    selected = ["file_roundtrip", "interactive_counter_panel", "arithmetic"]
    builder.build(RUN, tmp_path, selected=selected)
    tasks = read_json(tmp_path / "assets/starter-data.json")["tasks"]
    assert [task["id"] for task in tasks] == selected
    assert tasks[1]["result"] is None and tasks[1]["frames"] == []
    for task in (tasks[0], tasks[2]):
        assert task["result"] == source_results[task["id"]]
        assert task["prompt"] == read_json(RUN / task["id"] / "task.json")["prompt"]
    expected = [source_results[task_id] for task_id in ("file_roundtrip", "arithmetic")]
    assert read_json(tmp_path / "assets/starter-results.json")["results"] == expected
    archive = archive_files(tmp_path)
    assert json.loads(archive["starter-run/results.json"])["results"] == expected
    assert {Path(name).parts[1] for name in archive if len(Path(name).parts) > 2} == {"file_roundtrip", "arithmetic"}
    report = archive["starter-run/report.html"].decode()
    assert report.count("<article>") == 2
    assert report.index("Task file_roundtrip") < report.index("Task arithmetic")


def test_unrecorded_only_selection_builds_valid_empty_results_archive(tmp_path):
    selected = ["interactive_counter_panel", "cursor_callback"]
    summary = builder.build(RUN, tmp_path, selected=selected)
    tasks = read_json(tmp_path / "assets/starter-data.json")["tasks"]
    assert [task["id"] for task in tasks] == selected
    assert all(task["result"] is None and task["frames"] == [] for task in tasks)
    assert summary["tasks"] == 2 and summary["frames"] == 0
    assert read_json(tmp_path / "assets/starter-results.json")["results"] == []
    archive = archive_files(tmp_path)
    assert set(archive) == {"starter-run/results.json", "starter-run/report.html"}
    assert json.loads(archive["starter-run/results.json"])["results"] == []
    report = archive["starter-run/report.html"].decode()
    assert "No tasks" in report and "<article>" not in report
    assert not (tmp_path / "assets/starter-frames").exists()


@pytest.mark.parametrize("selected", [
    ["arithmetic", "arithmetic"],
    ["arithmetic", "unknown_task"],
    ["arithmetic", "../escape"],
    ["arithmetic", "/absolute"],
    ["arithmetic", "a/b"],
    ["arithmetic", "a\\b"],
    ["arithmetic", ".."],
    ["arithmetic", "ARITHMETIC"],
    ["arithmetic", "has space"],
    ["arithmetic", ""],
    ["arithmetic", None],
    [],
])
def test_invalid_selection_is_rejected_before_writing_output(tmp_path, selected):
    output = tmp_path / "site"
    with pytest.raises(ValueError):
        builder.build(RUN, output, selected=selected)
    assert not output.exists()
    output.mkdir()
    sentinel = output / "existing-content.txt"
    sentinel.write_bytes(b"retain this existing build")
    with pytest.raises(ValueError):
        builder.build(RUN, output, selected=selected)
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"retain this existing build"


@pytest.mark.parametrize("selected", [
    ["arithmetic", "arithmetic"], ["unknown_task"], ["../escape"],
])
def test_cli_invalid_selection_does_not_delete_existing_output(tmp_path, monkeypatch, selected):
    output = tmp_path / "site"
    output.mkdir()
    sentinel = output / "existing-content.txt"
    sentinel.write_bytes(b"retain this existing build")
    args = ["build_site", str(RUN), "--output", str(output)]
    for task_id in selected:
        args.extend(["--task", task_id])
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises((ValueError, SystemExit)) as caught:
        builder.main()
    if isinstance(caught.value, SystemExit):
        assert caught.value.code != 0
    assert sentinel.read_bytes() == b"retain this existing build"
    assert list(output.iterdir()) == [sentinel]


@pytest.mark.parametrize("source_name", ["site", "recorded", "tasks"])
@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant"])
def test_cli_output_cannot_overlap_source_directories(tmp_path, monkeypatch, source_name, relation):
    root = tmp_path / "project"
    run = root / "recorded"
    for name in ("site", "recorded", "tasks"):
        (root / name).mkdir(parents=True)
    source = root / source_name
    descendant = source / "preserved"
    descendant.mkdir()
    output = {"equal": source, "ancestor": root, "descendant": descendant}[relation]
    sentinel = descendant / "source-evidence.txt"
    sentinel.write_bytes(b"do not destroy source files")
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["build_site", str(run), "--output", str(output)])
    with pytest.raises(SystemExit) as caught:
        builder.main()
    assert caught.value.code != 0
    assert sentinel.read_bytes() == b"do not destroy source files"
