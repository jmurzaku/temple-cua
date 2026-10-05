from pathlib import Path
import subprocess

from PIL import Image
import pytest

from temple_cua.grading import grade


@pytest.fixture
def screenshot(tmp_path):
    path = tmp_path / "screen.png"
    Image.new("RGB", (640, 480), "white").save(path)
    return path


def mock_ocr(monkeypatch, output):
    monkeypatch.setattr("temple_cua.grading.subprocess.run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, output, ""))


def test_ocr_requires_all_literals_and_one_alternative(monkeypatch, screenshot, tmp_path):
    mock_ocr(monkeypatch, "Hello\n   TempleOS\n42\nReady")
    result = grade({"type": "ocr", "all": ["hello TempleOS", "42"],
                    "any": ["Finished", "ready"]}, screenshot, tmp_path / "artifacts")
    assert result["status"] == "passed"
    assert result["score"] == 1.0
    assert "guest state is not verified" in result["reason"]
    assert (tmp_path / "artifacts" / "grader-ocr.txt").read_text() == "Hello\n   TempleOS\n42\nReady"


@pytest.mark.parametrize("spec", [
    {"type": "ocr", "all": ["absent"]},
    {"type": "ocr", "all": ["ready"], "any": ["absent", "elsewhere"]},
    {"type": "ocr", "all": ["READY"], "case_sensitive": True},
])
def test_ocr_mismatch_fails(monkeypatch, screenshot, tmp_path, spec):
    mock_ocr(monkeypatch, "ready")
    result = grade(spec, screenshot, tmp_path / "artifacts")
    assert result["status"] == "failed"
    assert result["score"] == 0.0


@pytest.mark.parametrize("spec", [
    {"type": "ocr"},
    {"type": "ocr", "all": []},
    {"type": "ocr", "all": [], "any": ["ready"]},
    {"type": "ocr", "all": [""]},
    {"type": "ocr", "all": [" \n "]},
    {"type": "ocr", "all": "ready"},
    {"type": "ocr", "all": ["ready"], "any": [2]},
    {"type": "ocr", "all": ["ready"], "case_sensitive": "false"},
])
def test_invalid_ocr_config_cannot_pass(monkeypatch, screenshot, tmp_path, spec):
    def should_not_run(*args, **kwargs):
        pytest.fail("Invalid config should be rejected before invoking OCR")
    monkeypatch.setattr("temple_cua.grading.subprocess.run", should_not_run)
    result = grade(spec, screenshot, tmp_path / "artifacts")
    assert result["status"] == "error"
    assert result["score"] is None


def test_empty_ocr_output_is_an_error(monkeypatch, screenshot, tmp_path):
    mock_ocr(monkeypatch, " \n\t")
    result = grade({"type": "ocr", "all": ["ready"]}, screenshot, tmp_path / "artifacts")
    assert result["status"] == "error"
    assert result["score"] is None
    assert (tmp_path / "artifacts" / "grader-ocr.txt").exists()


def test_crop_preserves_bitmap_geometry(monkeypatch, screenshot, tmp_path):
    def inspect_input(command, **kwargs):
        with Image.open(command[1]) as image:
            assert image.size == (300, 150)
        assert command[-2:] == ["--psm", "6"]
        return subprocess.CompletedProcess(command, 0, "READY", "")
    monkeypatch.setattr("temple_cua.grading.subprocess.run", inspect_input)
    result = grade({"grader": {"type": "ocr", "all": ["ready"], "crop": [10, 20, 100, 50]}},
                   screenshot, tmp_path / "artifacts")
    assert result["status"] == "passed"


@pytest.mark.parametrize("crop", [[-1, 0, 10, 10], [0, 0, 0, 10], [0, 0, 641, 480],
                                 [0, 0, 1.5, 2], [0, 0, 10], [True, 0, 10, 10]])
def test_invalid_or_outside_crop_is_an_error(monkeypatch, screenshot, tmp_path, crop):
    mock_ocr(monkeypatch, "ready")
    result = grade({"type": "ocr", "all": ["ready"], "crop": crop}, screenshot, tmp_path / "artifacts")
    assert result["status"] == "error"
    assert result["score"] is None


def test_missing_tesseract_is_actionable(monkeypatch, screenshot, tmp_path):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "tesseract")
    monkeypatch.setattr("temple_cua.grading.subprocess.run", unavailable)
    result = grade({"type": "ocr", "all": ["ready"]}, screenshot, tmp_path / "artifacts")
    assert result["status"] == "error"
    assert "install tesseract-ocr" in result["reason"]


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("tesseract", 30),
                                     subprocess.CalledProcessError(1, "tesseract")])
def test_ocr_process_failure_has_no_score(monkeypatch, screenshot, tmp_path, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr("temple_cua.grading.subprocess.run", fail)
    result = grade({"type": "ocr", "all": ["ready"]}, screenshot, tmp_path / "artifacts")
    assert result["status"] == "error"
    assert result["score"] is None


def test_manual_grading_leaves_review_for_a_human(tmp_path):
    result = grade({"type": "manual", "rubric": ["Read the plot.", "Verify axes."]},
                   Path("unused.png"), tmp_path)
    assert result["status"] == "needs_review"
    assert result["score"] is None
    assert result["reason"] == "Read the plot. Verify axes."


def test_unknown_grader_is_an_error(tmp_path):
    assert grade({"type": "mystery"}, Path("unused.png"), tmp_path)["status"] == "error"


def test_real_native_templeos_output_uses_exact_font_decoder(monkeypatch, tmp_path):
    def should_not_run(*args, **kwargs):
        pytest.fail("Native stock text must not depend on Tesseract guesses")
    monkeypatch.setattr("temple_cua.grading.subprocess.run", should_not_run)
    screenshot = Path(__file__).parent / "fixtures" / "temple-arithmetic.png"
    result = grade({"type": "ocr", "all": ["BENCH_ARITH=391"]}, screenshot, tmp_path)
    assert result["status"] == "passed"
    assert result["engine"] == "templeos-bitmap"
    assert "BENCH_ARITH=391" in (tmp_path / "grader-ocr.txt").read_text()


def test_native_stock_text_mismatch_does_not_fall_back_to_fuzzy_ocr(monkeypatch, tmp_path):
    def should_not_run(*args, **kwargs):
        pytest.fail("Recognized stock glyphs must not be overridden by fuzzy OCR")
    monkeypatch.setattr("temple_cua.grading.subprocess.run", should_not_run)
    screenshot = Path(__file__).parent / "fixtures" / "temple-arithmetic.png"
    result = grade({"type": "ocr", "all": ["BEHCH_ARITH=391"]}, screenshot, tmp_path)
    assert result["status"] == "failed"
    assert result["engine"] == "templeos-bitmap"


def test_exact_output_line_rejects_marker_in_an_echoed_command(monkeypatch, screenshot, tmp_path):
    mock_ocr(monkeypatch, 'T:/Home>Print("BENCH_ARITH=391\\n");\nT:/Home>')
    result = grade({"type": "ocr", "all": ["BENCH_ARITH=391"], "match_mode": "line"},
                   screenshot, tmp_path)
    assert result["status"] == "failed"


def test_exact_output_line_accepts_visible_output_but_does_not_verify_its_origin(monkeypatch, screenshot, tmp_path):
    mock_ocr(monkeypatch, 'T:/Home>Print("BENCH_ARITH=%d\\n", 391);\nBENCH_ARITH=391\nT:/Home>')
    result = grade({"type": "ocr", "all": ["BENCH_ARITH=391"], "match_mode": "line"},
                   screenshot, tmp_path)
    assert result["status"] == "passed"
    assert "printed constants" in result["reason"]
    assert "guest state is not verified" in result["reason"]


@pytest.mark.parametrize("output, status", [
    ("Directory of T:/Demo/Graphics\n03/14 03:33 01C6 Blot�HC�Z\nT:/Demo/Graphics>", "passed"),
    ('T:/Home>Print("Directory of T:/Demo/Graphics Blot");\nT:/Home>', "failed"),
    ("Directory of T:/Demo/Graphics\n03/14 03:33 01C6 Blot�HC�Z\nT:/Home>", "failed"),
    ("Directory of T:/Demo/Graphics\nBlot\nT:/Demo/Graphics>", "failed"),
])
def test_directory_requires_listing_structure_and_final_path_prompt(monkeypatch, screenshot, tmp_path, output, status):
    mock_ocr(monkeypatch, output)
    result = grade({
        "type": "ocr", "all": ["/Demo/Graphics", "Blot"],
        "line_patterns": [r"Directory of [A-Za-z]:/Demo/Graphics",
                          r"\d{2}/\d{2} \d{2}:\d{2} [0-9A-Fa-f]+ Blot[.�]HC(?:[.�]Z)?"],
        "last_line_pattern": r"[A-Za-z]:/Demo/Graphics>(?:_|�)?",
    }, screenshot, tmp_path)
    assert result["status"] == status


@pytest.mark.parametrize("extra", [{"match_mode": "unknown"}, {"line_patterns": ["["]},
                                   {"last_line_pattern": ""}])
def test_invalid_line_matching_settings_are_errors(monkeypatch, screenshot, tmp_path, extra):
    mock_ocr(monkeypatch, "BENCH_ARITH=391")
    result = grade({"type": "ocr", "all": ["BENCH_ARITH=391"], **extra}, screenshot, tmp_path)
    assert result["status"] == "error"


@pytest.mark.parametrize("task_id, fixture_name", [("arithmetic", "temple-arithmetic.png"),
                                                  ("directory_navigation", "temple-directory.png")])
def test_simple_suite_line_checks_pass_real_reference_frames(tmp_path, task_id, fixture_name):
    from temple_cua.tasks import load_tasks
    root = Path(__file__).resolve().parents[1]
    task = next(task for task in load_tasks(root / "tasks") if task.id == task_id)
    screenshot = root / "tests" / "fixtures" / fixture_name
    result = grade(task.grader, screenshot, tmp_path / task.id)
    assert result["status"] == "passed", (task.id, result["reason"])
