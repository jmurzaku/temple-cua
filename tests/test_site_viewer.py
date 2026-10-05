import json
from pathlib import Path
import shutil
import subprocess

import pytest


APP = Path(__file__).resolve().parents[1] / "site/app.js"


def probe_viewer(value, probe, extra_ids=()):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the recording viewer")
    bootstrap = r"""
const fs = require('fs');
const vm = require('vm');
const elements = {};
function element(tag) {
  let id;
  const node = {tagName: tag, textContent: '', hidden: false, open: false,
    children: [], append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = [...children]; },
    after(node) { this.nextElementSibling = node; }, addEventListener() {}, setAttribute() {}};
  Object.defineProperty(node, 'id', {get() { return id; }, set(value) { id = value; elements[value] = node; }});
  Object.defineProperty(node, 'innerHTML', {set() { throw new Error('Raw input must never become HTML'); }});
  return node;
}
for (const id of ['timeline', 'previous', 'next', 'play', 'step-actions', ...JSON.parse(process.argv[3])]) {
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
const input = JSON.parse(process.argv[2]);
"""
    completed = subprocess.run([node, "-e", bootstrap + probe, str(APP),
                                json.dumps(value), json.dumps(extra_ids)],
                               check=True, capture_output=True, text=True, timeout=10)
    return json.loads(completed.stdout)


def render_rejection(frame):
    probe = r"""
context.showRejectedInput(input);
const panel = elements['rejected-input'];
const result = {hidden: panel.hidden, open: panel.open, heading: panel.children[0].textContent,
  text: elements['step-rejected'].textContent, executed: elements['step-actions'].textContent,
  insertedAfterExecuted: elements['step-actions'].parentElement.nextElementSibling === panel};
context.showRejectedInput({});
result.cleared = panel.hidden && !panel.open && elements['step-rejected'].textContent === '';
process.stdout.write(JSON.stringify(result));
"""
    return probe_viewer(frame, probe)


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


def render_host_evaluation(task):
    ids = ["host-evaluation", "host-evaluation-score", "host-evaluation-reason",
           "host-evaluation-checks-title", "host-evaluation-checks",
           "host-evaluation-evidence", "host-evaluation-artifacts",
           "host-evaluation-limitations", "host-evaluation-limits"]
    probe = r"""
context.showHostEvaluation(input);
const result = {
  hidden: elements['host-evaluation'].hidden,
  score: elements['host-evaluation-score'].textContent,
  reason: elements['host-evaluation-reason'].textContent,
  title: elements['host-evaluation-checks-title'].textContent,
  cases: elements['host-evaluation-checks'].children.map(item => item.children.map(node => node.textContent)),
  evidence: elements['host-evaluation-artifacts'].children.map(item => {
    const link = item.children[0]; return {text: link.textContent, href: link.href};
  }),
  limits: elements['host-evaluation-limits'].children.map(item => item.textContent),
  executed: elements['step-actions'].textContent,
  rawGrade: input.result.grade,
  row: context.taskStatusText(input)
};
context.showHostEvaluation({grader: {type: 'manual'}, result: {grade: {type: 'manual'}}});
result.hiddenForManual = elements['host-evaluation'].hidden;
context.showHostEvaluation({grader: {type: 'uart'}, result: null});
result.hiddenForUnrun = elements['host-evaluation'].hidden;
process.stdout.write(JSON.stringify(result));
"""
    return probe_viewer(task, probe, ids)


def test_uart_host_reward_evidence_and_limits_stay_separate_from_model_input():
    grade = {
        "type": "uart", "status": "failed", "score": 0.3,
        "passed_cases": 3, "total_cases": 10, "reason": "Three host checks passed.",
        "cases": [{"name": f"case_{index}", "status": "passed" if index < 3 else "failed",
                   "score": 0.1 if index < 3 else 0, "reason": "Literal <img src=x> reason."}
                  for index in range(10)],
        "limitations": ["Behavior alone does not prove interrupt-only code."],
    }
    task = {"grader": {"type": "uart"}, "result": {"status": "budget_exhausted", "grade": grade},
            "evaluation_artifacts": {"evaluation.json": "assets/evaluations/uart_irq_service/evaluation.json",
                                     "evaluation-stop-1.png": "assets/evaluations/uart_irq_service/evaluation-stop-1.png"}}
    rendered = render_host_evaluation(task)
    assert rendered["score"] == "Host score: 0.3 / 1 · failed"
    assert rendered["row"] == "Host: failed · 0.3 / 1"
    assert rendered["title"] == "Checks (3 / 10 passed)"
    assert len(rendered["cases"]) == 10
    assert "reward 0.1" in rendered["cases"][0][0]
    assert "reward 0" in rendered["cases"][9][0]
    assert rendered["cases"][0][1] == "Literal <img src=x> reason."
    assert rendered["evidence"][0] == {
        "text": "Evaluation JSON", "href": "assets/evaluations/uart_irq_service/evaluation.json",
    }
    assert rendered["limits"] == grade["limitations"]
    assert rendered["executed"] == "Move pointer: (10, 20)"
    assert rendered["rawGrade"] == grade
    assert not rendered["hidden"] and rendered["hiddenForManual"] and rendered["hiddenForUnrun"]


def test_uart_instrumentation_error_is_unverified_instead_of_zero_reward():
    grade = {"type": "uart", "status": "error", "score": None,
             "passed_cases": 0, "total_cases": 10, "reason": "Host oracle timed out.",
             "cases": [{"name": "irq4_mask_dependency", "status": "error", "score": None,
                        "reason": "No verified mask snapshot."}]}
    rendered = render_host_evaluation({"grader": {"type": "uart"}, "result": {"grade": grade}})
    assert rendered["score"] == "Host score: Unverified · error"
    assert rendered["row"] == "Host: error · Unverified"
    assert "reward unverified" in rendered["cases"][0][0]
    assert rendered["rawGrade"]["score"] is None


@pytest.mark.parametrize("score,status,expected", [
    (0, "failed", "1 / 2 passed"),
    (0.3, "failed", "1 / 2 passed"),
    (1, "passed", "2 / 2 passed"),
    (None, "error", "1 / 1 passed · 1 unverified"),
])
def test_automatic_summary_counts_host_grades_without_turning_oracle_errors_into_failures(score, status, expected):
    tasks = [
        {"grader": {"type": "ocr"}, "result": {"grade": {"status": "passed", "score": 1}}},
        {"grader": {"type": "uart"}, "result": {"grade": {"type": "uart", "status": status, "score": score}}},
        {"grader": {"type": "manual"}, "result": {"grade": {"status": "needs_review", "score": None}}},
    ]
    probe = "process.stdout.write(JSON.stringify(context.automaticGradeSummary(input)));"
    assert probe_viewer(tasks, probe) == expected


@pytest.mark.parametrize("grade,expected", [
    ({"type": "ocr", "status": "passed"}, "Automatic check: passed."),
    ({"type": "manual", "status": "needs_review"}, "Manual grade: needs review."),
])
def test_ordinary_outcome_keeps_existing_total_time_wording(grade, expected):
    task = {"grader": {"type": grade["type"]}, "result": {
        "status": "completed", "grade": grade, "steps": 3, "elapsed_seconds": 16.923,
        "policy_seconds": 10, "evaluation_seconds": 1,
    }}
    probe = "process.stdout.write(JSON.stringify(context.taskOutcomeText(input)));"
    assert probe_viewer(task, probe) == f"Run status: completed. {expected} 3 model turns, 16.9 seconds including reset."


def test_uart_outcome_labels_model_host_and_total_time_separately():
    task = {"grader": {"type": "uart"}, "result": {
        "status": "budget_exhausted", "grade": {"type": "uart", "status": "failed", "score": 0.3},
        "steps": 80, "policy_seconds": 899.25, "evaluation_seconds": 3.24, "elapsed_seconds": 930.5,
    }}
    probe = "process.stdout.write(JSON.stringify(context.taskOutcomeText(input)));"
    assert probe_viewer(task, probe) == (
        "Run status: budget exhausted. Host grade: failed. Score: 0.3 / 1. 80 model turns. "
        "Model episode: 899.3 seconds; host evaluation: 3.2 seconds; total: 930.5 seconds including reset."
    )


def test_uart_outcome_does_not_infer_missing_phase_times_from_total():
    task = {"grader": {"type": "uart"}, "result": {
        "status": "error", "grade": {"type": "uart", "status": "error", "score": None},
        "steps": 0, "elapsed_seconds": 12.5,
    }}
    probe = "process.stdout.write(JSON.stringify(context.taskOutcomeText(input)));"
    text = probe_viewer(task, probe)
    assert "Score: Unverified" in text
    assert "Model episode: not recorded; host evaluation: not recorded; total: 12.5 seconds" in text


def test_trajectory_summary_includes_uart_annotation_separately_from_host_grade():
    tasks = [
        {"grader": {"type": "manual"}, "result": {"grade": {"status": "needs_review"}},
         "review": {"status": "passed"}},
        {"grader": {"type": "manual"}, "result": {"grade": {"status": "needs_review"}},
         "review": {"status": "incomplete"}},
        {"grader": {"type": "uart"}, "result": {"grade": {"type": "uart", "status": "failed", "score": 0}},
         "review": {"status": "incomplete"}},
    ]
    probe = "process.stdout.write(JSON.stringify(context.trajectoryReviewSummary(input)));"
    assert probe_viewer(tasks, probe) == {"count": 3, "text": "1 passed, 2 incomplete"}
