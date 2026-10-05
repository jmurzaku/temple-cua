import json

import pytest

from temple_cua.report import build_report, compare_results


def write_run(directory, results, *, provider="openai", model="test-model"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(json.dumps({
        "created_at": "2026-10-04T00:00:00Z", "provider": provider,
        "model": model, "results": results,
    }))
    return directory


def result(task_id="example", *, execution="completed", grade_status="passed", score=1.0,
           grader_type="ocr"):
    return {"task_id": task_id, "title": task_id, "status": execution, "steps": 3,
            "elapsed_seconds": 1.5, "artifact_dir": task_id,
            "final_screenshot": f"{task_id}/final.png",
            "grade": {"type": grader_type, "status": grade_status, "score": score,
                      "reason": "Example reason", "evidence": []}, "usage": {}}


def test_report_preserves_execution_and_grading_as_separate_outcomes(tmp_path):
    run = write_run(tmp_path / "run", [
        result("passed", execution="budget_exhausted"),
        result("failed", grade_status="failed", score=0.0),
        result("manual", grade_status="needs_review", score=None, grader_type="manual"),
        result("error", execution="timeout", grade_status="error", score=None),
    ])
    path = build_report(run)
    html = path.read_text()
    assert path == run / "report.html"
    assert "Grade: passed" in html
    assert "Execution: budget_exhausted" in html
    assert "1 budget exhausted" in html
    assert "Needs review" in html
    assert "Grader errors" in html
    assert "guest state is not verified" in html
    assert 'href="passed/trajectory.jsonl"' in html
    assert 'href="passed/final.png"' in html
    assert 'src="passed/final.png"' in html
    assert "<script" not in html


def test_report_escapes_all_untrusted_fields_and_omits_arbitrary_usage(tmp_path):
    record = result("bad")
    record["title"] = '<script>alert("title")</script>'
    record["error"] = '<img src=x onerror="alert(1)"> sk-secretABCDEFGHIJK'
    record["grade"]["reason"] = '<b onclick="alert(2)">reason</b>'
    record["grade"]["evidence"] = ['<iframe src="https://evil.invalid"></iframe>']
    record["usage"] = {"input_tokens": 12, "api_key": "should-never-appear", "raw_headers": "sensitive"}
    run = write_run(tmp_path / "run", [record], model='<img src=x onerror="evil()">')
    html = build_report(run).read_text()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<iframe" not in html
    assert "&lt;iframe" in html
    assert "<b onclick=" not in html
    assert "should-never-appear" not in html
    assert "raw_headers" not in html
    assert "sk-secretABCDEFGHIJK" not in html
    assert "[redacted key]" in html
    assert "input tokens: 12" in html


@pytest.mark.parametrize("target", ["../secret.png", "/etc/passwd", "javascript:alert(1)", "https://evil.invalid/image.png"])
def test_screenshots_cannot_link_outside_run_or_execute_urls(tmp_path, target):
    record = result()
    record["final_screenshot"] = target
    run = write_run(tmp_path / "run", [record])
    html = build_report(run).read_text()
    assert '<img src="javascript:' not in html
    assert '<img src="https:' not in html
    assert 'href="../secret.png"' not in html
    assert 'href="/etc/passwd"' not in html


def test_symlink_screenshot_cannot_escape_run(tmp_path):
    run = write_run(tmp_path / "run", [result()])
    (run / "example").mkdir()
    (run / "example" / "final.png").symlink_to(tmp_path / "outside.png")
    assert '<img src=' not in build_report(run).read_text()


def test_ocr_evidence_is_a_relative_portable_link(tmp_path):
    record = result()
    run = write_run(tmp_path / "run", [record])
    (run / "example").mkdir()
    evidence = run / "example" / "grader-ocr.txt"
    evidence.write_text("ready")
    record["grade"]["evidence"] = [str(evidence)]
    write_run(run, [record])
    html = build_report(run).read_text()
    assert 'href="example/grader-ocr.txt"' in html


def test_grader_evidence_basename_resolves_inside_task_folder(tmp_path):
    record = result()
    record["grade"]["evidence"] = ["grader-ocr.txt"]
    run = write_run(tmp_path / "run", [record])
    (run / "example").mkdir()
    (run / "example" / "grader-ocr.txt").write_text("ready")
    assert 'href="example/grader-ocr.txt"' in build_report(run).read_text()


def test_comparison_excludes_unreviewed_and_invalid_scores(tmp_path):
    records = [
        result("a", score=1.0),
        result("b", grade_status="failed", score=0.0),
        result("c", grade_status="needs_review", score=1.0, grader_type="manual"),
        result("d", execution="error", grade_status="error", score=1.0),
        result("e", score=True),
        result("f", score=float("nan")),
        result("g", score=2.0),
        result("h", score=0.5, grader_type="unknown"),
        result("i", score=0.5, grader_type="manual"),
    ]
    run = write_run(tmp_path / "run", records)
    compared = compare_results([run])
    model = compared["models"][0]
    assert model["tasks"] == 9
    assert model["scored_tasks"] == 3
    assert model["average_score"] == 0.5
    assert model["needs_review"] == 1
    assert model["errors"] == 1
    assert model["execution_errors"] == 1


def test_comparison_groups_provider_and_model_and_merges_runs(tmp_path):
    first = write_run(tmp_path / "first", [result()], provider="openai", model="astra")
    second = write_run(tmp_path / "second", [result(grade_status="failed", score=0.0)],
                       provider="openai", model="astra")
    other = write_run(tmp_path / "other", [result(grade_status="needs_review", score=None)],
                      provider="anthropic", model="opus")
    same_name = write_run(tmp_path / "same", [result()], provider="other", model="astra")
    compared = compare_results([first, second / "results.json", other, same_name])
    assert compared["runs"] == 4
    groups = {(entry["provider"], entry["model"]): entry for entry in compared["models"]}
    assert groups[("openai", "astra")]["runs"] == 2
    assert groups[("openai", "astra")]["average_score"] == 0.5
    assert groups[("anthropic", "opus")]["average_score"] is None
    assert len(groups) == 3


def test_invalid_results_envelope_has_a_clear_error(tmp_path):
    (tmp_path / "results.json").write_text('{"results": "invalid"}')
    with pytest.raises(ValueError, match="results list"):
        build_report(tmp_path)


def test_empty_report_and_comparison_are_valid(tmp_path):
    run = write_run(tmp_path / "run", [])
    assert "No tasks" in build_report(run).read_text()
    assert compare_results([]) == {"runs": 0, "models": []}


def configured_run(directory, records, *, baseline="baseline.qcow2", baseline_hash="a" * 64,
                   options=None):
    for record in records:
        record.setdefault("budget", {"max_steps": 40, "timeout_seconds": 180})
        record.setdefault("task_fingerprint", "f" * 64)
    run = write_run(directory, records)
    path = run / "results.json"
    envelope = json.loads(path.read_text())
    envelope["config"] = {
        "iso": "temple.iso", "baseline": baseline, "baseline_sha256": baseline_hash,
        "baseline_identity": {"iso_sha256": "b" * 64, "qemu_version": "QEMU test", "memory_mb": 512},
        "memory_mb": 512, "model_options": options or {"max_output_tokens": 1000, "history_steps": 4},
    }
    path.write_text(json.dumps(envelope))
    return run


def test_comparison_uses_baseline_content_hash_and_records_effective_budgets(tmp_path):
    first = configured_run(tmp_path / "first", [result()], baseline="one.qcow2")
    second = configured_run(tmp_path / "second", [result()], baseline="copied.qcow2")
    compared = compare_results([first, second])
    assert compared["comparable"] is True
    assert compared["run_metadata"][0]["task_ids"] == ["example"]
    assert compared["run_metadata"][0]["budgets"]["example"] == {"max_steps": 40, "timeout_seconds": 180}
    assert compared["run_metadata"][0]["config"]["baseline"] == "one.qcow2"


@pytest.mark.parametrize("difference, expected_reason", [
    ("task", "task sets"), ("budget", "task budgets"), ("baseline", "baseline identities"),
    ("coverage", "score denominators"), ("fingerprint", "task definitions"),
    ("options", "model request budgets"),
])
def test_mismatched_experiments_are_explicitly_not_comparable(tmp_path, difference, expected_reason):
    first = configured_run(tmp_path / "first", [result()])
    changed = result()
    kwargs = {}
    if difference == "task":
        changed["task_id"] = "different"
    elif difference == "budget":
        changed["budget"] = {"max_steps": 10, "timeout_seconds": 180}
    elif difference == "baseline":
        kwargs["baseline_hash"] = "c" * 64
    elif difference == "coverage":
        changed["grade"] = {"type": "manual", "status": "needs_review", "score": None}
    elif difference == "fingerprint":
        changed["task_fingerprint"] = "d" * 64
    elif difference == "options":
        kwargs["options"] = {"max_output_tokens": 2000, "history_steps": 4}
    second = configured_run(tmp_path / "second", [changed], **kwargs)
    compared = compare_results([first, second])
    assert compared["comparable"] is False
    assert expected_reason in compared["comparison_reason"]
    assert "descriptive only" in compared["comparison_reason"]


def test_old_artifact_task_config_supplies_budgets_and_fingerprint(tmp_path):
    run = write_run(tmp_path / "run", [result()])
    (run / "example").mkdir()
    (run / "example" / "task.json").write_text(json.dumps({
        "id": "example", "prompt": "Do a thing", "max_steps": 25, "timeout_seconds": 100,
        "source": "/private/source.yaml",
    }))
    metadata = compare_results([run])["run_metadata"][0]
    assert metadata["budgets"]["example"] == {"max_steps": 25, "timeout_seconds": 100}
    assert len(metadata["task_fingerprints"]["example"]) == 64
    assert "/private/source.yaml" not in json.dumps(metadata)


def test_model_efficiency_uses_recorded_tokens_steps_and_elapsed(tmp_path):
    first, second = result("first"), result("second", grade_status="error", score=None)
    first.update(steps=2, elapsed_seconds=10, setup_seconds=4,
                 usage={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110, "key": "secret"})
    second.update(steps=4, elapsed_seconds=20, setup_seconds=6,
                  usage={"input_tokens": 200, "output_tokens": 20})
    run = write_run(tmp_path / "run", [first, second])
    model = compare_results([run])["models"][0]
    assert model["total_input_tokens"] == 300
    assert model["total_output_tokens"] == 30
    assert model["total_tokens"] == 330
    assert model["average_steps"] == 3
    assert model["average_elapsed_seconds"] == 15
    assert model["average_setup_seconds"] == 5
    assert "secret" not in json.dumps(model)
