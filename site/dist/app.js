"use strict";
const $ = id => document.getElementById(id);
let dataset, selected, timer = null;
function stop() { clearInterval(timer); timer = null; $("play").textContent = "Play"; $("play").setAttribute("aria-label", "Play recording"); }
function showFrame(index) {
  if (!selected) return;
  const frames = selected.frames, n = Math.max(0, Math.min(frames.length - 1, Number(index))), frame = frames[n];
  $("timeline").value = n; $("recording-step").textContent = `Step ${frame.step} / ${selected.result.steps}`;
  $("frame").src = frame.src; $("frame").alt = `TempleOS ${selected.title}, recorded step ${frame.step}`;
  $("step-note").textContent = frame.note || (n === 0 ? "Initial desktop, before the agent acts." : "Actions executed. Recorded desktop follows.");
  $("step-actions").textContent = frame.actions?.length ? frame.actions.map(a => a.kind === "type" ? `Type:\n${a.text}` : JSON.stringify(a)).join("\n\n") : "Initial observation.";
  $("previous").disabled = n === 0; $("next").disabled = n === frames.length - 1;
}
function selectTask(id, scroll = false) {
  const task = dataset.tasks.find(t => t.id === id); if (!task) return; stop(); selected = task;
  $("task-number").textContent = `Task ${String(dataset.tasks.indexOf(task) + 1).padStart(2, "0")}`;
  $("task-title").textContent = task.title; $("task-description").textContent = task.description;
  $("task-category").textContent = task.category; $("task-budget").textContent = `${task.result.budget.max_steps} turns · ${task.result.budget.timeout_seconds}s limit`;
  $("task-prompt").textContent = task.prompt.trim(); $("task-download").href = `assets/tasks/${task.id}.yaml`;
  $("task-outcome").textContent = `${task.result.status === "error" ? "The run encountered an execution error." : task.result.grade.status === "passed" ? "Passed the visual check." : "Visual check: " + task.result.grade.status + "."} ${task.result.steps} model turns; ${Number(task.result.elapsed_seconds).toFixed(1)} seconds including reset. Execution: ${task.result.status.replaceAll("_", " ")}.`;
  $("task-limitation").textContent = task.result.error || "Visible output is checked; the screenshot alone cannot prove how the program was implemented.";
  $("timeline").max = task.frames.length - 1; showFrame(task.frames.length - 1);
  document.querySelectorAll("#task-rows tr").forEach(row => { row.classList.toggle("selected", row.dataset.id === id); const button = row.querySelector(".task-selector"); button.setAttribute("aria-pressed", String(row.dataset.id === id)); });
  if (scroll) $("task-detail").scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block:"start"});
}
function td(text, className) { const node = document.createElement("td"); node.textContent = text; if (className) node.className = className; return node; }
fetch("assets/starter-data.json").then(r => { if (!r.ok) throw Error("Could not load task data"); return r.json(); }).then(data => {
  dataset = data;
  data.tasks.forEach((task, i) => {
    const row = document.createElement("tr"); row.dataset.id = task.id;
    const first = document.createElement("td"), button = document.createElement("button"), number = document.createElement("span"); button.className = "task-selector"; button.setAttribute("aria-pressed", "false"); number.textContent = String(i + 1).padStart(2, "0"); button.append(number, document.createTextNode(task.title)); button.addEventListener("click", () => selectTask(task.id, true)); first.append(button);
    const last = document.createElement("td"), view = document.createElement("button"); view.className = "row-view"; view.textContent = "View →"; view.setAttribute("aria-label", `View ${task.title}`); view.addEventListener("click", () => selectTask(task.id, true)); last.append(view);
    const status = task.result.status === "error" ? "error" : task.result.grade.status;
    row.append(first, td(task.category), td(status.replaceAll("_", " "), `result-status ${status}`), td(String(task.result.steps)), last); $("task-rows").append(row);
  });
  $("run-model").textContent = data.run.model; $("run-agent").textContent = `cua-agent ${data.run.cua_version}`;
  $("run-score").textContent = `${data.tasks.filter(t => t.result.grade.status === "passed").length} / ${data.tasks.length} passed`;
  $("run-date").textContent = new Date(data.run.created_at).toLocaleDateString("en-GB",{year:"numeric",month:"short",day:"2-digit",timeZone:"UTC"});
  $("run-description").textContent = data.run.description; selectTask(data.tasks[0].id);
}).catch(() => { $("task-rows").textContent = "Task data could not load. The results and run downloads are available below."; $("task-rows").className = "load-error"; $("task-outcome").textContent = "The recorded task could not load."; });
$("timeline").addEventListener("input", e => { stop(); showFrame(e.target.value); });
$("previous").addEventListener("click", () => { stop(); showFrame(Number($("timeline").value) - 1); });
$("next").addEventListener("click", () => { stop(); showFrame(Number($("timeline").value) + 1); });
$("play").addEventListener("click", () => { if (!selected) return; if (timer) return stop(); if (Number($("timeline").value) === selected.frames.length - 1) showFrame(0); $("play").textContent = "Pause"; $("play").setAttribute("aria-label", "Pause recording"); timer = setInterval(() => { const next = Number($("timeline").value) + 1; if (next >= selected.frames.length) return stop(); showFrame(next); }, 1100); });
document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
