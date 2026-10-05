import json
from pathlib import Path
import shutil
import subprocess

import pytest


APP = Path(__file__).resolve().parents[1] / "site/app.js"


def render_rejection(frame):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the recording viewer")
    probe = r"""
const fs = require('fs');
const vm = require('vm');
const elements = {};
function element(tag) {
  let id;
  const node = {tagName: tag, textContent: '', hidden: false, open: false,
    children: [], append(...children) { this.children.push(...children); },
    after(node) { this.nextElementSibling = node; }, addEventListener() {}, setAttribute() {}};
  Object.defineProperty(node, 'id', {get() { return id; }, set(value) { id = value; elements[value] = node; }});
  Object.defineProperty(node, 'innerHTML', {set() { throw new Error('Raw input must never become HTML'); }});
  return node;
}
for (const id of ['timeline', 'previous', 'next', 'play', 'step-actions']) {
  const node = element('div'); node.id = id;
}
elements['step-actions'].parentElement = element('details');
elements['step-actions'].textContent = 'Move pointer: (10, 20)';
const context = {
  document: {documentElement: {dataset: {}}, getElementById(id) { return elements[id] || null; },
    createElement: element, addEventListener() {}},
  fetch() { return new Promise(() => {}); },
  JSON: Object.assign(Object.create(JSON), {parse() { throw new Error('Do not parse requested arguments'); }})
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const frame = JSON.parse(process.argv[2]);
context.showRejectedInput(frame);
const panel = elements['rejected-input'];
const result = {hidden: panel.hidden, open: panel.open, heading: panel.children[0].textContent,
  text: elements['step-rejected'].textContent, executed: elements['step-actions'].textContent,
  insertedAfterExecuted: elements['step-actions'].parentElement.nextElementSibling === panel};
context.showRejectedInput({});
result.cleared = panel.hidden && !panel.open && elements['step-rejected'].textContent === '';
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run([node, "-e", probe, str(APP), json.dumps(frame)],
                               check=True, capture_output=True, text=True, timeout=10)
    return json.loads(completed.stdout)


def test_rejected_input_keeps_malformed_original_arguments_and_executed_input_separate():
    raw = '{"type":"scroll",\n"scroll_y":<img src=x onerror=alert(1)>'
    rendered = render_rejection({
        "requested_calls": [
            {"call_id": "different-call", "arguments": "DO NOT DISPLAY"},
            {"call_id": "rejected-call", "arguments": raw},
        ],
        "input_errors": [{"call_id": "rejected-call", "arguments": "NORMALIZED FALLBACK",
                          "error": {"message": "Malformed computer arguments."}, "input_executed": False}],
    })
    assert rendered["heading"] == "Rejected computer input — not executed"
    assert "Call ID: rejected-call" in rendered["text"]
    assert "Error: Malformed computer arguments." in rendered["text"]
    assert raw in rendered["text"]
    assert "DO NOT DISPLAY" not in rendered["text"]
    assert "NORMALIZED FALLBACK" not in rendered["text"]
    assert rendered["executed"] == "Move pointer: (10, 20)"
    assert rendered["insertedAfterExecuted"] and rendered["open"] and not rendered["hidden"]
    assert rendered["cleared"]


def test_rejected_input_falls_back_without_matching_call_and_does_not_match_missing_ids():
    rendered = render_rejection({
        "requested_calls": [{"call_id": None, "arguments": "UNRELATED UNKNOWN CALL"}],
        "input_errors": [
            {"call_id": "oversized-scroll", "arguments": '{"scroll_y":5000}',
             "error": {"message": "Use a shorter distance."}},
            {"call_id": None, "arguments": "raw unknown request", "error": {"message": "Invalid input."}},
        ],
    })
    assert "Call ID: oversized-scroll" in rendered["text"]
    assert '{"scroll_y":5000}' in rendered["text"]
    assert "Use a shorter distance." in rendered["text"]
    assert "Call ID: Not recorded" in rendered["text"]
    assert "raw unknown request" in rendered["text"]
    assert "UNRELATED UNKNOWN CALL" not in rendered["text"]
    assert rendered["cleared"]


def test_legacy_frame_hides_rejection_details_without_changing_executed_input():
    rendered = render_rejection({"actions": [{"kind": "move", "x": 10, "y": 20}]})
    assert rendered["hidden"] and not rendered["open"]
    assert rendered["text"] == ""
    assert rendered["executed"] == "Move pointer: (10, 20)"
