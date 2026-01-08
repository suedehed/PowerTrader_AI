const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const runnerState = document.getElementById("runnerState");
const traderState = document.getElementById("traderState");
const readyState = document.getElementById("readyState");
const autoState = document.getElementById("autoState");
const runnerLogs = document.getElementById("runnerLogs");
const traderLogs = document.getElementById("traderLogs");

async function post(path) {
  await fetch(path, { method: "POST" });
  await refresh();
}

function renderProcess(info, label) {
  if (!info) return `${label}: unknown`;
  if (info.running) {
    return `${label}: running (pid ${info.pid})`;
  }
  return `${label}: stopped`;
}

async function refresh() {
  try {
    const [statusRes, logsRes] = await Promise.all([
      fetch("/api/status"),
      fetch("/api/logs"),
    ]);
    const status = await statusRes.json();
    const logs = await logsRes.json();

    runnerState.textContent = renderProcess(status.runner, "Runner");
    traderState.textContent = renderProcess(status.trader, "Trader");
    readyState.textContent = status.runner_ready?.ready ? "Ready" : "Not ready";
    autoState.textContent = status.auto_start_pending ? "Pending" : "Idle";

    runnerLogs.textContent = (logs.runner || []).slice(-200).join("\n");
    traderLogs.textContent = (logs.trader || []).slice(-200).join("\n");
  } catch (err) {
    runnerState.textContent = "Runner: error";
    traderState.textContent = "Trader: error";
  }
}

startBtn.addEventListener("click", () => post("/api/start"));
stopBtn.addEventListener("click", () => post("/api/stop"));

refresh();
setInterval(refresh, 2000);
