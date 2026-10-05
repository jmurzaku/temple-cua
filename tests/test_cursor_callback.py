"""The callback example stays opt-in and its replay cannot silently reset it."""

import json
from collections import Counter
import os
from pathlib import Path

from PIL import Image
import pytest

from temple_cua.bitmap_text import read_bitmap_text
from temple_cua.grading import grade
from temple_cua.providers import ScriptedProvider
from temple_cua.runner import run_task
from temple_cua.tasks import load_task, load_tasks
from temple_cua.vm import VMConfig


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/tasks/cursor_callback.yaml"
REFERENCE = ROOT / "scripts/reference/cursor_callback.json"


def test_cursor_callback_is_opt_in_replayable_and_requires_manual_review(tmp_path):
    task = load_task(FIXTURE)
    assert task.id == "cursor_callback"
    assert task.id not in {item.id for item in load_tasks(ROOT / "tasks")}
    assert task.grader["type"] == "manual"
    # A replay claiming completion must not turn a human-review fixture into
    # a reward, even if there is no screenshot to review yet.
    result = grade(task.grader, tmp_path / "missing.png", tmp_path)
    assert result["status"] == "needs_review"
    assert result["score"] is None

    turns = json.loads(REFERENCE.read_text())
    provider = ScriptedProvider(REFERENCE)
    decisions = [provider.decide(task=task.prompt, screenshot=tmp_path / "missing.png",
                                history=[], step=step)
                 for step in range(1, len(turns) + 1)]
    actions = [action for decision in decisions for action in decision.actions]
    assert actions[-1].kind == "done"
    typed = [action.text for action in actions if action.kind == "type"]
    assert typed[0].startswith("U0 Cross(CTask *, CDC *dc)")
    assert typed[1:] == ["Fs->draw_it=&Cross;"]
    menu = next(index for index, action in enumerate(actions)
                if action.kind == "click" and action.button == "right")
    help_index = next(index for index, action in enumerate(actions)
                      if action.kind == "key" and action.keys == ["f1"])
    link = next(index for index, action in enumerate(actions)
                if action.kind == "click" and action.button == "left")
    assert menu < help_index < link
    before = [(action.x, action.y) for action in actions[:menu] if action.kind == "move"]
    after = [(action.x, action.y) for action in actions[link + 1:] if action.kind == "move"]
    assert len(set(before)) >= 2
    assert before == after


def _cross_center(path):
    with Image.open(path) as image:
        # The top status line can contain yellow text. Inspect the maximized
        # task's client area rather than mistaking its label for a cross arm.
        pixels = [(x, y) for y in range(16, image.height - 8) for x in range(8, image.width - 8)
                  if image.getpixel((x, y))[:3] == (255, 255, 87)]
    rows = Counter(y for x, y in pixels)
    columns = Counter(x for x, y in pixels)
    assert rows and columns, "No yellow drawing pixels in screenshot"
    x, y = max(columns, key=columns.get), max(rows, key=rows.get)
    # The 33px line can lose a pixel where the software cursor paints later.
    assert rows[y] >= 31 and columns[x] >= 31, "Yellow pixels do not form the live cross"
    return x, y


def test_cross_measurement_ignores_yellow_status_text(tmp_path):
    image = Image.new("RGB", (640, 480), "white")
    yellow = (255, 255, 87)
    for x in range(33):
        image.putpixel((x, 0), yellow)
    for x in range(172, 205):
        image.putpixel((x, 196), yellow)
    for y in range(180, 213):
        image.putpixel((188, y), yellow)
    image.putpixel((188, 180), (0, 0, 0))
    path = tmp_path / "status-and-cross.png"
    image.save(path)
    assert _cross_center(path) == (188, 196)


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("TEMPLE_CUA_CURSOR_CALLBACK_INTEGRATION") != "1",
                    reason="Opt-in: set TEMPLE_CUA_CURSOR_CALLBACK_INTEGRATION=1 for real QEMU")
def test_cursor_callback_remains_live_after_menu_and_document_link(tmp_path):
    config = VMConfig(
        iso=Path(os.environ.get("TEMPLE_CUA_ISO", str(ROOT / "assets/TempleOS.ISO"))),
        baseline=Path(os.environ.get("TEMPLE_CUA_BASELINE", str(ROOT / "assets/baseline.qcow2"))),
        qemu_binary=os.environ.get("TEMPLE_CUA_QEMU", "qemu-system-x86_64"),
        boot_wait=0.5,
    )
    artifacts = tmp_path / "cursor_callback"
    result = run_task(load_task(FIXTURE), ScriptedProvider(REFERENCE), config, artifacts)
    assert result["status"] == "completed", result
    assert result["grade"]["status"] == "needs_review"
    assert result["grade"]["score"] is None
    with Image.open(artifacts / "final.png") as image:
        assert "Command Line Overview" in read_bitmap_text(image)
    expected = ((3, (188, 196)), (4, (408, 316)),
                (8, (188, 196)), (9, (408, 316)))
    for step, center in expected:
        assert _cross_center(artifacts / f"{step:04d}.png") == center
