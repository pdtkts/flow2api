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
    clientLabel: "",
};

const DEFAULT_WORKER_PAGE_URL = "https://labs.google/fx/tools/flow";

const WORKER_RECAPTCHA_SETTLE_DEFAULT_MS = 1200;
const WORKER_RECAPTCHA_SETTLE_MAX_MS = 120000;

const DEFAULT_WORKER_SETTINGS = {
    workerPageUrl: DEFAULT_WORKER_PAGE_URL,
    usePersistentWorkerTab: false,
    autoRecycleWorkerTabOnCaptchaFailure: true,
    workerRecaptchaSettleMs: WORKER_RECAPTCHA_SETTLE_DEFAULT_MS,
};

const EVENTS_MAX = 100;
const RECENT_CAPTCHA_JOBS_MAX = 50;

const SESSION_REFRESH_WARMUP_URL = "https://labs.google/fx/tools/flow";
const SESSION_REFRESH_WARMUP_WAIT_MS = 10000;
const FLOW_SESSION_TOKEN_HISTORY_KEY = "flowSessionTokenHistory";
const FLOW_SESSION_TOKEN_HISTORY_MAX = 3;

const STORAGE_CAPTCHA_STATS = "extensionCaptchaJobStats";
const STORAGE_RECENT_JOBS = "extensionRecentCaptchaJobs";
const STORAGE_SESSION_REFRESH_STATS = "extensionSessionRefreshStats";
const STORAGE_GENERATION_STATS = "extensionGenerationJobStats";
const STORAGE_RECENT_GENERATION_JOBS = "extensionRecentGenerationJobs";
const STORAGE_WORKER_TAB_ID = "extensionWorkerTabId";

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
    events: [],
    flowSessionTokenHistory: [],
    captchaJobsSucceeded: 0,
    captchaJobsFailed: 0,
    recentCaptchaJobs: [],
    sessionRefreshSucceeded: 0,
    sessionRefreshFailed: 0,
    workerTabId: null,
    generationJobsSucceeded: 0,
    generationJobsFailed: 0,
    recentGenerationJobs: [],
    generationInFlight: false,
    generationLastPollFallbackReason: "",
    /** Dedicated worker capabilities from server register_ack (end-user mode: both true). */
    allowCaptcha: true,
    allowSessionRefresh: true,
};

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

function normalizeWorkerPageUrl(raw) {
    const t = String(raw || "").trim();
    if (!t) return DEFAULT_WORKER_PAGE_URL;
    try {
        const u = new URL(t);
        if (u.protocol !== "https:" && u.protocol !== "http:") return DEFAULT_WORKER_PAGE_URL;
        return u.toString();
    } catch {
        return DEFAULT_WORKER_PAGE_URL;
    }
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
    if (list.length > EVENTS_MAX) {
        list.splice(0, list.length - EVENTS_MAX);
    }
    runtimeState.events = list;
}

function normalizeRecentCaptchaJobs(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    for (const row of raw) {
        if (!row || typeof row !== "object") continue;
        out.push({
            ts: Number(row.ts) || 0,
            req_id: String(row.req_id || ""),
            action: String(row.action || ""),
            ok: !!row.ok,
            error: String(row.error || "").slice(0, 500),
        });
    }
    return out.slice(-RECENT_CAPTCHA_JOBS_MAX);
}

function normalizeRecentGenerationJobs(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    for (const row of raw) {
        if (!row || typeof row !== "object") continue;
        out.push({
            ts: Number(row.ts) || 0,
            command: String(row.command || ""),
            method: String(row.method || ""),
            url: String(row.url || ""),
            ok: !!row.ok,
            status: Number(row.status) || 0,
            error: String(row.error || "").slice(0, 500),
        });
    }
    return out.slice(-RECENT_CAPTCHA_JOBS_MAX);
}

function persistCaptchaPersistence() {
    chrome.storage.local.set(
        {
            [STORAGE_CAPTCHA_STATS]: {
                solved: runtimeState.captchaJobsSucceeded || 0,
                failed: runtimeState.captchaJobsFailed || 0,
            },
            [STORAGE_RECENT_JOBS]: runtimeState.recentCaptchaJobs || [],
            [STORAGE_SESSION_REFRESH_STATS]: {
                succeeded: runtimeState.sessionRefreshSucceeded || 0,
                failed: runtimeState.sessionRefreshFailed || 0,
            },
            [STORAGE_GENERATION_STATS]: {
                succeeded: runtimeState.generationJobsSucceeded || 0,
                failed: runtimeState.generationJobsFailed || 0,
            },
            [STORAGE_RECENT_GENERATION_JOBS]: runtimeState.recentGenerationJobs || [],
        },
        () => {
            if (chrome.runtime.lastError) {
                console.log("[Flow2API] persistCaptchaPersistence:", chrome.runtime.lastError.message);
            }
        }
    );
}

function persistWorkerTabId(tabId) {
    if (tabId == null || Number.isNaN(Number(tabId))) {
        runtimeState.workerTabId = null;
        chrome.storage.local.remove([STORAGE_WORKER_TAB_ID], () => {});
        return;
    }
    runtimeState.workerTabId = Number(tabId);
    chrome.storage.local.set({ [STORAGE_WORKER_TAB_ID]: runtimeState.workerTabId }, () => {});
}

function recordCaptchaJobCompletion(reqId, action, success, error) {
    if (success) {
        runtimeState.captchaJobsSucceeded = (runtimeState.captchaJobsSucceeded || 0) + 1;
    } else {
        runtimeState.captchaJobsFailed = (runtimeState.captchaJobsFailed || 0) + 1;
    }
    const list = Array.isArray(runtimeState.recentCaptchaJobs) ? runtimeState.recentCaptchaJobs : [];
    list.push({
        ts: Date.now(),
        req_id: String(reqId || ""),
        action: String(action || ""),
        ok: !!success,
        error: String(error || "").slice(0, 500),
    });
    if (list.length > RECENT_CAPTCHA_JOBS_MAX) {
        list.splice(0, list.length - RECENT_CAPTCHA_JOBS_MAX);
    }
    runtimeState.recentCaptchaJobs = list;
    persistCaptchaPersistence();
}

function recordSessionRefreshOutcome(success) {
    if (success) {
        runtimeState.sessionRefreshSucceeded = (runtimeState.sessionRefreshSucceeded || 0) + 1;
    } else {
        runtimeState.sessionRefreshFailed = (runtimeState.sessionRefreshFailed || 0) + 1;
    }
    persistCaptchaPersistence();
}

function recordGenerationJob(commandType, request, result) {
    const ok = !!(result && result.success);
    if (ok) {
        runtimeState.generationJobsSucceeded = (runtimeState.generationJobsSucceeded || 0) + 1;
    } else {
        runtimeState.generationJobsFailed = (runtimeState.generationJobsFailed || 0) + 1;
    }
    const list = Array.isArray(runtimeState.recentGenerationJobs) ? runtimeState.recentGenerationJobs : [];
    list.push({
        ts: Date.now(),
        command: String(commandType || ""),
        method: String((request && request.method) || ""),
        url: String((request && request.url) || ""),
        ok,
        status: Number((result && result.response_status) || 0),
        error: String((result && result.error) || "").slice(0, 500),
    });
    if (list.length > RECENT_CAPTCHA_JOBS_MAX) {
        list.splice(0, list.length - RECENT_CAPTCHA_JOBS_MAX);
    }
    runtimeState.recentGenerationJobs = list;
}

function loadExtensionJobAndWorkerState() {
    return new Promise((resolve) => {
        chrome.storage.local.get(
            {
                [STORAGE_CAPTCHA_STATS]: { solved: 0, failed: 0 },
                [STORAGE_RECENT_JOBS]: [],
                [STORAGE_SESSION_REFRESH_STATS]: { succeeded: 0, failed: 0 },
                [STORAGE_GENERATION_STATS]: { succeeded: 0, failed: 0 },
                [STORAGE_RECENT_GENERATION_JOBS]: [],
                [STORAGE_WORKER_TAB_ID]: null,
                workerPageUrl: DEFAULT_WORKER_PAGE_URL,
                usePersistentWorkerTab: false,
                autoRecycleWorkerTabOnCaptchaFailure: true,
            },
            (stored) => {
                const st = stored[STORAGE_CAPTCHA_STATS] || {};
                runtimeState.captchaJobsSucceeded = Number(st.solved) || 0;
                runtimeState.captchaJobsFailed = Number(st.failed) || 0;
                runtimeState.recentCaptchaJobs = normalizeRecentCaptchaJobs(stored[STORAGE_RECENT_JOBS]);
                const sr = stored[STORAGE_SESSION_REFRESH_STATS] || {};
                runtimeState.sessionRefreshSucceeded = Number(sr.succeeded) || 0;
                runtimeState.sessionRefreshFailed = Number(sr.failed) || 0;
                const gen = stored[STORAGE_GENERATION_STATS] || {};
                runtimeState.generationJobsSucceeded = Number(gen.succeeded) || 0;
                runtimeState.generationJobsFailed = Number(gen.failed) || 0;
                runtimeState.recentGenerationJobs = normalizeRecentGenerationJobs(
                    stored[STORAGE_RECENT_GENERATION_JOBS]
                );
                const wid = stored[STORAGE_WORKER_TAB_ID];
                runtimeState.workerTabId = wid != null && wid !== "" ? Number(wid) : null;
                if (runtimeState.workerTabId != null && Number.isNaN(runtimeState.workerTabId)) {
                    runtimeState.workerTabId = null;
                }
                resolve();
            }
        );
    });
}

function validateStoredWorkerTab() {
    const id = runtimeState.workerTabId;
    if (id == null) return;
    chrome.tabs.get(id, (tab) => {
        if (chrome.runtime.lastError || !tab) {
            runtimeState.workerTabId = null;
            persistWorkerTabId(null);
            pushEvent("worker_tab_gone", "Stored worker tab missing; cleared id", "warn");
        }
    });
}

/** Build http(s) base URL from captcha WebSocket URL for same-host REST uploads. */
function serverWebSocketToHttpBase(wsUrl) {
    const raw = String(wsUrl || "").trim();
    if (!raw) return "";
    try {
        const u = new URL(raw);
        const proto = u.protocol === "wss:" ? "https:" : "http:";
        let path = u.pathname || "";
        if (path.endsWith("/captcha_ws")) {
            path = path.slice(0, -"/captcha_ws".length) || "";
        }
        const origin = `${proto}//${u.host}`;
        if (!path || path === "/") return origin;
        return origin + (path.endsWith("/") ? path.slice(0, -1) : path);
    } catch {
        return "";
    }
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
        const keys = { ...DEFAULT_SETTINGS, ...DEFAULT_WORKER_SETTINGS };
        chrome.storage.local.get(keys, (stored) => {
            const connectionMode = inferConnectionMode(stored);
            resolve({
                serverUrl: normalizeWebSocketUrl((stored.serverUrl || DEFAULT_SETTINGS.serverUrl).trim()),
                connectionMode,
                apiKey: (stored.apiKey || "").trim(),
                workerAuthKey: (stored.workerAuthKey || "").trim(),
                routeKey: (stored.routeKey || "").trim(),
                clientLabel: (stored.clientLabel || "").trim(),
                workerPageUrl: normalizeWorkerPageUrl(stored.workerPageUrl),
                usePersistentWorkerTab: !!stored.usePersistentWorkerTab,
                autoRecycleWorkerTabOnCaptchaFailure:
                    stored.autoRecycleWorkerTabOnCaptchaFailure !== false,
                workerRecaptchaSettleMs: clampWorkerRecaptchaSettleMs(stored.workerRecaptchaSettleMs),
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

function normalizeFlowSessionTokenHistory(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    for (const entry of raw) {
        if (!entry || typeof entry !== "object") continue;
        const sessionToken = String(entry.sessionToken || "").trim();
        if (!sessionToken) continue;
        const capturedAt = Number(entry.capturedAt) || 0;
        out.push({ capturedAt, sessionToken });
        if (out.length >= FLOW_SESSION_TOKEN_HISTORY_MAX) break;
    }
    return out.slice(0, FLOW_SESSION_TOKEN_HISTORY_MAX);
}

function loadFlowSessionTokenHistoryFromStorage() {
    return new Promise((resolve) => {
        chrome.storage.local.get({ [FLOW_SESSION_TOKEN_HISTORY_KEY]: [] }, (stored) => {
            const raw = stored[FLOW_SESSION_TOKEN_HISTORY_KEY];
            runtimeState.flowSessionTokenHistory = normalizeFlowSessionTokenHistory(raw);
            resolve();
        });
    });
}

function recordCapturedFlowSessionToken(sessionToken) {
    const token = String(sessionToken || "").trim();
    if (!token) return;
    const prev = Array.isArray(runtimeState.flowSessionTokenHistory)
        ? runtimeState.flowSessionTokenHistory
        : [];
    if (prev[0] && String(prev[0].sessionToken || "") === token) return;
    const next = [{ capturedAt: Date.now(), sessionToken: token }, ...prev].slice(
        0,
        FLOW_SESSION_TOKEN_HISTORY_MAX
    );
    runtimeState.flowSessionTokenHistory = next;
    chrome.storage.local.set({ [FLOW_SESSION_TOKEN_HISTORY_KEY]: next }, () => {
        if (chrome.runtime.lastError) {
            console.log("[Flow2API] flowSessionTokenHistory persist failed:", chrome.runtime.lastError.message);
        }
    });
}

function stopWorkerSessionRefreshScheduler() {
    if (sessionRefreshTimeout) clearTimeout(sessionRefreshTimeout);
    sessionRefreshTimeout = null;
    runtimeState.sessionRefreshNextAt = 0;
    runtimeState.sessionRefreshInFlight = false;
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
            recordCapturedFlowSessionToken(result.sessionToken);
            recordSessionRefreshOutcome(true);
            if (reqId && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    req_id: reqId,
                    status: "success",
                    session_token: result.sessionToken
                }));
            }
            return { success: true, sessionToken: result.sessionToken, reason: refreshReason };
        }

        const errorCode = result.error || "session_refresh_failed";
        runtimeState.sessionRefreshLastFailureAt = Date.now();
        runtimeState.sessionRefreshLastError = errorCode;
        runtimeState.sessionRefreshConsecutiveFailures += 1;
        pushEvent("session_refresh_error", `Session refresh failed (${refreshReason}): ${errorCode}`, "warn");
        recordSessionRefreshOutcome(false);
        if (reqId && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                req_id: reqId,
                status: "error",
                error: errorCode
            }));
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
    runtimeState.flowSessionTokenHistory = [];
    runtimeState.captchaJobsSucceeded = 0;
    runtimeState.captchaJobsFailed = 0;
    runtimeState.recentCaptchaJobs = [];
    runtimeState.sessionRefreshSucceeded = 0;
    runtimeState.sessionRefreshFailed = 0;
    runtimeState.workerTabId = null;
    runtimeState.generationJobsSucceeded = 0;
    runtimeState.generationJobsFailed = 0;
    runtimeState.recentGenerationJobs = [];
    runtimeState.generationInFlight = false;
    runtimeState.generationLastPollFallbackReason = "";
    runtimeState.allowCaptcha = true;
    runtimeState.allowSessionRefresh = true;
}

async function closeWorkerTabIfAny() {
    const id = runtimeState.workerTabId;
    if (id == null) return;
    try {
        await chrome.tabs.remove(id);
    } catch (e) {
        console.log("[Flow2API] closeWorkerTabIfAny:", e);
    }
    runtimeState.workerTabId = null;
    persistWorkerTabId(null);
}

/** Clear saved settings, drop stable instance id, and reconnect (used by options Reset). */
function resetExtensionToDefaults(done) {
    cachedInstanceId = null;
    resetRuntimeStatePartial();
    closeSocket();
    closeWorkerTabIfAny().finally(() => {
        chrome.storage.local.remove(
            ["extensionInstanceId", FLOW_SESSION_TOKEN_HISTORY_KEY, STORAGE_WORKER_TAB_ID],
            () => {
                chrome.storage.local.set(
                    {
                        serverUrl: DEFAULT_SETTINGS.serverUrl,
                        connectionMode: DEFAULT_SETTINGS.connectionMode,
                        apiKey: DEFAULT_SETTINGS.apiKey,
                        workerAuthKey: DEFAULT_SETTINGS.workerAuthKey,
                        routeKey: DEFAULT_SETTINGS.routeKey,
                        clientLabel: DEFAULT_SETTINGS.clientLabel,
                        workerPageUrl: DEFAULT_WORKER_PAGE_URL,
                        usePersistentWorkerTab: false,
                        autoRecycleWorkerTabOnCaptchaFailure: true,
                        workerRecaptchaSettleMs: WORKER_RECAPTCHA_SETTLE_DEFAULT_MS,
                        [STORAGE_CAPTCHA_STATS]: { solved: 0, failed: 0 },
                        [STORAGE_RECENT_JOBS]: [],
                        [STORAGE_SESSION_REFRESH_STATS]: { succeeded: 0, failed: 0 },
                        [STORAGE_GENERATION_STATS]: { succeeded: 0, failed: 0 },
                        [STORAGE_RECENT_GENERATION_JOBS]: [],
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
            }
        );
    });
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function clampWorkerRecaptchaSettleMs(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return WORKER_RECAPTCHA_SETTLE_DEFAULT_MS;
    const i = Math.floor(n);
    if (i < 0) return 0;
    if (i > WORKER_RECAPTCHA_SETTLE_MAX_MS) return WORKER_RECAPTCHA_SETTLE_MAX_MS;
    return i;
}

/** After worker tab reaches `complete`, wait for enterprise.js / grecaptcha to be usable. */
async function sleepWorkerRecaptchaSettle(settings) {
    const ms = clampWorkerRecaptchaSettleMs(settings && settings.workerRecaptchaSettleMs);
    if (ms > 0) await sleep(ms);
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

/** True if the tab is already on Google Labs Flow (SPA may change path/query; do not force-reload). */
function isLabsFlowWorkerSurface(tabUrl) {
    const t = String(tabUrl || "").trim();
    if (!t || t.startsWith("chrome://")) return false;
    try {
        const u = new URL(t);
        if (u.hostname.toLowerCase() !== "labs.google") {
            return false;
        }
        const p = u.pathname || "";
        return p === "/" || p.startsWith("/fx");
    } catch {
        return false;
    }
}

async function executeRecaptchaScriptInTab(tabId, action) {
    const scriptTimeoutMs = action === "VIDEO_GENERATION" ? 30000 : 20000;
    let lastErrorMsg = "No response from tab.";
    try {
        const results = await chrome.scripting.executeScript({
            target: { tabId },
            world: "MAIN",
            func: async (actionArg, timeoutMs) => {
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
                                grecaptcha.enterprise.execute("6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV", { action: actionArg })
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
            return { success: true, token: results[0].result };
        }
    } catch (e) {
        lastErrorMsg = e.message || "Script execution failed";
    }
    return { success: false, error: "Extension script failed: " + lastErrorMsg };
}

async function generateTokenInFreshTab(action, pageUrl) {
    const url = normalizeWorkerPageUrl(pageUrl);
    let newTabId = null;
    try {
        console.log("[Flow2API] Opening fresh Labs tab for captcha:", url);
        const newTab = await chrome.tabs.create({ url, active: false });
        newTabId = newTab.id;

        await waitForTabReady(newTabId);
        const settleSettings = await getSettings();
        await sleepWorkerRecaptchaSettle(settleSettings);

        const execResult = await executeRecaptchaScriptInTab(newTabId, action);
        if (execResult.success) {
            runtimeState.lastError = "";
            return { success: true, token: execResult.token };
        }
        runtimeState.lastError = execResult.error;
        return { success: false, error: execResult.error };
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

async function reopenWorkerLabsPageAfterUpstreamRejection(settings, reason) {
    const pageUrl = normalizeWorkerPageUrl(settings.workerPageUrl);
    const auto = settings.autoRecycleWorkerTabOnCaptchaFailure !== false;
    if (!auto) {
        pushEvent("upstream_captcha_recycle_skipped", "autoRecycleWorkerTabOnCaptchaFailure disabled", "warn");
        return;
    }
    if (settings.usePersistentWorkerTab) {
        await recyclePersistentWorkerTab(settings, reason || "upstream_captcha_rejected");
        return;
    }
    const tabId = runtimeState.workerTabId;
    if (tabId != null) {
        try {
            await chrome.tabs.remove(tabId);
        } catch (_) {}
        runtimeState.workerTabId = null;
        persistWorkerTabId(null);
        try {
            const newTab = await chrome.tabs.create({ url: pageUrl, active: false });
            if (newTab && newTab.id) {
                runtimeState.workerTabId = newTab.id;
                persistWorkerTabId(newTab.id);
                await waitForTabReady(newTab.id);
                await sleepWorkerRecaptchaSettle(settings);
                pushEvent("worker_tab_recycled", `Worker tab replaced after upstream captcha (${reason || "upstream_captcha"})`);
            }
        } catch (e) {
            pushEvent("worker_page_open_failed", String(e && e.message ? e.message : e), "error");
        }
        return;
    }
    try {
        const tab = await chrome.tabs.create({ url: pageUrl, active: false });
        if (tab && tab.id) {
            await waitForTabReady(tab.id);
            await sleepWorkerRecaptchaSettle(settings);
            pushEvent("worker_page_opened", `Opened Labs tab after upstream captcha rejection (${reason || "upstream"})`);
        }
    } catch (e) {
        pushEvent("worker_page_open_failed", String(e && e.message ? e.message : e), "error");
    }
}

async function handleCaptchaUpstreamVerdict(data) {
    const accepted = !!data.accepted;
    const captchaRejected = !!data.captcha_rejected;
    const reqId = String(data.req_id || "");
    const detail = String(data.detail || "").trim();
    pushEvent(
        "captcha_upstream_verdict",
        `req=${reqId} accepted=${accepted} captcha_rejected=${captchaRejected}${detail ? ` detail=${detail.slice(0, 160)}` : ""}`
    );
    if (accepted || !captchaRejected) {
        return;
    }
    const settings = await getSettings();
    await reopenWorkerLabsPageAfterUpstreamRejection(settings, detail || "upstream_captcha_rejected");
}

async function recyclePersistentWorkerTab(settings, reason) {
    const oldId = runtimeState.workerTabId;
    if (oldId != null) {
        try {
            await chrome.tabs.remove(oldId);
        } catch (_) {}
    }
    runtimeState.workerTabId = null;
    persistWorkerTabId(null);
    pushEvent("worker_tab_recycled", `Worker tab recycled (${reason})`);
    const pageUrl = normalizeWorkerPageUrl(settings.workerPageUrl);
    const newTab = await chrome.tabs.create({ url: pageUrl, active: false });
    const newId = newTab.id;
    runtimeState.workerTabId = newId;
    persistWorkerTabId(newId);
    await waitForTabReady(newId);
    await sleepWorkerRecaptchaSettle(settings);
    return newId;
}

async function ensurePersistentWorkerTab(settings) {
    const pageUrl = normalizeWorkerPageUrl(settings.workerPageUrl);
    let tabId = runtimeState.workerTabId;

    if (tabId != null) {
        const tab = await new Promise((resolve) => {
            chrome.tabs.get(tabId, (t) => {
                if (chrome.runtime.lastError) resolve(null);
                else resolve(t);
            });
        });
        if (!tab) {
            tabId = null;
            runtimeState.workerTabId = null;
            persistWorkerTabId(null);
        } else {
            const currentUrl = tab.url || tab.pendingUrl || "";
            // Only navigate away from a wrong site. Do not reset the tab on every captcha job when
            // Labs/Flow has already moved the SPA to another /fx/... URL (that looked like a refresh).
            if (!isLabsFlowWorkerSurface(currentUrl)) {
                await new Promise((resolve) => {
                    chrome.tabs.update(tabId, { url: pageUrl }, () => resolve());
                });
                await waitForTabReady(tabId);
                await sleepWorkerRecaptchaSettle(settings);
            } else {
                // Same Labs/Flow surface as worker URL: no navigation — wait for load if needed, skip settle delay.
                await waitForTabReady(tabId);
            }
            return tabId;
        }
    }

    console.log("[Flow2API] Creating persistent worker tab:", pageUrl);
    const newTab = await chrome.tabs.create({ url: pageUrl, active: false });
    tabId = newTab.id;
    runtimeState.workerTabId = tabId;
    persistWorkerTabId(tabId);
    await waitForTabReady(tabId);
    await sleepWorkerRecaptchaSettle(settings);
    pushEvent("worker_tab_created", `Worker tab created (${pageUrl})`);
    return tabId;
}

async function generateTokenWithPersistentTab(action, settings) {
    try {
        let tabId = await ensurePersistentWorkerTab(settings);
        const execResult = await executeRecaptchaScriptInTab(tabId, action);
        if (execResult.success) {
            runtimeState.lastError = "";
            return { success: true, token: execResult.token };
        }
        runtimeState.lastError = execResult.error;
        if (settings.autoRecycleWorkerTabOnCaptchaFailure) {
            tabId = await recyclePersistentWorkerTab(settings, "captcha_failure");
        }
        return { success: false, error: execResult.error };
    } catch (err) {
        const msg = err.message || "unknown_error";
        runtimeState.lastError = msg;
        if (settings.autoRecycleWorkerTabOnCaptchaFailure && runtimeState.workerTabId != null) {
            try {
                await recyclePersistentWorkerTab(settings, "captcha_exception");
            } catch (_) {}
        }
        return { success: false, error: msg };
    }
}

async function generateTokenForCaptcha(action) {
    const settings = await getSettings();
    if (settings.usePersistentWorkerTab) {
        return generateTokenWithPersistentTab(action, settings);
    }
    return generateTokenInFreshTab(action, settings.workerPageUrl);
}

/**
 * Page-origin fetch to aisandbox from https://labs.google/* is CORS-restricted.
 * The server only allows a small set of request headers on preflight. Flow2API's
 * server-side client adds custom x-browser-* headers for curl_cffi; those must not
 * be forwarded into fetch() or the OPTIONS preflight fails (no ACAO / wrong ACAH).
 */
function filterHeadersForLabsGoogleCors(rawHeaders) {
    const allow = new Set([
        "authorization",
        "content-type",
        "accept",
        "accept-language",
        "user-agent",
        "referer",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
    ]);
    const out = {};
    if (!rawHeaders || typeof rawHeaders !== "object") return out;
    for (const [k, v] of Object.entries(rawHeaders)) {
        if (v == null || v === "") continue;
        const key = String(k).trim();
        if (!key) continue;
        if (!allow.has(key.toLowerCase())) continue;
        out[key] = String(v);
    }
    if (!out.Referer && !out.referer) {
        out["Referer"] = "https://labs.google/";
    }
    return out;
}

async function executeHttpRequestInTab(tabId, request) {
    const scriptTimeoutMs = Math.max(15000, Math.min(120000, Number(request.timeout_ms) || 60000));
    const requestForPage = {
        ...request,
        headers: filterHeadersForLabsGoogleCors(request.headers || {}),
    };
    try {
        const results = await chrome.scripting.executeScript({
            target: { tabId },
            world: "MAIN",
            func: async (req, timeoutMs) => {
                const ctl = new AbortController();
                const timer = setTimeout(() => ctl.abort(), timeoutMs);
                try {
                    const method = String(req.method || "POST").toUpperCase();
                    const headers = req.headers && typeof req.headers === "object" ? { ...req.headers } : {};
                    const hasJson = req.json_data && typeof req.json_data === "object";
                    const init = { method, headers, signal: ctl.signal };
                    if (hasJson) {
                        if (!init.headers["Content-Type"] && !init.headers["content-type"]) {
                            init.headers["Content-Type"] = "application/json";
                        }
                        init.body = JSON.stringify(req.json_data);
                    }
                    const resp = await fetch(String(req.url || ""), init);
                    const text = await resp.text();
                    let parsed = null;
                    try {
                        parsed = text ? JSON.parse(text) : null;
                    } catch (_) {}
                    return {
                        ok: !!resp.ok,
                        status: Number(resp.status) || 0,
                        response_text: String(text || ""),
                        response_json: parsed,
                    };
                } catch (e) {
                    return {
                        ok: false,
                        status: 0,
                        error: String((e && e.message) || e || "request_failed"),
                        response_text: "",
                        response_json: null,
                    };
                } finally {
                    clearTimeout(timer);
                }
            },
            args: [requestForPage, scriptTimeoutMs],
        });
        const payload = results && results[0] ? results[0].result : null;
        if (!payload || typeof payload !== "object") {
            return { success: false, error: "empty_extension_http_response" };
        }
        if (!payload.ok) {
            const err = payload.error || `HTTP ${payload.status || 0}`;
            return {
                success: false,
                error: String(err),
                response_status: Number(payload.status) || 0,
                response_text: String(payload.response_text || ""),
                response_json: payload.response_json || null,
            };
        }
        return {
            success: true,
            response_status: Number(payload.status) || 200,
            response_text: String(payload.response_text || ""),
            response_json: payload.response_json || null,
        };
    } catch (e) {
        return { success: false, error: String((e && e.message) || e || "script_execution_failed") };
    }
}

async function executeGenerationHttpRequest(request) {
    const settings = await getSettings();
    if (settings.usePersistentWorkerTab) {
        const tabId = await ensurePersistentWorkerTab(settings);
        return executeHttpRequestInTab(tabId, request);
    }
    let tempTabId = null;
    try {
        const tab = await chrome.tabs.create({ url: normalizeWorkerPageUrl(settings.workerPageUrl), active: false });
        tempTabId = tab && tab.id ? tab.id : null;
        if (!tempTabId) {
            return { success: false, error: "worker_tab_create_failed" };
        }
        await waitForTabReady(tempTabId);
        await sleepWorkerRecaptchaSettle(settings);
        return await executeHttpRequestInTab(tempTabId, request);
    } finally {
        if (tempTabId) {
            try {
                await chrome.tabs.remove(tempTabId);
            } catch (_) {}
        }
    }
}

async function handleGenerationRequest(data, commandType) {
    const request = {
        url: String(data.url || "").trim(),
        method: String(data.method || "POST").trim().toUpperCase(),
        headers: data.headers && typeof data.headers === "object" ? data.headers : {},
        json_data: data.json_data && typeof data.json_data === "object" ? data.json_data : {},
        timeout_ms: Number(data.timeout_ms) || 60000,
    };
    if (!request.url) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                req_id: data.req_id,
                type: `${commandType}_result`,
                status: "error",
                error: "missing_url",
            }));
        }
        return;
    }
    runtimeState.generationInFlight = true;
    const result = await executeGenerationHttpRequest(request);
    recordGenerationJob(commandType, request, result);
    if (commandType === "poll_generation" && !result.success) {
        runtimeState.generationLastPollFallbackReason = String(result.error || "poll_generation_failed");
    }
    if (commandType === "poll_generation" && result.success) {
        runtimeState.generationLastPollFallbackReason = "";
    }
    runtimeState.generationInFlight = false;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const lr = data.large_response_upload && typeof data.large_response_upload === "object"
        ? data.large_response_upload
        : null;
    if (result.success) {
        const rt = String(result.response_text || "");
        const threshold =
            lr && lr.threshold_bytes != null ? Number(lr.threshold_bytes) : 524288;
        const force = lr && !!lr.force_http_upload;
        const shouldHttp =
            lr &&
            rt &&
            (force || (Number.isFinite(threshold) && rt.length >= threshold));
        let successPayload = {
            req_id: data.req_id,
            type: `${commandType}_result`,
            status: "success",
            response_status: result.response_status,
            response_text: result.response_text,
            response_json: result.response_json,
            upload_status: shouldHttp ? "pending" : "not_required",
        };
        if (shouldHttp) {
            const settings = await getSettings();
            const base = serverWebSocketToHttpBase(settings.serverUrl || DEFAULT_SETTINGS.serverUrl);
            const path = String(lr.upload_path || "/api/extension/generation-upload").trim() || "/api/extension/generation-upload";
            let uploadTarget = "";
            try {
                uploadTarget = new URL(path.startsWith("/") ? path : `/${path}`, `${base}/`).toString();
            } catch (e) {
                uploadTarget = "";
            }
            if (!uploadTarget || !lr.upload_id || !lr.upload_secret) {
                pushEvent("generation_upload_bad_config", "large_response_upload missing fields or bad URL base", "error");
                successPayload.upload_status = "failed";
                successPayload.upload_error = "generation_upload_bad_target";
                pushEvent(
                    "generation_upload_forwarded_with_warning",
                    `req_id=${String(data.req_id || "-")} upload_target_invalid upstream_status=${String(result.response_status || 0)}`,
                    "warning"
                );
            } else {
                const u = new URL(uploadTarget);
                u.searchParams.set("upload_id", String(lr.upload_id));
                u.searchParams.set("upload_secret", String(lr.upload_secret));
                try {
                    const up = await fetch(u.toString(), {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: rt,
                    });
                    if (!up.ok) {
                        const errText = await up.text().catch(() => "");
                        successPayload.upload_status = "failed";
                        successPayload.upload_error = `generation_upload_http_failed:${up.status}:${(errText || "").slice(0, 200)}`;
                        pushEvent(
                            "generation_upload_forwarded_with_warning",
                            `req_id=${String(data.req_id || "-")} upload_http_failed=${String(up.status)} upstream_status=${String(result.response_status || 0)}`,
                            "warning"
                        );
                    } else {
                        successPayload = {
                            req_id: data.req_id,
                            type: `${commandType}_result`,
                            status: "success",
                            response_status: result.response_status,
                            large_response_upload_id: String(lr.upload_id),
                            upload_status: "uploaded",
                        };
                        pushEvent(
                            "generation_upload_forwarded",
                            `req_id=${String(data.req_id || "-")} upload_id=${String(lr.upload_id)} upstream_status=${String(result.response_status || 0)}`
                        );
                    }
                } catch (e) {
                    successPayload.upload_status = "failed";
                    successPayload.upload_error = `generation_upload_fetch_error:${String((e && e.message) || e || "err")}`.slice(0, 400);
                    pushEvent(
                        "generation_upload_forwarded_with_warning",
                        `req_id=${String(data.req_id || "-")} upload_fetch_failed upstream_status=${String(result.response_status || 0)}`,
                        "warning"
                    );
                }
            }
        }
        if (!shouldHttp) {
            pushEvent(
                "generation_forwarded_direct",
                `req_id=${String(data.req_id || "-")} upstream_status=${String(result.response_status || 0)}`
            );
        }
        ws.send(JSON.stringify(successPayload));
    } else {
        ws.send(JSON.stringify({
            req_id: data.req_id,
            type: `${commandType}_result`,
            status: "error",
            error: result.error || "generation_request_failed",
            response_status: result.response_status || 0,
            response_text: result.response_text || "",
            response_json: result.response_json || null,
        }));
    }
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
            const ac = data.allow_captcha;
            const ar = data.allow_session_refresh;
            runtimeState.allowCaptcha = ac !== false && ac !== 0 && ac !== "0";
            runtimeState.allowSessionRefresh = ar !== false && ar !== 0 && ar !== "0";
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
                    runtimeState.bindingSource || "-",
                    "allowCaptcha=",
                    runtimeState.allowCaptcha,
                    "allowSessionRefresh=",
                    runtimeState.allowSessionRefresh
                );
                stopWorkerSessionRefreshScheduler();
            }
            return;
        }

        if (data.type === "captcha_upstream_verdict") {
            tokenQueue = tokenQueue.then(() => handleCaptchaUpstreamVerdict(data)).catch(err => {
                console.error("[Flow2API] captcha_upstream_verdict error:", err);
            });
            return;
        }

        if (data.type === "get_token") {
            tokenQueue = tokenQueue.then(() => handleGetToken(data)).catch(err => {
                console.error("[Flow2API] Queue Error:", err);
            });
            return;
        }
        if (data.type === "submit_generation") {
            tokenQueue = tokenQueue.then(() => handleGenerationRequest(data, "submit_generation")).catch(err => {
                console.error("[Flow2API] submit_generation queue error:", err);
            });
            return;
        }
        if (data.type === "poll_generation") {
            tokenQueue = tokenQueue.then(() => handleGenerationRequest(data, "poll_generation")).catch(err => {
                console.error("[Flow2API] poll_generation queue error:", err);
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

async function handleGetToken(data) {
    if (runtimeState.connectionMode === "worker" && runtimeState.allowCaptcha === false) {
        if (ws && ws.readyState === WebSocket.OPEN && data.req_id) {
            ws.send(
                JSON.stringify({
                    req_id: data.req_id,
                    status: "error",
                    error: "captcha_disabled_for_worker_key",
                })
            );
        }
        pushEvent("get_token_blocked", "Captcha disabled for this worker key", "warn");
        return;
    }
    const action = data.action || "IMAGE_GENERATION";
    const result = await generateTokenForCaptcha(action);
    recordCaptchaJobCompletion(data.req_id, action, result.success, result.error || "");
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
    if (runtimeState.connectionMode === "worker" && runtimeState.allowSessionRefresh === false) {
        if (ws && ws.readyState === WebSocket.OPEN && data && data.req_id) {
            ws.send(
                JSON.stringify({
                    req_id: data.req_id,
                    status: "error",
                    error: "session_refresh_disabled_for_worker_key",
                })
            );
        }
        pushEvent("refresh_st_blocked", "Session refresh disabled for this worker key", "warn");
        return;
    }
    await performSessionRefresh({ reason: "server_request", reqId: data && data.req_id ? data.req_id : null });
}

chrome.tabs.onRemoved.addListener((tabId) => {
    if (runtimeState.workerTabId != null && tabId === runtimeState.workerTabId) {
        runtimeState.workerTabId = null;
        persistWorkerTabId(null);
        pushEvent("worker_tab_removed", "Worker tab closed (browser)", "warn");
    }
});

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

function mergeStateForStatus(settings) {
    return {
        ...runtimeState,
        workerPageUrl: settings.workerPageUrl,
        usePersistentWorkerTab: settings.usePersistentWorkerTab,
        autoRecycleWorkerTabOnCaptchaFailure: settings.autoRecycleWorkerTabOnCaptchaFailure,
        workerRecaptchaSettleMs: settings.workerRecaptchaSettleMs,
    };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || !message.type) return;
    if (message.type === "get_status") {
        getSettings().then((s) => {
            sendResponse({ success: true, state: mergeStateForStatus(s) });
        });
        return true;
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
        generateTokenForCaptcha(message.action || "IMAGE_GENERATION")
            .then((result) => sendResponse(result))
            .catch((err) => sendResponse({ success: false, error: err.message || "test_failed" }));
        return true;
    }
    if (message.type === "worker_tab_open") {
        getSettings()
            .then(async (s) => {
                if (!s.usePersistentWorkerTab) {
                    sendResponse({ success: false, error: "enable_persistent_worker_tab_first" });
                    return;
                }
                const tabId = await ensurePersistentWorkerTab(s);
                sendResponse({ success: true, tabId });
            })
            .catch((err) => sendResponse({ success: false, error: err.message || "worker_tab_open_failed" }));
        return true;
    }
    if (message.type === "worker_tab_close") {
        closeWorkerTabIfAny()
            .then(() => sendResponse({ success: true }))
            .catch((err) => sendResponse({ success: false, error: err.message || "worker_tab_close_failed" }));
        return true;
    }
    if (message.type === "worker_tab_recycle") {
        getSettings()
            .then(async (s) => {
                if (!s.usePersistentWorkerTab) {
                    sendResponse({ success: false, error: "enable_persistent_worker_tab_first" });
                    return;
                }
                const tabId = await recyclePersistentWorkerTab(s, "manual");
                sendResponse({ success: true, tabId });
            })
            .catch((err) => sendResponse({ success: false, error: err.message || "worker_tab_recycle_failed" }));
        return true;
    }
});

Promise.all([loadFlowSessionTokenHistoryFromStorage(), loadExtensionJobAndWorkerState()]).then(() => {
    validateStoredWorkerTab();
    pushEvent("startup", "Background worker started");
    connectWS();
});
