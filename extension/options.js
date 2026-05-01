const DEFAULT_SETTINGS = {
  serverUrl: "ws://127.0.0.1:8000/captcha_ws",
  apiKey: "",
  routeKey: "",
  clientLabel: ""
};

const $ = (id) => document.getElementById(id);
let reconnectInProgress = false;

function normalizeSettings(values) {
  return {
    serverUrl: (values.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
    apiKey: (values.apiKey || "").trim(),
    routeKey: (values.routeKey || "").trim(),
    clientLabel: (values.clientLabel || "").trim()
  };
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

function loadSettings() {
  chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
    const settings = normalizeSettings(stored);
    $("serverUrl").value = settings.serverUrl;
    $("apiKey").value = settings.apiKey;
    $("routeKey").value = settings.routeKey;
    $("clientLabel").value = settings.clientLabel;
  });
}

function saveSettings() {
  const settings = normalizeSettings({
    serverUrl: $("serverUrl").value,
    apiKey: $("apiKey").value,
    routeKey: $("routeKey").value,
    clientLabel: $("clientLabel").value
  });

  if (!isValidWsUrl(settings.serverUrl)) {
    setStatus("WebSocket URL must start with ws:// or wss://.", true);
    return;
  }
  if (!settings.apiKey) {
    setStatus("API Key cannot be empty.", true);
    return;
  }
  chrome.storage.local.set(settings, () => {
    if (chrome.runtime.lastError) {
      setStatus(`Save failed: ${chrome.runtime.lastError.message}`, true);
      return;
    }
    setStatus("Saved. Background connection will auto-reconnect.");
  });
}

function updateRuntimeStatus(state) {
  const el = $("runtimeStatus");
  if (!state) {
    el.textContent = "Connection status: unknown";
    return;
  }
  const ws = state.wsStatus || "unknown";
  const route = state.routeKey || "(empty)";
  const managed = state.managedApiKeyId || "-";
  const ack = state.lastRegisterStatus || "unknown";
  const source = state.bindingSource || "unknown";
  const ackError = state.lastRegisterError ? `, register_error=${state.lastRegisterError}` : "";
  const last = state.lastError ? `, error=${state.lastError}` : "";
  el.textContent = `Connection status: ${ws}, route=${route}, managed_key=${managed}, binding=${source}, register=${ack}${ackError}${last}`;
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

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  $("saveBtn").addEventListener("click", saveSettings);
  $("reconnectBtn").addEventListener("click", reconnectNow);
  $("testBtn").addEventListener("click", runTokenTest);
  refreshRuntimeStatus();
  setInterval(refreshRuntimeStatus, 3000);
});
