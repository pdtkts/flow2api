let ws = null;
let reconnectTimeout = null;
let heartbeatInterval = null;
let cachedInstanceId = null;
let sessionRefreshTimeout = null;

const DEFAULT_SETTINGS = {
    serverUrl: "ws://127.0.0.1:8000/captcha_ws",
    connectionMode: "endUser",
    apiKey: "",
    workerAuthKey: "",
    routeKey: "",
    clientLabel: ""
};
const runtimeState = {
    wsStatus: "idle",
    connectionMode: "",
    routeKey: "",
    instanceId: "",
    workerSessionId: "",
    managedApiKeyId: "",
    dedicatedWorkerId: "",
    dedicatedTokenId: "",
    bindingSource: "",
    lastRegisterStatus: "never",
    lastRegisterError: "",
    lastError: "",
    sessionRefreshInFlight: false,
    sessionRefreshLastSuccessAt: 0,
    sessionRefreshLastFailureAt: 0,
    sessionRefreshLastReason: "",
    sessionRefreshLastError: "",
    sessionRefreshConsecutiveFailures: 0,
    sessionRefreshNextAt: 0,
    events: []
};

const WORKER_REFRESH_SUCCESS_INTERVAL_MS = 8 * 60 * 1000;
const WORKER_REFRESH_MISSING_COOKIE_INTERVAL_MS = 15 * 60 * 1000;
const WORKER_REFRESH_RETRY_BASE_MS = 45 * 1000;
const WORKER_REFRESH_RETRY_MAX_MS = 5 * 60 * 1000;
const WORKER_REFRESH_RECOVERY_DELAY_MS = 8 * 1000;
const SESSION_REFRESH_WARMUP_URL = "https://labs.google/fx/tools/flow";
const SESSION_REFRESH_WARMUP_WAIT_MS = 10000;

function inferConnectionMode(stored) {
    const explicit = String(stored.connectionMode || "").trim();
    if (explicit === "worker" || explicit === "endUser") {
        return explicit;
    }
    const wk = String(stored.workerAuthKey || "").trim();
    const ak = String(stored.apiKey || "").trim();
    if (wk && !ak) return "worker";
    return "endUser";
}

function pushEvent(type, message, level = "info") {
    const evt = {
        ts: Date.now(),
        type: String(type || "event"),
        message: String(message || ""),
        level: level === "error" || level === "warn" ? level : "info",
    };
    const list = Array.isArray(runtimeState.events) ? runtimeState.events : [];
    list.push(evt);
    if (list.length > 50) {
        list.splice(0, list.length - 50);
    }
    runtimeState.events = list;
}

/** Public hosts should use wss://; keep ws:// for localhost-style hosts. */
function normalizeWebSocketUrl(raw) {
    const trimmed = String(raw || "").trim();
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

function generateInstanceId() {
    const rand = Math.random().toString(36).slice(2, 10);
    return `ext-${Date.now().toString(36)}-${rand}`;
}

function getInstanceId() {
    if (cachedInstanceId) return Promise.resolve(cachedInstanceId);
    return new Promise((resolve) => {
        chrome.storage.local.get({ extensionInstanceId: "" }, (stored) => {
            const existing = String(stored.extensionInstanceId || "").trim();
            if (existing) {
                cachedInstanceId = existing;
                resolve(cachedInstanceId);
                return;
            }
            const created = generateInstanceId();
            chrome.storage.local.set({ extensionInstanceId: created }, () => {
                cachedInstanceId = created;
                resolve(cachedInstanceId);
            });
        });
    });
}

function getSettings() {
    return new Promise((resolve) => {
        chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
            const connectionMode = inferConnectionMode(stored);
            resolve({
                serverUrl: normalizeWebSocketUrl((stored.serverUrl || DEFAULT_SETTINGS.serverUrl).trim()),
                connectionMode,
                apiKey: (stored.apiKey || "").trim(),
                workerAuthKey: (stored.workerAuthKey || "").trim(),
                routeKey: (stored.routeKey || "").trim(),
                clientLabel: (stored.clientLabel || "").trim(),
            });
        });
    });
}

function closeSocket() {
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = null;
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
    stopWorkerSessionRefreshScheduler();
    if (ws) {
        try {
            ws.close();
        } catch (e) {
            console.log("[Flow2API] Close socket error", e);
        }
        ws = null;
    }
}

function stopWorkerSessionRefreshScheduler() {
    if (sessionRefreshTimeout) clearTimeout(sessionRefreshTimeout);
    sessionRefreshTimeout = null;
    runtimeState.sessionRefreshNextAt = 0;
    runtimeState.sessionRefreshInFlight = false;
}

function isWorkerModeConnected() {
    return Boolean(
        ws &&
        ws.readyState === WebSocket.OPEN &&
        runtimeState.connectionMode === "worker" &&
        runtimeState.lastRegisterStatus === "ok"
    );
}

function computeWorkerRefreshBackoffMs(errorCode, failures) {
    if (errorCode === "session_cookie_missing") {
        return WORKER_REFRESH_MISSING_COOKIE_INTERVAL_MS;
    }
    const exponent = Math.max(0, Number(failures || 1) - 1);
    const next = WORKER_REFRESH_RETRY_BASE_MS * Math.pow(2, exponent);
    return Math.min(WORKER_REFRESH_RETRY_MAX_MS, next);
}

function scheduleWorkerSessionRefresh(delayMs, reason = "proactive") {
    if (sessionRefreshTimeout) clearTimeout(sessionRefreshTimeout);
    if (runtimeState.connectionMode !== "worker") {
        runtimeState.sessionRefreshNextAt = 0;
        return;
    }
    const safeDelay = Math.max(1000, Number(delayMs || WORKER_REFRESH_SUCCESS_INTERVAL_MS));
    runtimeState.sessionRefreshNextAt = Date.now() + safeDelay;
    sessionRefreshTimeout = setTimeout(() => {
        sessionRefreshTimeout = null;
        performSessionRefresh({ reason }).catch((err) => {
            console.log("[Flow2API] proactive session refresh execution failed:", err);
        });
    }, safeDelay);
}

async function performSessionRefresh({ reason = "server_request", reqId = null } = {}) {
    const refreshReason = String(reason || "server_request");
    if (runtimeState.connectionMode !== "worker") {
        return { success: false, error: "worker_mode_required", reason: refreshReason };
    }
    if (runtimeState.sessionRefreshInFlight) {
        return { success: false, error: "session_refresh_busy", reason: refreshReason };
    }
    runtimeState.sessionRefreshInFlight = true;
    runtimeState.sessionRefreshLastReason = refreshReason;
    try {
        const warmupResult = await warmupLabsForSessionRefresh();
        if (!warmupResult.success) {
            pushEvent("session_refresh_warmup_warn", `Warmup failed (${refreshReason}): ${warmupResult.error}`, "warn");
        }
        const result = await getSessionTokenFromCookie();
        if (result.success) {
            runtimeState.sessionRefreshLastSuccessAt = Date.now();
            runtimeState.sessionRefreshLastError = "";
            runtimeState.sessionRefreshConsecutiveFailures = 0;
            pushEvent("session_refresh_ok", `Session refresh succeeded (${refreshReason})`);
            if (reqId && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    req_id: reqId,
                    status: "success",
                    session_token: result.sessionToken
                }));
            }
            if (refreshReason !== "server_request" && isWorkerModeConnected()) {
                scheduleWorkerSessionRefresh(WORKER_REFRESH_SUCCESS_INTERVAL_MS, "proactive");
            }
            return { success: true, sessionToken: result.sessionToken, reason: refreshReason };
        }

        const errorCode = result.error || "session_refresh_failed";
        runtimeState.sessionRefreshLastFailureAt = Date.now();
        runtimeState.sessionRefreshLastError = errorCode;
        runtimeState.sessionRefreshConsecutiveFailures += 1;
        pushEvent("session_refresh_error", `Session refresh failed (${refreshReason}): ${errorCode}`, "warn");
        if (reqId && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                req_id: reqId,
                status: "error",
                error: errorCode
            }));
        }
        if (refreshReason !== "server_request" && isWorkerModeConnected()) {
            const retryMs = computeWorkerRefreshBackoffMs(errorCode, runtimeState.sessionRefreshConsecutiveFailures);
            scheduleWorkerSessionRefresh(retryMs, "proactive");
        }
        return { success: false, error: errorCode, reason: refreshReason };
    } finally {
        runtimeState.sessionRefreshInFlight = false;
    }
}

function resetRuntimeStatePartial() {
    runtimeState.wsStatus = "idle";
    runtimeState.connectionMode = "";
    runtimeState.routeKey = "";
    runtimeState.workerSessionId = "";
    runtimeState.managedApiKeyId = "";
    runtimeState.dedicatedWorkerId = "";
    runtimeState.dedicatedTokenId = "";
    runtimeState.bindingSource = "";
    runtimeState.lastRegisterStatus = "never";
    runtimeState.lastRegisterError = "";
    runtimeState.lastError = "";
    runtimeState.sessionRefreshInFlight = false;
    runtimeState.sessionRefreshLastSuccessAt = 0;
    runtimeState.sessionRefreshLastFailureAt = 0;
    runtimeState.sessionRefreshLastReason = "";
    runtimeState.sessionRefreshLastError = "";
    runtimeState.sessionRefreshConsecutiveFailures = 0;
    runtimeState.sessionRefreshNextAt = 0;
    runtimeState.events = [];
}

/** Clear saved settings, drop stable instance id, and reconnect (used by options Reset). */
function resetExtensionToDefaults(done) {
    cachedInstanceId = null;
    resetRuntimeStatePartial();
    closeSocket();
    chrome.storage.local.remove(["extensionInstanceId"], () => {
        chrome.storage.local.set(
            {
                serverUrl: DEFAULT_SETTINGS.serverUrl,
                connectionMode: DEFAULT_SETTINGS.connectionMode,
                apiKey: DEFAULT_SETTINGS.apiKey,
                workerAuthKey: DEFAULT_SETTINGS.workerAuthKey,
                routeKey: DEFAULT_SETTINGS.routeKey,
                clientLabel: DEFAULT_SETTINGS.clientLabel,
            },
            () => {
                console.log("[Flow2API] Extension reset to defaults.");
                pushEvent("reset", "Extension reset to defaults and reconnect started");
                connectWS()
                    .then(() => {
                        if (typeof done === "function") done(null);
                    })
                    .catch((err) => {
                        if (typeof done === "function") done(err);
                    });
            }
        );
    });
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function waitForTabReady(tabId, timeoutMs = 12000) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timer);
            resolve();
        };
        const onUpdated = (updatedTabId, changeInfo) => {
            if (updatedTabId === tabId && changeInfo.status === "complete") {
                finish();
            }
        };
        const timer = setTimeout(finish, timeoutMs);

        chrome.tabs.onUpdated.addListener(onUpdated);
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError) {
                finish();
                return;
            }
            if (tab && tab.status === "complete") {
                finish();
            }
        });
    });
}

async function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    const settings = await getSettings();
    const instanceId = await getInstanceId();
    const mode = settings.connectionMode === "worker" ? "worker" : "endUser";
    stopWorkerSessionRefreshScheduler();
    runtimeState.connectionMode = mode;
    runtimeState.routeKey = mode === "endUser" ? settings.routeKey : "";
    runtimeState.instanceId = instanceId;
    runtimeState.workerSessionId = "";
    runtimeState.managedApiKeyId = "";
    runtimeState.bindingSource = "";
    runtimeState.wsStatus = "connecting";
    runtimeState.lastRegisterStatus = "pending";
    runtimeState.lastRegisterError = "";
    runtimeState.lastError = "";
    pushEvent("connect_start", `Connecting to ${settings.serverUrl || DEFAULT_SETTINGS.serverUrl}`);
    const url = new URL(settings.serverUrl || DEFAULT_SETTINGS.serverUrl);
    if (mode === "worker") {
        if (settings.workerAuthKey) {
            url.searchParams.set("worker_key", settings.workerAuthKey);
        }
    } else {
        if (settings.apiKey) {
            url.searchParams.set("key", settings.apiKey);
        }
        if (settings.routeKey) {
            url.searchParams.set("route_key", settings.routeKey);
        }
        if (settings.clientLabel) {
            url.searchParams.set("client_label", settings.clientLabel);
        }
    }
    url.searchParams.set("instance_id", instanceId);
    const socket = new WebSocket(url.toString());
    ws = socket;

    socket.onopen = () => {
        if (socket !== ws) return;
        console.log("[Flow2API] Background connected to WebSocket", url.toString());
        runtimeState.wsStatus = "open";
        pushEvent("connect_open", "WebSocket connected");
        socket.send(JSON.stringify({
            type: "register",
            route_key: mode === "endUser" ? settings.routeKey : "",
            client_label: mode === "endUser" ? settings.clientLabel : "",
            instance_id: instanceId,
        }));
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            if (socket === ws && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: "ping" }));
            }
        }, 20000);
    };

    let tokenQueue = Promise.resolve();

    socket.onmessage = async (event) => {
        if (socket !== ws) return;
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        if (data.type === "register_ack") {
            const ackStatus = data.status || "ok";
            const ackError = String(data.error || "").trim();
            runtimeState.lastRegisterStatus = ackStatus;
            runtimeState.lastRegisterError = ackError;
            runtimeState.bindingSource = String(data.binding_source || "");
            runtimeState.instanceId = String(data.instance_id || runtimeState.instanceId || "");
            runtimeState.workerSessionId = String(data.worker_session_id || "");
            runtimeState.managedApiKeyId = String(data.managed_api_key_id || "");
            runtimeState.dedicatedWorkerId = String(data.dedicated_worker_id || "");
            runtimeState.dedicatedTokenId = String(data.dedicated_token_id || "");
            if (ackStatus === "error") {
                runtimeState.wsStatus = "open_register_error";
                runtimeState.lastError = ackError || "register_failed";
                pushEvent("register_ack", `Register failed: ${ackError || "unknown"}`, "error");
                console.log("[Flow2API] Register ack error:", ackError || "unknown");
                stopWorkerSessionRefreshScheduler();
            } else {
                runtimeState.wsStatus = "open";
                runtimeState.lastError = "";
                pushEvent("register_ack", "Register successful");
                console.log(
                    "[Flow2API] Registered route key:",
                    data.route_key || "(empty)",
                    "managed_api_key_id=",
                    runtimeState.managedApiKeyId || "-",
                    "binding_source=",
                    runtimeState.bindingSource || "-"
                );
                if (runtimeState.connectionMode === "worker") {
                    scheduleWorkerSessionRefresh(WORKER_REFRESH_RECOVERY_DELAY_MS, "reconnect_recovery");
                } else {
                    stopWorkerSessionRefreshScheduler();
                }
            }
            return;
        }

        if (data.type === "get_token") {
            tokenQueue = tokenQueue.then(() => handleGetToken(data)).catch(err => {
                console.error("[Flow2API] Queue Error:", err);
            });
            return;
        }
        if (data.type === "refresh_st") {
            tokenQueue = tokenQueue.then(() => handleRefreshSessionToken(data)).catch(err => {
                console.error("[Flow2API] refresh_st queue error:", err);
            });
        }
    };

    socket.onclose = () => {
        if (socket !== ws) return;
        console.log("[Flow2API] WebSocket Closed. Reconnecting in 2s...");
        runtimeState.wsStatus = "closed";
        stopWorkerSessionRefreshScheduler();
        pushEvent("connect_close", "WebSocket closed, reconnect scheduled", "warn");
        ws = null;
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(connectWS, 2000);
    };

    socket.onerror = (e) => {
        if (socket !== ws) return;
        console.log("[Flow2API] WebSocket Error", e);
        runtimeState.wsStatus = "error";
        runtimeState.lastError = "websocket_error";
        pushEvent("connect_error", "WebSocket transport error", "error");
    };
}

async function generateTokenInFreshTab(action) {
    let newTabId = null;
    let lastErrorMsg = "No response from tab.";
    try {
        console.log("[Flow2API] Auto-opening fresh Google Labs tab to avoid token expiry...");
        const newTab = await chrome.tabs.create({ url: "https://labs.google/fx/tools/flow", active: false });
        newTabId = newTab.id;

        await waitForTabReady(newTabId);
        await sleep(1200);

        let successToken = null;
        const scriptTimeoutMs = action === "VIDEO_GENERATION" ? 30000 : 20000;

        try {
            const results = await chrome.scripting.executeScript({
                target: { tabId: newTabId },
                world: "MAIN",
                func: async (action, timeoutMs) => {
                    return new Promise((resolve, reject) => {
                        let settled = false;
                        const finish = (fn, value) => {
                            if (settled) return;
                            settled = true;
                            fn(value);
                        };
                        try {
                            function run() {
                                grecaptcha.enterprise.ready(function() {
                                    grecaptcha.enterprise.execute("6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV", { action: action })
                                        .then(token => finish(resolve, token))
                                        .catch(err => finish(reject, err.message || "reCAPTCHA evaluation failed internally"));
                                });
                            }

                            if (typeof grecaptcha !== "undefined" && grecaptcha.enterprise) {
                                run();
                            } else {
                                const s = document.createElement("script");
                                s.src = "https://www.google.com/recaptcha/enterprise.js?render=6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV";
                                s.onload = run;
                                s.onerror = () => finish(reject, "Failed to load enterprise.js via network");
                                document.head.appendChild(s);
                            }

                            setTimeout(() => finish(reject, "Timeout generating reCAPTCHA locally"), timeoutMs);
                        } catch (e) {
                            finish(reject, e.message);
                        }
                    });
                },
                args: [action || "IMAGE_GENERATION", scriptTimeoutMs]
            });

            if (results && results[0] && results[0].result) {
                successToken = results[0].result;
            }
        } catch (e) {
            lastErrorMsg = e.message || "Script execution failed";
        }

        if (successToken) {
            runtimeState.lastError = "";
            return { success: true, token: successToken };
        }
        runtimeState.lastError = lastErrorMsg;
        return { success: false, error: "Extension script failed: " + lastErrorMsg };
    } catch (err) {
        runtimeState.lastError = err.message || "unknown_error";
        return { success: false, error: err.message || "unknown_error" };
    } finally {
        if (newTabId) {
            try {
                await chrome.tabs.remove(newTabId);
                console.log("[Flow2API] Closed temporary token tab.");
            } catch (e) {
                console.log("[Flow2API] Error closing tab:", e);
            }
        }
    }
}

async function handleGetToken(data) {
    const result = await generateTokenInFreshTab(data.action || "IMAGE_GENERATION");
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (result.success) {
        ws.send(JSON.stringify({
            req_id: data.req_id,
            status: "success",
            token: result.token
        }));
    } else {
        ws.send(JSON.stringify({
            req_id: data.req_id,
            status: "error",
            error: result.error || "unknown_error"
        }));
    }
}

async function getSessionTokenFromCookie() {
    return new Promise((resolve) => {
        chrome.cookies.get(
            { url: "https://labs.google/", name: "__Secure-next-auth.session-token" },
            (cookie) => {
                if (chrome.runtime.lastError) {
                    resolve({ success: false, error: chrome.runtime.lastError.message || "cookie_read_failed" });
                    return;
                }
                const value = cookie && cookie.value ? String(cookie.value).trim() : "";
                if (!value) {
                    resolve({ success: false, error: "session_cookie_missing" });
                    return;
                }
                resolve({ success: true, sessionToken: value });
            }
        );
    });
}

async function warmupLabsForSessionRefresh() {
    let newTabId = null;
    try {
        const tab = await chrome.tabs.create({ url: SESSION_REFRESH_WARMUP_URL, active: false });
        newTabId = tab && tab.id ? tab.id : null;
        if (!newTabId) {
            return { success: false, error: "warmup_tab_create_failed" };
        }
        await waitForTabReady(newTabId);
        await sleep(SESSION_REFRESH_WARMUP_WAIT_MS);
        return { success: true };
    } catch (err) {
        return { success: false, error: (err && err.message) ? err.message : "warmup_failed" };
    } finally {
        if (newTabId) {
            try {
                await chrome.tabs.remove(newTabId);
            } catch (e) {
                console.log("[Flow2API] Session warmup tab close error:", e);
            }
        }
    }
}

async function handleRefreshSessionToken(data) {
    await performSessionRefresh({ reason: "server_request", reqId: data && data.req_id ? data.req_id : null });
}

chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (
        changes.routeKey ||
        changes.serverUrl ||
        changes.clientLabel ||
        changes.apiKey ||
        changes.workerAuthKey ||
        changes.connectionMode
    ) {
        console.log("[Flow2API] Extension settings changed, reconnecting WebSocket...");
        pushEvent("settings_changed", "Settings changed, reconnecting");
        closeSocket();
        connectWS();
    }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || !message.type) return;
    if (message.type === "get_status") {
        sendResponse({ success: true, state: runtimeState });
        return;
    }
    if (message.type === "reconnect_now") {
        pushEvent("manual_reconnect", "Manual reconnect triggered");
        closeSocket();
        connectWS()
            .then(() => sendResponse({ success: true }))
            .catch((err) => sendResponse({ success: false, error: err.message || "reconnect_failed" }));
        return true;
    }
    if (message.type === "reset_extension") {
        resetExtensionToDefaults((err) => {
            if (err) {
                pushEvent("reset", `Reset failed: ${err.message || "unknown"}`, "error");
                sendResponse({ success: false, error: err.message || "reset_failed" });
            } else {
                sendResponse({ success: true });
            }
        });
        return true;
    }
    if (message.type === "test_token") {
        pushEvent("test_token", `Test token started (${message.action || "IMAGE_GENERATION"})`);
        generateTokenInFreshTab(message.action || "IMAGE_GENERATION")
            .then((result) => sendResponse(result))
            .catch((err) => sendResponse({ success: false, error: err.message || "test_failed" }));
        return true;
    }
});

pushEvent("startup", "Background worker started");
connectWS();
