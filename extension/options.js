const DEFAULT_SETTINGS = {
  serverUrl: "ws://127.0.0.1:8000/captcha_ws",
  connectionMode: "endUser",
  apiKey: "",
  workerAuthKey: "",
  routeKey: "",
  clientLabel: ""
};

const DEFAULT_WORKER_PAGE_URL = "https://labs.google/fx/tools/flow";

const STORAGE_KEYS = {
  serverUrl: DEFAULT_SETTINGS.serverUrl,
  connectionMode: DEFAULT_SETTINGS.connectionMode,
  apiKey: "",
  workerAuthKey: "",
  routeKey: "",
  clientLabel: "",
  workerPageUrl: DEFAULT_WORKER_PAGE_URL,
  usePersistentWorkerTab: false,
  autoRecycleWorkerTabOnCaptchaFailure: true
};

const $ = (id) => document.getElementById(id);
let reconnectInProgress = false;
let eventLogFilter = "all";

function normalizeWorkerPageUrl(raw) {
  const t = (raw || "").trim();
  if (!t) return DEFAULT_WORKER_PAGE_URL;
  try {
    const u = new URL(t);
    if (u.protocol !== "https:" && u.protocol !== "http:") return DEFAULT_WORKER_PAGE_URL;
    return u.toString();
  } catch {
    return DEFAULT_WORKER_PAGE_URL;
  }
}

function normalizeSettings(values) {
  const mode = (values.connectionMode || "").trim() === "worker" ? "worker" : "endUser";
  return {
    serverUrl: normalizeWebSocketUrl((values.serverUrl || DEFAULT_SETTINGS.serverUrl).trim()),
    connectionMode: mode,
    apiKey: (values.apiKey || "").trim(),
    workerAuthKey: (values.workerAuthKey || "").trim(),
    routeKey: (values.routeKey || "").trim(),
    clientLabel: (values.clientLabel || "").trim(),
    workerPageUrl: normalizeWorkerPageUrl(values.workerPageUrl),
    usePersistentWorkerTab: !!values.usePersistentWorkerTab,
    autoRecycleWorkerTabOnCaptchaFailure: values.autoRecycleWorkerTabOnCaptchaFailure !== false
  };
}

function inferConnectionMode(stored) {
  const explicit = (stored.connectionMode || "").trim();
  if (explicit === "worker" || explicit === "endUser") {
    return explicit;
  }
  const wk = (stored.workerAuthKey || "").trim();
  const ak = (stored.apiKey || "").trim();
  if (wk && !ak) return "worker";
  return "endUser";
}

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.style.color = isError ? "#b91c1c" : "#065f46";
}

function isValidWsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "ws:" || url.protocol === "wss:";
  } catch (e) {
    return false;
  }
}

function normalizeWebSocketUrl(raw) {
  const trimmed = (raw || "").trim();
  if (!trimmed) return trimmed;
  try {
    const u = new URL(trimmed);
    if (u.protocol !== "ws:") return trimmed;
    const host = (u.hostname || "").toLowerCase();
    const isLocal =
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "[::1]" ||
      host.endsWith(".local");
    if (isLocal) return trimmed;
    u.protocol = "wss:";
    return u.toString();
  } catch {
    return trimmed;
  }
}

function getActiveMode() {
  const endTab = $("tabEndUser");
  return endTab && endTab.getAttribute("aria-selected") === "true" ? "endUser" : "worker";
}

function setActiveMode(mode) {
  const isEnd = mode === "endUser";
  $("tabEndUser").setAttribute("aria-selected", isEnd ? "true" : "false");
  $("tabWorker").setAttribute("aria-selected", isEnd ? "false" : "true");
  $("panelEndUser").setAttribute("aria-hidden", isEnd ? "false" : "true");
  $("panelWorker").setAttribute("aria-hidden", isEnd ? "true" : "false");
}

function loadSettings() {
  chrome.storage.local.get(STORAGE_KEYS, (stored) => {
    const inferred = inferConnectionMode(stored);
    const rawUrl = (stored.serverUrl || DEFAULT_SETTINGS.serverUrl).trim();
    const fixedUrl = normalizeWebSocketUrl(rawUrl);
    if (fixedUrl && fixedUrl !== rawUrl) {
      chrome.storage.local.set({ serverUrl: fixedUrl }, () => {
        chrome.storage.local.get(STORAGE_KEYS, (s2) => {
          applyLoadedSettings(s2, inferConnectionMode(s2));
        });
      });
      return;
    }
    applyLoadedSettings(stored, inferred);
  });
}

function applyLoadedSettings(stored, inferredMode) {
  const settings = normalizeSettings({ ...stored, connectionMode: inferredMode });
  $("serverUrl").value = settings.serverUrl;
  $("apiKey").value = settings.apiKey;
  $("workerAuthKey").value = settings.workerAuthKey;
  $("routeKey").value = settings.routeKey;
  $("clientLabel").value = settings.clientLabel;
  $("workerPageUrl").value = settings.workerPageUrl;
  $("usePersistentWorkerTab").checked = settings.usePersistentWorkerTab;
  $("autoRecycleWorkerTabOnCaptchaFailure").checked = settings.autoRecycleWorkerTabOnCaptchaFailure;
  setActiveMode(settings.connectionMode);
  updateWorkerActionButtons();
}

function saveSettings() {
  const mode = getActiveMode();
  let serverUrl = normalizeWebSocketUrl(($("serverUrl").value || "").trim());
  $("serverUrl").value = serverUrl;

  if (!isValidWsUrl(serverUrl)) {
    setStatus("WebSocket URL must start with ws:// or wss://.", true);
    return;
  }

  if (mode === "endUser") {
    const apiKey = ($("apiKey").value || "").trim();
    if (!apiKey) {
      setStatus("API Key is required for End user mode.", true);
      return;
    }
    const payload = {
      serverUrl,
      connectionMode: "endUser",
      apiKey,
      workerAuthKey: "",
      clientLabel: ($("clientLabel").value || "").trim(),
      routeKey: ($("routeKey").value || "").trim()
    };
    chrome.storage.local.set(payload, () => {
      if (chrome.runtime.lastError) {
        setStatus(`Save failed: ${chrome.runtime.lastError.message}`, true);
        return;
      }
      setStatus("Saved connection (End user). Worker key cleared. Background will reconnect.");
    });
    return;
  }

  const workerAuthKey = ($("workerAuthKey").value || "").trim();
  if (!workerAuthKey) {
    setStatus("Worker Registration Key is required for Worker mode.", true);
    return;
  }
  const payload = {
    serverUrl,
    connectionMode: "worker",
    workerAuthKey,
    apiKey: "",
    clientLabel: "",
    routeKey: ""
  };
  chrome.storage.local.set(payload, () => {
    if (chrome.runtime.lastError) {
      setStatus(`Save failed: ${chrome.runtime.lastError.message}`, true);
      return;
    }
    setStatus("Saved connection (Worker). API key and labels cleared. Background will reconnect.");
    $("apiKey").value = "";
    $("clientLabel").value = "";
    $("routeKey").value = "";
  });
}

function saveWorkerSettings() {
  let workerPageUrl = normalizeWorkerPageUrl(($("workerPageUrl").value || "").trim());
  $("workerPageUrl").value = workerPageUrl;
  try {
    const u = new URL(workerPageUrl);
    if (u.hostname.toLowerCase() !== "labs.google") {
      setStatus("Worker URL must use hostname labs.google (extension host permissions).", true);
      return;
    }
    if (u.protocol !== "https:") {
      setStatus("Worker URL must use https://", true);
      return;
    }
  } catch {
    setStatus("Invalid worker page URL.", true);
    return;
  }
  const usePersistentWorkerTab = $("usePersistentWorkerTab").checked;
  const autoRecycleWorkerTabOnCaptchaFailure = $("autoRecycleWorkerTabOnCaptchaFailure").checked;
  chrome.storage.local.set(
    {
      workerPageUrl,
      usePersistentWorkerTab,
      autoRecycleWorkerTabOnCaptchaFailure
    },
    () => {
      if (chrome.runtime.lastError) {
        setStatus(`Save worker settings failed: ${chrome.runtime.lastError.message}`, true);
        return;
      }
      setStatus("Worker tab settings saved.");
      updateWorkerActionButtons();
    }
  );
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatPercent(solved, total) {
  if (!total) return "—";
  return `${((100 * solved) / total).toFixed(1)}%`;
}

function renderMetrics(state) {
  const el = $("metricsGrid");
  if (!state) {
    el.innerHTML = "";
    return;
  }
  const solved = Number(state.captchaJobsSucceeded) || 0;
  const failed = Number(state.captchaJobsFailed) || 0;
  const total = solved + failed;
  const srOk = Number(state.sessionRefreshSucceeded) || 0;
  const srFail = Number(state.sessionRefreshFailed) || 0;

  el.innerHTML = `
    <div class="metric-card">
      <span class="metric-label">Captcha solved</span>
      <span class="metric-value ok">${escapeHtml(String(solved))}</span>
    </div>
    <div class="metric-card">
      <span class="metric-label">Captcha failed</span>
      <span class="metric-value bad">${escapeHtml(String(failed))}</span>
    </div>
    <div class="metric-card">
      <span class="metric-label">Captcha total</span>
      <span class="metric-value">${escapeHtml(String(total))}</span>
      <div class="metric-sub">Success rate: ${escapeHtml(formatPercent(solved, total))}</div>
    </div>
    <div class="metric-card">
      <span class="metric-label">Session refresh</span>
      <span class="metric-value">${escapeHtml(String(srOk))} ok / ${escapeHtml(String(srFail))} fail</span>
      <div class="metric-sub">Worker mode server refresh</div>
    </div>
  `;
}

function renderJobHistory(state) {
  const body = $("jobHistoryBody");
  const list = Array.isArray(state.recentCaptchaJobs) ? [...state.recentCaptchaJobs].reverse() : [];
  if (!list.length) {
    body.innerHTML = `<tr><td colspan="5" class="event-item" style="border:0;">No captcha jobs yet</td></tr>`;
    return;
  }
  body.innerHTML = list
    .map((row) => {
      const ts = row && row.ts ? Number(row.ts) : 0;
      const timeStr = ts ? escapeHtml(new Date(ts).toLocaleString()) : "—";
      const action = escapeHtml(String((row && row.action) || ""));
      const ok = row && row.ok;
      const resCell = ok
        ? `<span class="job-ok">OK</span>`
        : `<span class="job-fail">FAIL</span>`;
      const req = escapeHtml(String((row && row.req_id) || ""));
      const err = escapeHtml(String((row && row.error) || ""));
      return `<tr>
        <td>${timeStr}</td>
        <td><code>${action}</code></td>
        <td>${resCell}</td>
        <td><code style="word-break:break-all;">${req}</code></td>
        <td>${err || "—"}</td>
      </tr>`;
    })
    .join("");
}

function renderStatusCards(state) {
  const cardsEl = $("statusCards");
  const ws = state.wsStatus || "unknown";
  const mode = state.connectionMode || "-";
  const route = state.routeKey || "(empty)";
  const instance = state.instanceId || "-";
  const workerSession = state.workerSessionId || "-";
  const managed = state.managedApiKeyId || "-";
  const dedicatedWorker = state.dedicatedWorkerId || "-";
  const dedicatedToken = state.dedicatedTokenId || "-";
  const ack = state.lastRegisterStatus || "unknown";
  const source = state.bindingSource || "unknown";
  const registerError = state.lastRegisterError || "-";
  const lastError = state.lastError || "-";
  const persistent = state.usePersistentWorkerTab ? "on" : "off";
  const workerTabId =
    state.workerTabId != null && state.workerTabId !== "" ? String(state.workerTabId) : "(none)";

  const items = [
    ["Connection", ws, false],
    ["Mode", mode, false],
    ["Register", ack, ack === "error"],
    ["Binding", source, false],
    ["Managed key", managed, false],
    ["Dedicated worker", dedicatedWorker, false],
    ["Dedicated token", dedicatedToken, false],
    ["Route key", route, false],
    ["Instance ID", instance, false],
    ["Worker session", workerSession, false],
    ["Persistent worker tab", persistent, false],
    ["Worker tab ID", workerTabId, false],
    ["Register error", registerError, registerError !== "-"],
    ["Last error", lastError, lastError !== "-"],
  ];

  cardsEl.innerHTML = items
    .map(([label, value, isError]) => {
      return `<div class="status-card">
        <span class="status-label">${escapeHtml(label)}</span>
        <span class="status-value${isError ? " error" : ""}">${escapeHtml(value)}</span>
      </div>`;
    })
    .join("");
}

function formatEventTime(ts) {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleTimeString();
}

function renderSessionTokenHistory(entries) {
  const listEl = $("sessionTokenHistoryList");
  if (!listEl) return;
  const list = Array.isArray(entries) ? entries : [];
  if (!list.length) {
    listEl.innerHTML = `<li class="event-item">No captures yet (server-requested session refresh only)</li>`;
    return;
  }
  listEl.innerHTML = list
    .map((row, idx) => {
      const ts = row && row.capturedAt ? Number(row.capturedAt) : 0;
      const timeStr = ts ? escapeHtml(new Date(ts).toLocaleString()) : "—";
      const tokenFull = escapeHtml(String((row && row.sessionToken) || ""));
      return `<li class="event-item">
        <span class="event-time">${timeStr}</span>
        <span class="event-level info">#${idx + 1}</span>
        <span><code>${tokenFull}</code></span>
      </li>`;
    })
    .join("");
}

function renderEventLog(events) {
  const logEl = $("eventLogList");
  let list = Array.isArray(events) ? [...events] : [];
  list = list.slice().reverse();
  if (eventLogFilter === "issues") {
    list = list.filter((evt) => evt && (evt.level === "warn" || evt.level === "error"));
  }
  if (!list.length) {
    logEl.innerHTML = `<li class="event-item">No events match this filter</li>`;
    return;
  }
  logEl.innerHTML = list
    .map((evt) => {
      const level = ["info", "warn", "error"].includes(String(evt.level || ""))
        ? String(evt.level)
        : "info";
      const msg = String(evt.message || evt.type || "-");
      return `<li class="event-item">
        <span class="event-time">${escapeHtml(formatEventTime(evt.ts))}</span>
        <span class="event-level ${escapeHtml(level)}">${escapeHtml(level.toUpperCase())}</span>
        <span>${escapeHtml(msg)}</span>
      </li>`;
    })
    .join("");
}

function updateRuntimeStatus(state) {
  const metaEl = $("statusMeta");
  if (!state) {
    $("statusCards").innerHTML = `<div class="status-card"><span class="status-label">Connection</span><span class="status-value">unknown</span></div>`;
    metaEl.textContent = "Last update: no runtime state";
    renderMetrics(null);
    renderJobHistory({ recentCaptchaJobs: [] });
    renderSessionTokenHistory([]);
    renderEventLog([]);
    return;
  }
  renderMetrics(state);
  renderJobHistory(state);
  renderStatusCards(state);
  renderSessionTokenHistory(state.flowSessionTokenHistory);
  renderEventLog(state.events);
  metaEl.textContent = `Last update: ${new Date().toLocaleTimeString()} • WebSocket: ${state.wsStatus || "unknown"}`;
}

function refreshRuntimeStatus() {
  chrome.runtime.sendMessage({ type: "get_status" }, (resp) => {
    if (chrome.runtime.lastError) return;
    if (resp && resp.success) updateRuntimeStatus(resp.state);
  });
}

function reconnectNow() {
  if (reconnectInProgress) return;
  reconnectInProgress = true;
  const reconnectBtn = $("reconnectBtn");
  if (reconnectBtn) reconnectBtn.disabled = true;
  setStatus("Reconnecting...", false);
  chrome.runtime.sendMessage({ type: "reconnect_now" }, (resp) => {
    if (chrome.runtime.lastError) {
      setStatus(`Reconnect failed: ${chrome.runtime.lastError.message}`, true);
    } else if (!resp || !resp.success) {
      setStatus(`Reconnect failed: ${(resp && resp.error) || "unknown"}`, true);
    } else {
      setStatus("Reconnect triggered.");
      setTimeout(refreshRuntimeStatus, 400);
    }
    reconnectInProgress = false;
    if (reconnectBtn) reconnectBtn.disabled = false;
  });
}

function runResetExtension() {
  if (!confirm("Reset this extension?\n\nThis removes WebSocket URL, API keys, labels, route key, worker tab settings, captcha job stats and history, session refresh counters, stored Flow session token history (last 3), worker tab id, and assigns a new instance id. The background worker reconnects with default local URL.")) {
    return;
  }
  setStatus("Resetting extension…", false);
  chrome.runtime.sendMessage({ type: "reset_extension" }, (resp) => {
    if (chrome.runtime.lastError) {
      setStatus(`Reset failed: ${chrome.runtime.lastError.message}`, true);
      return;
    }
    if (!resp || !resp.success) {
      setStatus(`Reset failed: ${(resp && resp.error) || "unknown"}`, true);
      return;
    }
    loadSettings();
    setStatus("Extension reset. Defaults loaded; background reconnected.");
    setTimeout(refreshRuntimeStatus, 400);
  });
}

function runTokenTest() {
  setStatus("Running token test, please wait...", false);
  chrome.runtime.sendMessage({ type: "test_token", action: "IMAGE_GENERATION" }, (resp) => {
    if (chrome.runtime.lastError) {
      setStatus(`Test failed: ${chrome.runtime.lastError.message}`, true);
      return;
    }
    if (resp && resp.success) {
      setStatus("Test passed: token acquired.");
    } else {
      setStatus(`Test failed: ${(resp && resp.error) || "unknown error"}`, true);
    }
    refreshRuntimeStatus();
  });
}

function updateWorkerActionButtons() {
  const on = $("usePersistentWorkerTab").checked;
  $("workerOpenBtn").disabled = !on;
  $("workerRecycleBtn").disabled = !on;
}

function sendWorkerMessage(type, okMsg) {
  setStatus("Working…", false);
  chrome.runtime.sendMessage({ type }, (resp) => {
    if (chrome.runtime.lastError) {
      setStatus(`${type} failed: ${chrome.runtime.lastError.message}`, true);
      return;
    }
    if (!resp || !resp.success) {
      const err = (resp && resp.error) || "unknown";
      if (err === "enable_persistent_worker_tab_first") {
        setStatus("Turn on “Use persistent worker tab” and save worker tab settings first.", true);
      } else {
        setStatus(`${type} failed: ${err}`, true);
      }
      return;
    }
    setStatus(okMsg || "Done.");
    refreshRuntimeStatus();
  });
}

function wireTabs() {
  $("tabEndUser").addEventListener("click", () => setActiveMode("endUser"));
  $("tabWorker").addEventListener("click", () => setActiveMode("worker"));
}

document.addEventListener("DOMContentLoaded", () => {
  wireTabs();
  loadSettings();
  $("saveBtn").addEventListener("click", saveSettings);
  $("saveWorkerBtn").addEventListener("click", saveWorkerSettings);
  $("reconnectBtn").addEventListener("click", reconnectNow);
  $("testBtn").addEventListener("click", runTokenTest);
  $("resetBtn").addEventListener("click", runResetExtension);
  $("usePersistentWorkerTab").addEventListener("change", updateWorkerActionButtons);
  $("workerOpenBtn").addEventListener("click", () =>
    sendWorkerMessage("worker_tab_open", "Worker tab opened.")
  );
  $("workerCloseBtn").addEventListener("click", () =>
    sendWorkerMessage("worker_tab_close", "Worker tab closed.")
  );
  $("workerRecycleBtn").addEventListener("click", () =>
    sendWorkerMessage("worker_tab_recycle", "Worker tab recycled.")
  );
  $("eventLogFilter").addEventListener("change", (e) => {
    eventLogFilter = (e.target && e.target.value) || "all";
    refreshRuntimeStatus();
  });
  refreshRuntimeStatus();
  updateWorkerActionButtons();
  setInterval(refreshRuntimeStatus, 3000);
});
