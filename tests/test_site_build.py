import importlib.util
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest
import yaml

from temple_cua.tasks import load_tasks


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "recordings/cua-starter"
CURATED = ["arithmetic", "cursor_callback", "interactive_counter_panel", "tictactoe_sprite", "uart_irq_service"]
FEATURED_RECORDINGS = ["cua-starter", "cua-cursor", "cua-counter", "cua-sprite-rerun", "cua-uart-300"]
spec = importlib.util.spec_from_file_location("build_site", ROOT / "website/build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def read_json(path):
    return json.loads(path.read_text())


def make_uart_grade_fixture(run, evidence=None):
    folder = run / "cursor_callback"
    task = read_json(folder / "task.json")
    task["grader"] = {"type": "uart", "rubric": "Host byte and interrupt checks."}
    (folder / "task.json").write_text(json.dumps(task))
    envelope = read_json(run / "results.json")
    envelope["results"][0]["grade"] = {
        "type": "uart", "status": "failed", "score": 0.3,
        "reason": "3/10 host checks passed", "passed_cases": 3, "total_cases": 10,
        "cases": [{"name": "valid_short", "status": "passed", "score": 1.0}],
        "evidence": evidence if evidence is not None else ["evaluation.json", "host-stop.png"],
    }
    (run / "results.json").write_text(json.dumps(envelope))
    (folder / "result.json").write_text(json.dumps(envelope["results"][0]))
    return folder, envelope["results"][0]


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


class BuildAssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.urls["assets/starter-data.json"] = attrs.get("data-task-data")
        elif tag == "script" and urlsplit(attrs.get("src", "")).path == "app.js":
            self.urls["app.js"] = attrs["src"]
        elif tag == "link" and attrs.get("rel") == "stylesheet":
            self.urls["styles.css"] = attrs.get("href")
        elif tag == "a" and urlsplit(attrs.get("href", "")).path in {"assets/starter-results.json", "assets/starter-run.zip"}:
            self.urls[urlsplit(attrs["href"]).path] = attrs["href"]


def built_asset_urls(output):
    parser = BuildAssetLinks()
    parser.feed((output / "index.html").read_text())
    assert set(parser.urls) == {"app.js", "styles.css", "assets/starter-data.json",
                               "assets/starter-results.json", "assets/starter-run.zip"}
    for filename, url in parser.urls.items():
        assert url is not None
        parts = urlsplit(url)
        assert parts.scheme == "" and parts.netloc == "" and parts.fragment == ""
        assert parts.path == filename
        fingerprint = hashlib.sha256((output / filename).read_bytes()).hexdigest()[:12]
        assert parse_qs(parts.query) == {"v": [fingerprint]}
    return parser.urls


@pytest.fixture
def source_results():
    envelope = read_json(RUN / "results.json")
    assert envelope["provider"] == "cua"
    assert envelope["provenance"]["execution_mode"] == "live_model"
    assert len(envelope["results"]) == 6
    return {record["task_id"]: record for record in envelope["results"]}


@pytest.fixture
def supplemental_run(tmp_path):
    run = tmp_path / "callback-recording-fixture"
    folder = run / "cursor_callback"
    # Reuse real image bytes to test packaging; this fixture is not callback evidence.
    shutil.copytree(RUN / "arithmetic", folder)
    task = yaml.safe_load((ROOT / "tasks/07_cursor_callback.yaml").read_text())
    task.pop("version")
    task["source"] = "/fixture-host/tasks/cursor_callback.yaml"
    (folder / "task.json").write_text(json.dumps(task))
    envelope = read_json(RUN / "results.json")
    record = dict(envelope["results"][0])
    record.update({
        "task_id": "cursor_callback", "title": task["title"],
        "artifact_dir": "cursor_callback", "final_screenshot": "cursor_callback/final.png",
        "grade": {"type": "manual", "status": "needs_review", "score": None,
                  "reason": "Review the full trajectory.", "evidence": []},
        "budget": {"max_steps": 40, "timeout_seconds": 240},
        "completion_text": "Packaging fixture only.",
    })
    record.pop("task_fingerprint", None)
    envelope.update({"model": "openai/fixture-callback-model",
                     "created_at": "2026-10-05T02:10:00+00:00", "results": [record]})
    envelope["config"]["max_steps_override"] = 40
    envelope["config"]["timeout_override"] = 240
    envelope["config"]["model_options"]["max_output_tokens"] = 2048
    envelope["provenance"]["test_fixture"] = "Derived artifact packaging fixture, not a model run."
    (folder / "result.json").write_text(json.dumps(record))
    (run / "results.json").write_text(json.dumps(envelope))
    return run


def test_explicit_primary_recording_uses_curated_tasks_and_preserves_real_task_snapshot(tmp_path, source_results):
    assert yaml.safe_load((ROOT / "website/config.yaml").read_text())["tasks"] == CURATED
    summary = builder.build(RUN, tmp_path)
    catalog = read_json(tmp_path / "assets/starter-data.json")
    tasks = catalog["tasks"]
    assert [task["id"] for task in tasks] == CURATED
    assert [task["difficulty"] for task in tasks] == ["easy", "medium", "hard", "hard", "hard"]
    assert summary["tasks"] == 5
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


def test_default_build_features_all_five_configured_recordings_without_changing_results(tmp_path):
    config = yaml.safe_load((ROOT / "website/config.yaml").read_text())
    assert config == {"tasks": CURATED,
                      "recordings": [f"recordings/{name}" for name in FEATURED_RECORDINGS]}
    summary = builder.build(output=tmp_path)
    catalog = read_json(tmp_path / "assets/starter-data.json")
    assert [task["id"] for task in catalog["tasks"]] == CURATED
    assert len(catalog["runs"]) == 5
    assert summary["tasks"] == 5 and summary["frames"] == 206
    downloads = read_json(tmp_path / "assets/starter-results.json")
    assert downloads["format"] == "templeosbench-recorded-runs"
    assert len(downloads["runs"]) == 5
    for task, name, exported in zip(catalog["tasks"], FEATURED_RECORDINGS, downloads["runs"]):
        source = ROOT / "recordings" / name
        envelope = read_json(source / "results.json")
        result = next(record for record in envelope["results"] if record["task_id"] == task["id"])
        assert task["result"] == result
        assert task["run"]["id"].endswith("-" + name)
        for key in ("provider", "model", "created_at", "config", "provenance"):
            assert task["run"][key] == envelope[key]
        assert {key: value for key, value in exported.items() if key != "id"} == {
            **envelope, "results": [result],
        }
        assert len(task["frames"]) == result["steps"] + 1
        if (source / task["id"] / "review.json").is_file():
            assert task["review"] == read_json(source / task["id"] / "review.json")
    uart = catalog["tasks"][-1]
    assert uart["result"]["steps"] == 32
    assert uart["result"]["budget"]["max_steps"] == 300
    assert uart["result"]["budget"]["timeout_seconds"] == 3600
    assert uart["result"]["grade"]["score"] == 0.0
    for name, relative in uart["evaluation_artifacts"].items():
        assert (tmp_path / relative).read_bytes() == (ROOT / "recordings/cua-uart-300/uart_irq_service" / name).read_bytes()
    built_asset_urls(tmp_path)


def test_cli_defaults_use_configuration_from_any_working_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "published"
    monkeypatch.setattr(sys, "argv", ["website/build.py", "--output", str(output)])
    builder.main()
    assert json.loads(capsys.readouterr().out)["tasks"] == 5
    tasks = read_json(output / "assets/starter-data.json")["tasks"]
    assert [task["id"] for task in tasks] == CURATED
    assert [task["run"]["id"].split("-", 1)[1] for task in tasks] == FEATURED_RECORDINGS


def test_cli_run_flags_replace_configured_recordings_without_implicit_duplicates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["website/build.py", "--run", str(RUN),
                                     "--task", "arithmetic", "--output", str(tmp_path)])
    builder.main()
    assert json.loads(capsys.readouterr().out)["tasks"] == 1
    catalog = read_json(tmp_path / "assets/starter-data.json")
    assert [task["id"] for task in catalog["tasks"]] == ["arithmetic"]
    assert len(catalog["runs"]) == 1
    assert catalog["runs"][0]["id"] == "01-cua-starter"
    assert "runs" not in read_json(tmp_path / "assets/starter-results.json")


@pytest.mark.parametrize("config, message", [
    ([], "must contain only tasks and recordings"),
    ({"tasks": ["arithmetic"]}, "must contain only tasks and recordings"),
    ({"tasks": ["arithmetic"], "recordings": ["recordings/cua-starter"], "typo": True},
     "must contain only tasks and recordings"),
    ({"tasks": [], "recordings": ["recordings/cua-starter"]}, "nonempty list of task IDs"),
    ({"tasks": ["arithmetic", "arithmetic"], "recordings": ["recordings/cua-starter"]},
     "Duplicate selected task IDs"),
    ({"tasks": ["arithmetic"], "recordings": []}, "nonempty list of repository-relative paths"),
    ({"tasks": ["arithmetic"], "recordings": "recordings/cua-starter"},
     "nonempty list of repository-relative paths"),
    ({"tasks": ["arithmetic"], "recordings": [None]}, "nonempty list of repository-relative paths"),
    ({"tasks": ["arithmetic"], "recordings": [" "]}, "nonempty list of repository-relative paths"),
    ({"tasks": ["arithmetic"], "recordings": ["/tmp/run"]}, "relative to the repository root"),
    ({"tasks": ["arithmetic"], "recordings": ["../run"]}, "relative to the repository root"),
    ({"tasks": ["arithmetic"], "recordings": ["recordings/cua-starter", "recordings/./cua-starter"]},
     "Duplicate website recording paths"),
])
def test_invalid_configuration_preserves_existing_output(tmp_path, config, message):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    output = tmp_path / "published"
    output.mkdir()
    sentinel = output / "existing-build.txt"
    sentinel.write_bytes(b"preserve before configuration validation")
    with pytest.raises(ValueError, match=message):
        builder.build(RUN, output, config=config_path)
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve before configuration validation"


@pytest.mark.parametrize("case", ["missing", "malformed", "missing_recording", "unknown_task"])
def test_configuration_loading_failures_preserve_existing_output(tmp_path, case):
    config_path = tmp_path / "config.yaml"
    message = "Could not read website configuration"
    if case == "malformed":
        config_path.write_text("tasks: [unterminated")
    elif case in {"missing_recording", "unknown_task"}:
        config = {"tasks": ["unknown_task" if case == "unknown_task" else "arithmetic"],
                  "recordings": ["recordings/missing-recording" if case == "missing_recording"
                                 else "recordings/cua-starter"]}
        config_path.write_text(yaml.safe_dump(config))
        message = "missing results.json" if case == "missing_recording" else "Unknown selected task IDs"
    output = tmp_path / "published"
    output.mkdir()
    sentinel = output / "existing-build.txt"
    sentinel.write_bytes(b"preserve failed configuration build")
    with pytest.raises(ValueError, match=message):
        builder.build(output=output, config=config_path)
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve failed configuration build"


def test_five_task_catalog_preserves_three_recordings_and_leaves_new_tasks_unrun(tmp_path):
    sources = [RUN, ROOT / "recordings/cua-cursor", ROOT / "recordings/cua-counter"]
    summary = builder.build(sources[0], tmp_path, additional_runs=sources[1:])
    tasks = read_json(tmp_path / "assets/starter-data.json")["tasks"]
    assert [task["id"] for task in tasks] == CURATED
    assert summary["tasks"] == 5
    assert sum(task["result"] is not None for task in tasks) == 3
    for task, source in zip(tasks[:3], sources):
        original = next(result for result in read_json(source / "results.json")["results"]
                        if result["task_id"] == task["id"])
        assert task["result"] == original
    sprite = tasks[3]
    assert sprite["result"] is None and sprite["frames"] == []
    assert sprite.get("run") is None and sprite.get("review") is None
    definition = load_tasks(ROOT / "tasks", selected=[sprite["id"]])[0]
    assert sprite["prompt"] == definition.prompt
    assert sprite["grader"] == definition.grader
    downloads = read_json(tmp_path / "assets/starter-results.json")
    assert [record["task_id"] for run in downloads["runs"] for record in run["results"]] == CURATED[:3]
    assert not any("tictactoe_sprite" in Path(name).parts for name in archive_files(tmp_path))


def test_four_real_sources_preserve_provenance_downloads_and_sprite_error_review(tmp_path):
    sources = [RUN, ROOT / "recordings/cua-cursor", ROOT / "recordings/cua-counter",
               ROOT / "recordings/cua-sprite"]
    summary = builder.build(sources[0], tmp_path, additional_runs=sources[1:])
    catalog = read_json(tmp_path / "assets/starter-data.json")
    assert summary["tasks"] == 5
    assert [task["id"] for task in catalog["tasks"]] == CURATED
    assert len(catalog["runs"]) == 4
    assert len({metadata["id"] for metadata in catalog["runs"]}) == 4
    download = read_json(tmp_path / "assets/starter-results.json")
    assert download["format"] == "templeosbench-recorded-runs"
    assert len(download["runs"]) == 4
    archive = archive_files(tmp_path)
    assert json.loads(archive["starter-run/results.json"]) == download
    for task, source, exported in zip(catalog["tasks"], sources, download["runs"]):
        envelope = read_json(source / "results.json")
        original = next(record for record in envelope["results"] if record["task_id"] == task["id"])
        assert task["result"] == original
        metadata = task["run"]
        for key in ("provider", "model", "created_at", "config", "provenance"):
            assert metadata[key] == envelope[key]
        assert metadata["cua_version"] == envelope["config"]["model_options"]["cua_version"]
        assert metadata in catalog["runs"]
        filtered = {**envelope, "results": [original]}
        assert {key: value for key, value in exported.items() if key != "id"} == filtered
        assert exported["id"] == metadata["id"]
        prefix = f"starter-run/{metadata['id']}/"
        assert json.loads(archive[prefix + "results.json"]) == filtered
        folder = source / task["id"]
        snapshot = read_json(folder / "task.json")
        snapshot.pop("source", None)
        assert task["prompt"] == snapshot["prompt"]
        assert task["grader"] == snapshot["grader"]
        assert yaml.safe_load((tmp_path / f"assets/tasks/{task['id']}.yaml").read_text()) == {
            "version": 1, **snapshot,
        }
        assert json.loads(archive[prefix + task["id"] + "/task.json"]) == snapshot
        assert json.loads(archive[prefix + task["id"] + "/result.json"]) == original
        assert archive[prefix + task["id"] + "/final.png"] == (folder / "final.png").read_bytes()
        trajectory = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines()]
        archived_trajectory = [json.loads(line) for line in
                               archive[prefix + task["id"] + "/trajectory.jsonl"].decode().splitlines()]
        assert archived_trajectory == trajectory
        for frame in task["frames"]:
            assert (tmp_path / frame["src"]).read_bytes() == (folder / Path(frame["src"]).name).read_bytes()
        if (folder / "review.json").exists():
            review = read_json(folder / "review.json")
            assert task["review"] == review
            assert json.loads(archive[prefix + task["id"] + "/review.json"]) == review
            assert "review" not in task["result"]
        report = archive[prefix + "report.html"].decode()
        assert "Task " + task["id"] in report
        assert envelope["model"] in report and envelope["created_at"] in report
        assert report.count("<article>") == 1
    sprite = catalog["tasks"][3]
    assert sprite["result"]["status"] == "error"
    assert sprite["result"]["steps"] == 24
    assert sprite["result"]["error"].startswith("CuaProtocolError:")
    assert sprite["result"]["grade"]["status"] == "needs_review"
    assert sprite["result"]["grade"]["score"] is None
    assert sprite["review"]["status"] == "incomplete"
    assert len(sprite["frames"]) == 25
    assert summary["frames"] == sum(len(task["frames"]) for task in catalog["tasks"])


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


@pytest.mark.parametrize("source_name", ["website", "recorded", "tasks"])
@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant"])
def test_cli_output_cannot_overlap_source_directories(tmp_path, monkeypatch, source_name, relation):
    root = tmp_path / "project"
    run = root / "recorded"
    for name in ("website", "recorded", "tasks"):
        (root / name).mkdir(parents=True)
    source = root / source_name
    descendant = source / "preserved"
    descendant.mkdir()
    output = {"equal": source, "ancestor": root, "descendant": descendant}[relation]
    sentinel = descendant / "source-evidence.txt"
    sentinel.write_bytes(b"do not destroy source files")
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["website/build.py", str(run), "--task", "arithmetic",
                                     "--output", str(output)])
    with pytest.raises(SystemExit) as caught:
        builder.main()
    assert caught.value.code != 0
    assert sentinel.read_bytes() == b"do not destroy source files"


def test_supplementary_runs_preserve_task_provenance_and_separate_download_envelopes(tmp_path, supplemental_run):
    output = tmp_path / "site"
    builder.build(RUN, output, additional_runs=[supplemental_run])
    catalog = read_json(output / "assets/starter-data.json")
    assert [task["id"] for task in catalog["tasks"]] == CURATED
    assert catalog["run"]["model"] == read_json(RUN / "results.json")["model"]
    assert len(catalog["runs"]) == 2
    original, supplementary = read_json(RUN / "results.json"), read_json(supplemental_run / "results.json")
    arithmetic, callback, panel, sprite, uart = catalog["tasks"]
    assert uart["result"] is None and uart["frames"] == []
    for task, source in ((arithmetic, original), (callback, supplementary)):
        metadata = task["run"]
        for key in ("provider", "model", "created_at", "config", "provenance"):
            assert metadata[key] == source[key]
        assert metadata["cua_version"] == source["config"]["model_options"]["cua_version"]
        assert metadata in catalog["runs"]
        assert task["result"] == source["results"][0]
    assert arithmetic["run"]["model"] != callback["run"]["model"]
    assert arithmetic["run"]["created_at"] != callback["run"]["created_at"]
    assert arithmetic["run"]["config"] != callback["run"]["config"]
    assert panel["result"] is None and panel["frames"] == []
    assert sprite["result"] is None and sprite["frames"] == []
    assert callback["prompt"] == read_json(supplemental_run / "cursor_callback/task.json")["prompt"]
    download = read_json(output / "assets/starter-results.json")
    assert download["format"] == "templeosbench-recorded-runs" and download["version"] == 1
    assert "model" not in download and "config" not in download
    assert len(download["runs"]) == 2
    archive = archive_files(output)
    assert json.loads(archive["starter-run/results.json"]) == download
    for exported, source, task in zip(download["runs"], (original, supplementary), (arithmetic, callback)):
        expected = {**source, "results": [source["results"][0]]}
        assert {key: value for key, value in exported.items() if key != "id"} == expected
        assert exported["id"] == task["run"]["id"]
        prefix = "starter-run/" + exported["id"] + "/"
        assert json.loads(archive[prefix + "results.json"])["results"] == expected["results"]
        assert json.loads(archive[prefix + "results.json"])["config"] == source["config"]
        assert prefix + task["id"] + "/trajectory.jsonl" in archive
        assert prefix + task["id"] + "/final.png" in archive
        report = archive[prefix + "report.html"].decode()
        assert "Task " + task["id"] in report
        assert source["model"] in report
        assert source["created_at"] in report
        assert report.count("<article>") == 1
        links = ArtifactLinks()
        links.feed(report)
        assert all(prefix + target in archive for target in links.targets)
    excluded = {record["task_id"] for record in original["results"]} - {"arithmetic"}
    assert not any(part in excluded for name in archive for part in Path(name).parts)
    for frame in callback["frames"]:
        assert (output / frame["src"]).read_bytes() == (supplemental_run / "cursor_callback" / Path(frame["src"]).name).read_bytes()


@pytest.mark.parametrize("status", ["passed", "failed", "incomplete"])
def test_assistant_review_remains_separate_from_raw_manual_grade(tmp_path, supplemental_run, status):
    output = tmp_path / "site"
    review = {"status": status, "reviewer": "assistant trajectory review",
              "reason": "Fixture review for export separation.", "evidence": ["Step 2: fixture evidence."]}
    (supplemental_run / "cursor_callback/review.json").write_text(json.dumps(review))
    builder.build(RUN, output, selected=["cursor_callback"], additional_runs=[supplemental_run])
    catalog = read_json(output / "assets/starter-data.json")
    task = catalog["tasks"][0]
    source = read_json(supplemental_run / "results.json")
    assert task["review"] == review
    assert task["result"] == source["results"][0]
    assert task["result"]["grade"]["status"] == "needs_review"
    assert task["result"]["grade"]["score"] is None
    assert catalog["run"]["model"] == source["model"]
    download = read_json(output / "assets/starter-results.json")
    assert download == source
    assert "review" not in download["results"][0]
    archive = archive_files(output)
    assert json.loads(archive["starter-run/cursor_callback/review.json"]) == review
    report = archive["starter-run/report.html"].decode()
    assert "Grade: needs review" in report
    assert "Grade: passed" not in report
    assert "Grade: failed" not in report
    assert not any("arithmetic" == part for name in archive for part in Path(name).parts)


def test_multiple_input_archives_are_deterministic_and_allow_no_recorded_selection(tmp_path, supplemental_run):
    first, second = tmp_path / "first", tmp_path / "second"
    for output in (first, second):
        builder.build(RUN, output, additional_runs=[supplemental_run])
    assert (first / "assets/starter-run.zip").read_bytes() == (second / "assets/starter-run.zip").read_bytes()
    builder.build(RUN, first, selected=["interactive_counter_panel"], additional_runs=[supplemental_run])
    catalog = read_json(first / "assets/starter-data.json")
    assert catalog["tasks"][0]["result"] is None and catalog["tasks"][0]["frames"] == []
    assert catalog["runs"] == []
    download = read_json(first / "assets/starter-results.json")
    assert download == {"format": "templeosbench-recorded-runs", "version": 1, "runs": []}
    archive = archive_files(first)
    assert json.loads(archive["starter-run/results.json"]) == download
    assert not any(name.endswith(".png") or name.endswith("trajectory.jsonl") for name in archive)


@pytest.mark.parametrize("duplicate_within_one_run", [False, True])
def test_duplicate_recorded_task_ids_fail_before_existing_output_is_touched(tmp_path, duplicate_within_one_run):
    duplicate_run = tmp_path / "duplicate-source"
    duplicate_run.mkdir()
    envelope = read_json(RUN / "results.json")
    envelope["results"] = [envelope["results"][0]]
    if duplicate_within_one_run:
        envelope["results"].append(dict(envelope["results"][0]))
    (duplicate_run / "results.json").write_text(json.dumps(envelope))
    output = tmp_path / "site"
    output.mkdir()
    sentinel = output / "previous-build.txt"
    sentinel.write_bytes(b"preserve before validation")
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        if duplicate_within_one_run:
            builder.build(duplicate_run, output)
        else:
            builder.build(RUN, output, additional_runs=[duplicate_run])
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve before validation"


def test_cli_repeated_run_argument_routes_supplementary_sources(tmp_path, supplemental_run, monkeypatch, capsys):
    panel_run = tmp_path / "panel-recording-fixture"
    panel_folder = panel_run / "interactive_counter_panel"
    shutil.copytree(supplemental_run / "cursor_callback", panel_folder)
    task = yaml.safe_load((ROOT / "tasks/08_interactive_counter_panel.yaml").read_text())
    task.pop("version")
    (panel_folder / "task.json").write_text(json.dumps(task))
    panel_envelope = read_json(supplemental_run / "results.json")
    panel_envelope["model"] = "openai/fixture-panel-model"
    panel_result = panel_envelope["results"][0]
    panel_result.update({"task_id": "interactive_counter_panel", "title": task["title"],
                         "artifact_dir": "interactive_counter_panel",
                         "final_screenshot": "interactive_counter_panel/final.png"})
    (panel_folder / "result.json").write_text(json.dumps(panel_result))
    (panel_run / "results.json").write_text(json.dumps(panel_envelope))
    output = tmp_path / "site"
    monkeypatch.setattr(sys, "argv", ["build_site", str(RUN), "--run", str(supplemental_run),
                                     "--run", str(panel_run), "--output", str(output)])
    builder.main()
    assert json.loads(capsys.readouterr().out)["tasks"] == 5
    catalog = read_json(output / "assets/starter-data.json")
    assert catalog["tasks"][1]["run"]["model"] == read_json(supplemental_run / "results.json")["model"]
    assert catalog["tasks"][2]["run"]["model"] == panel_envelope["model"]
    assert catalog["tasks"][3]["result"] is None and catalog["tasks"][3]["frames"] == []
    assert len(read_json(output / "assets/starter-results.json")["runs"]) == 3


def test_versioned_asset_urls_match_built_contents_and_repeat_stably(tmp_path, supplemental_run):
    first, second = tmp_path / "first", tmp_path / "second"
    for output in (first, second):
        builder.build(RUN, output, additional_runs=[supplemental_run])
    assert built_asset_urls(first) == built_asset_urls(second)
    legacy = read_json(first / "assets/starter-data.json")["run"]
    # A previously cached viewer dereferences these fields even with several source runs.
    assert isinstance(legacy["model"], str) and legacy["model"].startswith("openai/")
    assert isinstance(legacy["cua_version"], str) and legacy["cua_version"]


@pytest.mark.parametrize("filename", ["app.js", "styles.css"])
def test_editing_source_asset_refreshes_only_its_content_version(tmp_path, monkeypatch, filename):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "website", source / "website")
    shutil.copytree(ROOT / "tasks", source / "tasks")
    monkeypatch.setattr(builder, "ROOT", source)
    first, second = tmp_path / "first", tmp_path / "second"
    builder.build(RUN, first, selected=["arithmetic"])
    asset = source / "website" / filename
    asset.write_bytes(asset.read_bytes() + b"\n/* Changed content for cache regression. */\n")
    builder.build(RUN, second, selected=["arithmetic"])
    before, after = built_asset_urls(first), built_asset_urls(second)
    assert before[filename] != after[filename]
    for unchanged in before.keys() - {filename}:
        assert before[unchanged] == after[unchanged]


def test_catalog_change_refreshes_manifest_url_without_changing_static_asset_urls(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    builder.build(RUN, first, selected=["arithmetic"])
    builder.build(RUN, second, selected=["arithmetic", "cursor_callback"])
    before, after = built_asset_urls(first), built_asset_urls(second)
    assert before["assets/starter-data.json"] != after["assets/starter-data.json"]
    assert before["app.js"] == after["app.js"]
    assert before["styles.css"] == after["styles.css"]


def test_viewer_fetches_html_manifest_url_without_http_cache(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the viewer's fetch contract")
    builder.build(RUN, tmp_path)
    manifest = built_asset_urls(tmp_path)["assets/starter-data.json"]
    probe = r"""
const fs = require('fs');
const vm = require('vm');
const calls = [];
const element = {addEventListener() {}};
const context = {
  document: {
    documentElement: {dataset: {taskData: process.argv[2]}},
    getElementById() { return element; },
    addEventListener() {}
  },
  fetch(url, options) {
    calls.push({url, options});
    return new Promise(() => {});
  }
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
process.stdout.write(JSON.stringify(calls));
"""
    completed = subprocess.run([node, "-e", probe, str(tmp_path / "app.js"), manifest],
                               check=True, capture_output=True, text=True, timeout=10)
    calls = json.loads(completed.stdout)
    assert len(calls) == 1
    assert calls[0]["url"] == manifest
    assert calls[0]["options"]["cache"] == "no-store"


def test_rejected_input_evidence_survives_manifest_and_portable_download_without_changing_grade(tmp_path, supplemental_run):
    folder = supplemental_run / "cursor_callback"
    raw = '{"action":"type","text":"<img src=x onerror=alert(1)>",\nBROKEN'
    requested = {"call_id": "fixture-malformed", "name": "computer", "arguments": raw}
    error = {"call_id": "fixture-malformed", "arguments": raw,
             "error": {"code": "invalid_computer_action", "message": "Computer arguments must be valid JSON"},
             "input_executed": False, "screenshot": "observations/0001.png"}
    turn = {"step": 1, "note": "Input rejected; current screenshot returned.",
            "actions": [], "executed_actions": [], "requested_calls": [requested], "input_errors": [error],
            "screenshot_before": "0000.png", "screenshot_after": "0001.png",
            "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            "cua_output": [{"type": "reasoning", "encrypted_content": "fixture-private-provider-payload"}],
            "response_id": "fixture-private-response-id"}
    (folder / "trajectory.jsonl").write_text(json.dumps(turn) + "\n")
    source = read_json(supplemental_run / "results.json")
    result = source["results"][0]
    result.update({"status": "budget_exhausted", "steps": 1,
                   "usage": turn["usage"], "elapsed_seconds": 1.0})
    result.pop("completion_text", None)
    (folder / "result.json").write_text(json.dumps(result))
    (supplemental_run / "results.json").write_text(json.dumps(source))
    output = tmp_path / "site"
    builder.build(supplemental_run, output, selected=["cursor_callback"])
    task = read_json(output / "assets/starter-data.json")["tasks"][0]
    initial, rejected = task["frames"]
    assert initial["requested_calls"] == initial["input_errors"] == initial["actions"] == []
    assert rejected["requested_calls"] == [requested]
    assert rejected["input_errors"] == [error]
    assert rejected["actions"] == []
    assert task["result"] == result
    assert task["result"]["grade"]["status"] == "needs_review"
    assert task["result"]["grade"]["score"] is None
    assert "cua_output" not in rejected and "response_id" not in rejected
    assert (output / rejected["src"]).read_bytes() == (folder / "0001.png").read_bytes()
    assert read_json(output / "assets/starter-results.json") == source
    archive = archive_files(output)
    downloaded_turns = [json.loads(line) for line in archive["starter-run/cursor_callback/trajectory.jsonl"].splitlines()]
    assert len(downloaded_turns) == 1
    downloaded = downloaded_turns[0]
    assert downloaded["requested_calls"] == [requested] and downloaded["input_errors"] == [error]
    assert downloaded["actions"] == downloaded["executed_actions"] == []
    assert "cua_output" not in downloaded and "response_id" not in downloaded
    assert json.loads(archive["starter-run/results.json"]) == source
    assert json.loads(archive["starter-run/cursor_callback/result.json"]) == result
    assert "Grade: needs review" in archive["starter-run/report.html"].decode()
    assert archive["starter-run/cursor_callback/" + error["screenshot"]] == (folder / error["screenshot"]).read_bytes()
    # Sanitization applies to the export; it never rewrites the recorded source.
    assert json.loads((folder / "trajectory.jsonl").read_text()) == turn


def test_older_recordings_receive_empty_recovery_fields_without_inventing_errors(tmp_path):
    builder.build(RUN, tmp_path, selected=["arithmetic"])
    task = read_json(tmp_path / "assets/starter-data.json")["tasks"][0]
    source_turns = [json.loads(line) for line in (RUN / "arithmetic/trajectory.jsonl").read_text().splitlines()]
    assert all("requested_calls" not in turn and "input_errors" not in turn for turn in source_turns)
    assert all(frame["requested_calls"] == frame["input_errors"] == [] for frame in task["frames"])
    for frame, turn in zip(task["frames"][1:], source_turns):
        assert frame["actions"] == turn["executed_actions"]


def test_uart_host_evidence_preserves_fractional_reward_without_adding_model_frames(tmp_path, supplemental_run):
    folder, result = make_uart_grade_fixture(supplemental_run)
    evaluation = {"phase": "host_evaluation_after_policy", "model_actions_added": 0,
                  "grade": result["grade"], "transcript": [{"direction": "host_to_guest", "hex": "a501010203"}]}
    (folder / "evaluation.json").write_text(json.dumps(evaluation))
    shutil.copyfile(folder / "final.png", folder / "host-stop.png")
    original_turns = (folder / "trajectory.jsonl").read_bytes()
    output = tmp_path / "published-host-fixture"
    builder.build(supplemental_run, output, selected=["cursor_callback"])
    task = read_json(output / "assets/starter-data.json")["tasks"][0]
    assert task["result"] == result
    assert task["result"]["grade"]["score"] == 0.3
    assert len(task["frames"]) == len(original_turns.splitlines()) + 1
    assert task["evaluation_artifacts"] == {
        "evaluation.json": "assets/evaluations/cursor_callback/evaluation.json",
        "host-stop.png": "assets/evaluations/cursor_callback/host-stop.png",
    }
    for name, url in task["evaluation_artifacts"].items():
        assert (output / url).read_bytes() == (folder / name).read_bytes()
    archived = archive_files(output)
    assert json.loads(archived["starter-run/cursor_callback/evaluation.json"]) == evaluation
    assert archived["starter-run/cursor_callback/trajectory.jsonl"] == original_turns
    assert json.loads(archived["starter-run/cursor_callback/result.json"]) == result


@pytest.mark.parametrize("unsafe", ["../evaluation.json", "/evaluation.json", "nested/evaluation.json"])
def test_uart_host_evidence_rejects_paths_outside_task_directory(tmp_path, supplemental_run, unsafe):
    make_uart_grade_fixture(supplemental_run, [unsafe])
    with pytest.raises(ValueError, match="filename within its task directory"):
        builder.build(supplemental_run, tmp_path / "output", selected=["cursor_callback"])


@pytest.mark.parametrize("kind", ["missing", "symlink"])
def test_uart_host_evidence_requires_real_local_artifact(tmp_path, supplemental_run, kind):
    folder, _ = make_uart_grade_fixture(supplemental_run, ["evaluation.json"])
    if kind == "symlink":
        target = tmp_path / "outside.json"
        target.write_text('{}')
        (folder / "evaluation.json").symlink_to(target)
    with pytest.raises(ValueError, match="Missing or unsafe"):
        builder.build(supplemental_run, tmp_path / "output", selected=["cursor_callback"])
