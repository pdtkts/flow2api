const DEFAULT_SETTINGS = {
  serverUrl: "ws://127.0.0.1:8000/captcha_ws",
  connectionMode: "endUser",
  apiKey: "",
  workerAuthKey: "",
  routeKey: "",
  clientLabel: ""
};

const STORAGE_KEYS = {
  serverUrl: DEFAULT_SETTINGS.serverUrl,
  connectionMode: DEFAULT_SETTINGS.connectionMode,
  apiKey: "",
  workerAuthKey: "",
  routeKey: "",
  clientLabel: ""
};

const $ = (id) => document.getElementById(id);
let reconnectInProgress = false;

function normalizeSettings(values) {
  const mode = (values.connectionMode || "").trim() === "worker" ? "worker" : "endUser";
  return {
    serverUrl: normalizeWebSocketUrl((values.serverUrl || DEFAULT_SETTINGS.serverUrl).trim()),
    connectionMode: mode,
    apiKey: (values.apiKey || "").trim(),
    workerAuthKey: (values.workerAuthKey || "").trim(),
    routeKey: (values.routeKey || "").trim(),
    clientLabel: (values.clientLabel || "").trim()
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

/** Use wss:// on the public internet; keep ws:// for localhost / LAN-style hosts. */
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
  setActiveMode(settings.connectionMode);
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
      setStatus("Saved (End user). Worker key cleared. Background will reconnect.");
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
    setStatus("Saved (Worker). API key and labels cleared. Background will reconnect.");
    $("apiKey").value = "";
    $("clientLabel").value = "";
    $("routeKey").value = "";
  });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
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

function renderEventLog(events) {
  const logEl = $("eventLogList");
  const list = Array.isArray(events) ? events.slice(-10).reverse() : [];
  if (!list.length) {
    logEl.innerHTML = `<li class="event-item">No recent events</li>`;
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
    renderEventLog([]);
    return;
  }
  renderStatusCards(state);
  renderEventLog(state.events);
  metaEl.textContent = `Last update: ${new Date().toLocaleTimeString()} • Status: ${state.wsStatus || "unknown"}`;
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
  if (!confirm("Reset this extension?\n\nThis removes WebSocket URL, API key, worker key, labels, route key, and assigns a new instance id. The background worker reconnects with default local URL.")) {
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
  setStatus("Running token test, please wait...");
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

function wireTabs() {
  $("tabEndUser").addEventListener("click", () => setActiveMode("endUser"));
  $("tabWorker").addEventListener("click", () => setActiveMode("worker"));
}

document.addEventListener("DOMContentLoaded", () => {
  wireTabs();
  loadSettings();
  $("saveBtn").addEventListener("click", saveSettings);
  $("reconnectBtn").addEventListener("click", reconnectNow);
  $("testBtn").addEventListener("click", runTokenTest);
  $("resetBtn").addEventListener("click", runResetExtension);
  refreshRuntimeStatus();
  setInterval(refreshRuntimeStatus, 3000);
});
