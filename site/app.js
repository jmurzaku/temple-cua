"use strict";

const byId = id => document.getElementById(id);
const setText = (id, text) => {
  const element = byId(id);
  if (element) element.textContent = text;
};
const setHidden = (id, hidden) => {
  const element = byId(id);
  if (element) element.hidden = hidden;
};
let dataset;
let selectedTask;
let playbackTimer = null;

function stopPlayback() {
  window.clearInterval(playbackTimer);
  playbackTimer = null;
  byId("play").textContent = "Play";
  byId("play").setAttribute("aria-label", "Play recorded steps");
}

function actionText(actions) {
  if (!actions || !actions.length) return "No executed input at this step.";
  return actions.map(action => {
    if (action.kind === "type") return `Type:\n${action.text}`;
    if (action.kind === "key") return `Press: ${action.keys.join(" + ")}`;
    if (action.kind === "move") return `Move pointer: (${action.x}, ${action.y})`;
    if (action.kind === "click") return `Click: ${action.button || "left"} at (${action.x}, ${action.y})`;
    return JSON.stringify(action, null, 2);
  }).join("\n\n");
}

function rejectedInputText(frame) {
  const errors = Array.isArray(frame.input_errors) ? frame.input_errors : [];
  const requested = Array.isArray(frame.requested_calls) ? frame.requested_calls : [];
  return errors.filter(error => error && typeof error === "object").map(error => {
    const call = error.call_id == null ? undefined : requested.find(item => item?.call_id === error.call_id);
    const argumentsValue = call?.arguments ?? error.arguments;
    const argumentsText = typeof argumentsValue === "string" ? argumentsValue : argumentsValue == null ? "Not recorded." : JSON.stringify(argumentsValue, null, 2);
    const message = typeof error.error?.message === "string" ? error.error.message : "Error message not recorded.";
    return `Call ID: ${error.call_id ?? "Not recorded"}\nError: ${message}\nRequested arguments:\n${argumentsText}`;
  }).join("\n\n");
}

function showRejectedInput(frame) {
  let panel = byId("rejected-input");
  if (!panel) {
    const input = byId("step-actions");
    if (!input) return;
    panel = document.createElement("details");
    panel.id = "rejected-input";
    panel.className = "action-details";
    const summary = document.createElement("summary");
    summary.textContent = "Rejected computer input — not executed";
    const text = document.createElement("pre");
    text.id = "step-rejected";
    panel.append(summary, text);
    input.parentElement.after(panel);
  }
  const text = rejectedInputText(frame);
  panel.hidden = !text;
  panel.open = Boolean(text);
  setText("step-rejected", text);
}

function showFrame(index) {
  if (!selectedTask?.result || !selectedTask.frames.length) return;
  const frames = selectedTask.frames;
  const current = Math.max(0, Math.min(frames.length - 1, Number(index)));
  const frame = frames[current];
  byId("timeline").value = String(current);
  byId("timeline").setAttribute("aria-valuetext", `Step ${frame.step} of ${selectedTask.result.steps}`);
  byId("recording-step").textContent = `Step ${frame.step} / ${selectedTask.result.steps}`;
  byId("frame").src = frame.src;
  byId("frame").alt = `TempleOS: ${selectedTask.title}, recorded step ${frame.step}`;
  const note = (frame.note || "").replace(/^```[a-z]*\s*$/gm, "").replace(/`([^`]+)`/g, "$1").trim();
  byId("step-note").textContent = note ? (current === 0 ? note : `Agent note: ${note}`) : "Screen after the input below.";
  byId("step-actions").textContent = actionText(frame.actions);
  showRejectedInput(frame);
  byId("previous").disabled = current === 0;
  byId("next").disabled = current === frames.length - 1;
}

function showPrompt(prompt) {
  const target = byId("task-prompt");
  target.replaceChildren();
  const formatted = prompt.includes("```") ? prompt : prompt.replace(/^U0\s+\w+\([^\n]+\)\s*\{[\s\S]*?^\}/gm, code => "```holyc\n" + code + "\n```");
  const sections = formatted.trim().split(/```[^\n]*\n([\s\S]*?)```/g);
  sections.forEach((section, index) => {
    if (!section.trim()) return;
    if (index % 2) {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = section.trimEnd();
      pre.append(code);
      target.append(pre);
    } else {
      const paragraph = document.createElement("p");
      paragraph.textContent = section.trim();
      target.append(paragraph);
    }
  });
}

function showCriteria(grader = {}, targetId = "completion-criteria") {
  const target = byId(targetId);
  if (!target) return;
  target.replaceChildren();
  const paragraph = text => {
    const node = document.createElement("p");
    node.textContent = text;
    target.append(node);
  };
  const expected = (label, values) => {
    if (!Array.isArray(values) || !values.length) return;
    paragraph(label);
    const pre = document.createElement("pre");
    pre.textContent = values.join("\n");
    target.append(pre);
  };
  if (grader.type === "manual" || grader.type === "uart") {
    paragraph(grader.rubric?.trim() || (grader.type === "uart" ? "Host serial, interrupt-mask, and cleanup checks determine the numeric reward." : "Review the screenshot and trajectory against the task prompt."));
  } else if (grader.type === "ocr") {
    expected("Required visible text:", grader.all);
    expected("At least one of:", grader.any);
    expected("Required line patterns:", grader.line_patterns);
    if (grader.last_line_pattern) expected("Final line pattern:", [grader.last_line_pattern]);
    paragraph("These checks inspect visible text, not hidden guest state.");
  } else {
    paragraph("See the task YAML for completion criteria.");
  }
}

function runMetadata(task) {
  return task.run || dataset.run || {};
}

function isManualTask(task) {
  return task.grader?.type === "manual" || task.result?.grade?.type === "manual";
}

function isUARTTask(task) {
  return task.grader?.type === "uart" || task.result?.grade?.type === "uart";
}

function hostScoreText(grade) {
  return typeof grade?.score === "number" && Number.isFinite(grade.score) ? `${grade.score} / 1` : "Unverified";
}

function automaticGradeSummary(tasks) {
  const automatic = tasks.filter(task => task.result && !isManualTask(task));
  if (!automatic.length) return tasks.some(task => task.result) ? "None" : "Pending";
  const verified = automatic.filter(task => task.result.grade.status !== "error" && (!isUARTTask(task) || typeof task.result.grade.score === "number" && Number.isFinite(task.result.grade.score)));
  const passed = verified.filter(task => task.result.grade.status === "passed").length;
  const unverified = automatic.length - verified.length;
  return verified.length ? `${passed} / ${verified.length} passed${unverified ? ` · ${unverified} unverified` : ""}` : "Unverified";
}

function trajectoryReviewSummary(tasks) {
  const reviewed = tasks.filter(task => task.result && (isManualTask(task) || task.review));
  const counts = { passed: 0, failed: 0, incomplete: 0, pending: 0 };
  reviewed.forEach(task => { counts[task.review?.status || "pending"]++; });
  return { count: reviewed.length, text: Object.entries(counts).filter(([, number]) => number).map(([status, number]) => `${number} ${status}`).join(", ") || "—" };
}

function taskStatusText(task) {
  const result = task.result;
  if (!result) return "Not run";
  if (isUARTTask(task)) return `Host: ${result.grade.status} · ${hostScoreText(result.grade)}`;
  if (result.status === "error") return "Run error";
  if (isManualTask(task)) return task.review ? `Review: ${task.review.status}` : "Review pending";
  return `Automatic: ${result.grade.status.replaceAll("_", " ")}`;
}

function taskOutcomeText(task) {
  const result = task.result;
  if (!result) return "No model run recorded.";
  const grading = isUARTTask(task) ? `Host grade: ${result.grade.status}. Score: ${hostScoreText(result.grade)}.` : isManualTask(task) ? `Manual grade: ${result.grade.status.replaceAll("_", " ")}.` : `Automatic check: ${result.grade.status.replaceAll("_", " ")}.`;
  const prefix = `Run status: ${result.status.replaceAll("_", " ")}. ${grading}`;
  if (!isUARTTask(task)) return `${prefix} ${result.steps} model turns, ${Number(result.elapsed_seconds).toFixed(1)} seconds including reset.`;
  const duration = value => typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)} seconds` : "not recorded";
  return `${prefix} ${result.steps} model turns. Model episode: ${duration(result.policy_seconds)}; host evaluation: ${duration(result.evaluation_seconds)}; total: ${duration(result.elapsed_seconds)} including reset.`;
}

function showHostEvaluation(task) {
  const card = byId("host-evaluation");
  if (!card) return;
  card.hidden = !task.result || !isUARTTask(task);
  if (card.hidden) return;
  const grade = task.result.grade;
  setText("host-evaluation-score", `Host score: ${hostScoreText(grade)} · ${grade.status}`);
  setText("host-evaluation-reason", grade.reason || "");
  const cases = Array.isArray(grade.cases) ? grade.cases : [];
  setText("host-evaluation-checks-title", `Checks (${grade.passed_cases ?? 0} / ${grade.total_cases ?? cases.length} passed)`);
  const checks = byId("host-evaluation-checks");
  checks.replaceChildren();
  cases.forEach(check => {
    const item = document.createElement("li");
    const outcome = document.createElement("strong");
    const reward = typeof check.score === "number" && Number.isFinite(check.score) ? check.score : "unverified";
    outcome.textContent = `${check.name.replaceAll("_", " ")}: ${check.status} · reward ${reward}`;
    const reason = document.createElement("p");
    reason.className = "small muted";
    reason.textContent = check.reason || "";
    item.append(outcome, reason);
    checks.append(item);
  });
  const artifacts = Object.entries(task.evaluation_artifacts || {});
  setHidden("host-evaluation-evidence", !artifacts.length);
  const evidence = byId("host-evaluation-artifacts");
  evidence.replaceChildren();
  artifacts.forEach(([name, src]) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = src;
    link.textContent = name === "evaluation.json" ? "Evaluation JSON" : name;
    if (name.endsWith(".png")) { link.target = "_blank"; link.rel = "noopener"; }
    else link.download = "";
    item.append(link);
    evidence.append(item);
  });
  const limitations = Array.isArray(grade.limitations) ? grade.limitations : [];
  setHidden("host-evaluation-limitations", !limitations.length);
  const limits = byId("host-evaluation-limits");
  limits.replaceChildren();
  limitations.forEach(text => {
    const item = document.createElement("li");
    item.textContent = text;
    limits.append(item);
  });
}

function showReview(task) {
  const card = byId("task-review");
  if (!card) return;
  card.hidden = !task.review;
  if (!task.review) return;
  byId("review-outcome").textContent = `${task.review.status[0].toUpperCase() + task.review.status.slice(1)} · ${task.review.reviewer}`;
  byId("review-reason").textContent = task.review.reason;
  const evidence = byId("review-evidence");
  evidence.replaceChildren();
  task.review.evidence.forEach(item => {
    const node = document.createElement("li");
    node.textContent = item;
    evidence.append(node);
  });
  const limitations = Array.isArray(task.review.limitations) ? task.review.limitations.filter(item => typeof item === "string") : typeof task.review.limitations === "string" ? [task.review.limitations] : [];
  setHidden("review-limitations", !limitations.length);
  const limits = byId("review-limits");
  if (!limits) return;
  limits.replaceChildren();
  limitations.forEach(item => {
    const node = document.createElement("li");
    node.textContent = item;
    limits.append(node);
  });
}

function selectTask(id, moveFocus = false) {
  const task = dataset.tasks.find(candidate => candidate.id === id);
  if (!task) return;
  stopPlayback();
  selectedTask = task;
  byId("task-number").textContent = `Task ${String(dataset.tasks.indexOf(task) + 1).padStart(2, "0")} · ${difficultyLabel(task)}`;
  byId("task-title").textContent = task.title;
  showPrompt(task.prompt);
  byId("task-download").href = `assets/tasks/${task.id}.yaml`;
  const result = task.result;
  byId("recording").hidden = !result;
  byId("unrecorded-task").hidden = Boolean(result);
  byId("replay-layout").classList.toggle("unrecorded", !result);
  byId("task-limitation").hidden = !result;
  setHidden("task-provenance", !result);
  setHidden("task-run-details", !result);
  setHidden("task-criteria-details", !result || (!isManualTask(task) && !isUARTTask(task)));
  setHidden("task-review", true);
  showHostEvaluation(task);
  if (result) {
    byId("task-outcome").textContent = taskOutcomeText(task);
    byId("task-budget").textContent = `Limit: ${result.budget.max_steps} turns · ${result.budget.timeout_seconds} seconds.`;
    const metadata = runMetadata(task);
    const started = metadata.created_at ? new Date(metadata.created_at).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : "Start time not recorded";
    setText("task-provenance", `${metadata.model || "Model not recorded"} · cua-agent ${metadata.cua_version || "—"} · ${started}`);
    setText("task-run-config", JSON.stringify({ ...metadata, budget: result.budget }, null, 2));
    byId("task-limitation").textContent = isUARTTask(task) ? "Host tests measure serial replies, interrupt-mask dependence, and cleanup. Proving an interrupt-only implementation requires source review." : isManualTask(task) ? "A trajectory review does not change the raw grader result or assign an automatic score." : "The grader checks visible text. It does not verify the program’s implementation or hidden guest state.";
    if (isManualTask(task) || isUARTTask(task)) showCriteria(task.grader, "recorded-criteria");
    showReview(task);
    byId("timeline").max = String(task.frames.length - 1);
    byId("timeline").disabled = false;
    byId("play").disabled = task.frames.length < 2;
    showFrame(task.frames.length - 1);
  } else {
    showCriteria(task.grader);
    byId("task-outcome").textContent = "No model run recorded.";
    byId("task-budget").textContent = "";
    byId("timeline").disabled = true;
    byId("play").disabled = true;
    byId("previous").disabled = true;
    byId("next").disabled = true;
  }
  document.querySelectorAll("#task-rows tr").forEach(row => {
    const active = row.dataset.id === id;
    row.classList.toggle("selected", active);
    row.querySelector("button").setAttribute("aria-pressed", String(active));
  });
  if (moveFocus) {
    const detail = byId("task-detail");
    detail.focus({ preventScroll: true });
    detail.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }
}

function cell(text, className) {
  const node = document.createElement("td");
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function difficultyLabel(task) {
  const labels = { easy: "Trivial", trivial: "Trivial", medium: "Intermediate", intermediate: "Intermediate", hard: "Hard" };
  return labels[String(task.difficulty || "").toLowerCase()] || task.difficulty || task.category || "—";
}

async function loadTasks() {
  const dataUrl = document.documentElement.dataset.taskData || "assets/starter-data.json";
  const response = await fetch(dataUrl, { cache: "no-store" });
  if (!response.ok) throw new Error("Task data could not load.");
  dataset = await response.json();
  if (!Array.isArray(dataset.tasks) || !dataset.tasks.length) throw new Error("No tasks are available.");
  byId("task-count").textContent = `${dataset.tasks.length} tasks`;
  const count = ({ 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five" })[dataset.tasks.length] || String(dataset.tasks.length);
  byId("catalog-description").textContent = `${count} ${dataset.tasks.length === 1 ? "task" : "tasks"} in TempleOS. An agent sees screenshots, writes HolyC, and controls the mouse in a QEMU virtual machine.`;
  const rows = byId("task-rows");
  rows.replaceChildren();
  dataset.tasks.forEach((task, index) => {
    const row = document.createElement("tr");
    row.dataset.id = task.id;
    const first = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "task-selector";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-controls", "task-detail");
    const number = document.createElement("span");
    number.textContent = String(index + 1).padStart(2, "0");
    button.append(number, document.createTextNode(task.title));
    button.addEventListener("click", () => selectTask(task.id, true));
    first.append(button);
    row.append(first, cell(difficultyLabel(task), "task-difficulty"), cell(taskStatusText(task), "result-status"));
    rows.append(row);
  });
  const recordedTasks = dataset.tasks.filter(task => task.result);
  const models = [...new Set(recordedTasks.map(task => runMetadata(task).model).filter(Boolean))];
  const versions = [...new Set(recordedTasks.map(task => runMetadata(task).cua_version).filter(Boolean))];
  byId("run-model").textContent = models.length === 1 ? models[0] : models.length ? "Multiple models" : "—";
  byId("run-agent").textContent = versions.length === 1 ? `cua-agent ${versions[0]}` : versions.length ? "Multiple versions" : "—";
  byId("run-task").textContent = `${recordedTasks.length} / ${dataset.tasks.length}`;
  byId("run-score").textContent = automaticGradeSummary(dataset.tasks);
  const reviews = trajectoryReviewSummary(dataset.tasks);
  setHidden("review-summary-field", !reviews.count);
  byId("run-facts")?.classList.toggle("has-reviews", Boolean(reviews.count));
  setText("run-reviews", reviews.text);
  const unrecordedCount = dataset.tasks.length - recordedTasks.length;
  const sourceCount = new Set(recordedTasks.map(task => runMetadata(task).id || runMetadata(task).created_at)).size;
  byId("run-note").textContent = !recordedTasks.length ? "These tasks have no recorded model run." : `One featured attempt per task across ${sourceCount} ${sourceCount === 1 ? "run" : "runs"}.${unrecordedCount ? ` ${unrecordedCount} tasks have no recorded model run.` : ""}${reviews.count ? " Trajectory reviews are separate from automatic grading." : ""}`;
  const budgets = recordedTasks.map(task => task.result.budget);
  const sharedBudget = budgets.length && budgets.every(budget => budget.max_steps === budgets[0].max_steps && budget.timeout_seconds === budgets[0].timeout_seconds);
  const limits = !budgets.length ? "" : sharedBudget ? `The recorded limit is ${budgets[0].max_steps} model turns and ${budgets[0].timeout_seconds} seconds per task. ` : "The harness enforces the time and turn limits recorded for each task. ";
  const bridge = models.length && models.every(model => model.startsWith("openai/")) ? " through an OpenAI Responses compatibility bridge" : "";
  byId("run-protocol").textContent = `${limits}Cua’s ComputerAgent drives a TempleOS computer adapter${bridge}.`;
  selectTask(dataset.tasks[0].id);
}

loadTasks().catch(error => {
  console.error("TempleOSBench task viewer failed:", error);
  byId("task-rows").replaceChildren();
  const row = document.createElement("tr");
  const message = cell("Task data could not load.");
  message.colSpan = 3;
  row.append(message);
  byId("task-rows").append(row);
  byId("data-error").hidden = false;
  byId("data-error").textContent = "The recording viewer is unavailable. Download the results or recorded run below.";
  byId("task-prompt").textContent = "Task data could not load.";
  byId("task-outcome").textContent = "See the downloadable results below.";
  byId("step-note").textContent = "Recording unavailable.";
  byId("step-actions").textContent = "Recording unavailable.";
});

byId("timeline").addEventListener("input", event => { stopPlayback(); showFrame(event.target.value); });
byId("previous").addEventListener("click", () => { stopPlayback(); showFrame(Number(byId("timeline").value) - 1); });
byId("next").addEventListener("click", () => { stopPlayback(); showFrame(Number(byId("timeline").value) + 1); });
byId("play").addEventListener("click", () => {
  if (!selectedTask?.result || !selectedTask.frames.length) return;
  if (playbackTimer) return stopPlayback();
  if (Number(byId("timeline").value) === selectedTask.frames.length - 1) showFrame(0);
  byId("play").textContent = "Pause";
  byId("play").setAttribute("aria-label", "Pause recorded steps");
  playbackTimer = window.setInterval(() => {
    const next = Number(byId("timeline").value) + 1;
    if (next >= selectedTask.frames.length) return stopPlayback();
    showFrame(next);
    if (next === selectedTask.frames.length - 1) stopPlayback();
  }, 1200);
});
document.addEventListener("visibilitychange", () => { if (document.hidden) stopPlayback(); });
