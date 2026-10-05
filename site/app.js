"use strict";

const byId = id => document.getElementById(id);
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
  if (!actions || !actions.length) return "No input at this step.";
  return actions.map(action => {
    if (action.kind === "type") return `Type:\n${action.text}`;
    if (action.kind === "key") return `Press: ${action.keys.join(" + ")}`;
    if (action.kind === "move") return `Move pointer: (${action.x}, ${action.y})`;
    if (action.kind === "click") return `Click: ${action.button || "left"} at (${action.x}, ${action.y})`;
    return JSON.stringify(action, null, 2);
  }).join("\n\n");
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
  byId("step-note").textContent = note || (current === 0 ? "Initial screen, before the agent acts." : "Screen after the input below.");
  byId("step-actions").textContent = actionText(frame.actions);
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

function showCriteria(grader = {}) {
  const target = byId("completion-criteria");
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
  if (grader.type === "manual") {
    paragraph(grader.rubric?.trim() || "Review the screenshot and trajectory against the task prompt.");
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

function selectTask(id, moveFocus = false) {
  const task = dataset.tasks.find(candidate => candidate.id === id);
  if (!task) return;
  stopPlayback();
  selectedTask = task;
  byId("task-number").textContent = `Task ${String(dataset.tasks.indexOf(task) + 1).padStart(2, "0")}`;
  byId("task-title").textContent = task.title;
  showPrompt(task.prompt);
  byId("task-download").href = `assets/tasks/${task.id}.yaml`;
  const result = task.result;
  byId("recording").hidden = !result;
  byId("unrecorded-task").hidden = Boolean(result);
  byId("replay-layout").classList.toggle("unrecorded", !result);
  byId("task-limitation").hidden = !result;
  if (result) {
    const status = result.status === "error" ? "Execution error." : result.grade.status === "passed" ? "Passed the visual check." : `Visual check: ${result.grade.status.replaceAll("_", " ")}.`;
    byId("task-outcome").textContent = `${status} ${result.steps} model turns, ${Number(result.elapsed_seconds).toFixed(1)} seconds including reset.`;
    byId("task-budget").textContent = `Limit: ${result.budget.max_steps} turns · ${result.budget.timeout_seconds} seconds.`;
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

async function loadTasks() {
  const response = await fetch("assets/starter-data.json");
  if (!response.ok) throw new Error("Task data could not load.");
  dataset = await response.json();
  if (!Array.isArray(dataset.tasks) || !dataset.tasks.length) throw new Error("No tasks are available.");
  byId("task-count").textContent = `${dataset.tasks.length} tasks`;
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
    const result = task.result;
    const status = !result ? "Not run" : result.status === "error" ? "error" : result.grade.status.replaceAll("_", " ");
    row.append(first, cell(task.category), cell(result ? String(result.steps) : "—"), cell(result ? `${Number(result.elapsed_seconds).toFixed(1)}s` : "—"), cell(status, "result-status"));
    rows.append(row);
  });
  byId("run-model").textContent = dataset.run.model;
  byId("run-agent").textContent = `cua-agent ${dataset.run.cua_version}`;
  const recordedTasks = dataset.tasks.filter(task => task.result);
  byId("run-score").textContent = `${recordedTasks.filter(task => task.result.status !== "error" && task.result.grade.status === "passed").length} / ${recordedTasks.length} passed`;
  const budgets = recordedTasks.map(task => task.result.budget);
  const sharedBudget = budgets.every(budget => budget.max_steps === budgets[0].max_steps && budget.timeout_seconds === budgets[0].timeout_seconds);
  const limits = sharedBudget ? `The harness enforces a limit of ${budgets[0].max_steps} model turns and ${budgets[0].timeout_seconds} seconds per task.` : "The harness enforces the time and turn limits recorded for each task.";
  const bridge = dataset.run.model.startsWith("openai/") ? " through an OpenAI Responses compatibility bridge" : "";
  byId("run-protocol").textContent = `Each task starts from the same VM snapshot. ${limits} Cua’s ComputerAgent drives a TempleOS computer adapter${bridge}.`;
  selectTask(dataset.tasks[0].id);
}

loadTasks().catch(() => {
  byId("task-rows").replaceChildren();
  const row = document.createElement("tr");
  const message = cell("Task data could not load.");
  message.colSpan = 5;
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
