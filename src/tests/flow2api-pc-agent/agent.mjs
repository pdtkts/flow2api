#!/usr/bin/env node
/**
 * Real PC captcha agent: connects to agent-gateway, runs Playwright to obtain
 * reCAPTCHA Enterprise token (same pattern as Flow2API in-process browser_captcha
 * for browser_captcha_page_url + grecaptcha.enterprise.execute).
 *
 * 1) npm install
 * 2) npx playwright install chromium
 * 3) Edit FILE_CONFIG (wss, agentToken, tokenIds, startUrl, websiteKey).
 * 4) npm start. Default startUrl is Labs “providers” — that URL returns JSON in a real
 *    browser, so this agent mirrors browser_captcha.py: stub page + enterprise.js
 *    (not JSON-only navigation).
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import crypto from "node:crypto";

import { chromium } from "playwright";
import WebSocket from "ws";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Same as Python BROWSER_CAPTCHA_DEFAULT_PAGE_URL — must use stub (HTML inject), not raw JSON. */
const BROWSER_CAPTCHA_DEFAULT_PAGE_URL = "https://labs.google/fx/api/auth/providers";

function isStubProvidersPage(url) {
  try {
    const a = new URL(url.trim());
    const b = new URL(BROWSER_CAPTCHA_DEFAULT_PAGE_URL);
    return (
      a.origin + a.pathname.replace(/\/$/, "") ===
      b.origin + b.pathname.replace(/\/$/, "")
    );
  } catch {
    return false;
  }
}

/**
 * In-process Flow2API serves synthetic HTML for this URL so grecaptcha loads; we do the same.
 * @param {string} websiteKey
 */
function stubPageHtml(websiteKey) {
  const primary = "https://www.google.com";
  const secondary = "https://www.recaptcha.net";
  return `<!doctype html><html><head><script>
(function(){
  const urls = [
    "${primary}/recaptcha/enterprise.js?render=${websiteKey}",
    "${secondary}/recaptcha/enterprise.js?render=${websiteKey}"
  ];
  function load(i){
    if (i >= urls.length) return;
    var s = document.createElement("script");
    s.src = urls[i];
    s.async = true;
    s.onerror = function() { load(i + 1); };
    document.head.appendChild(s);
  }
  load(0);
})();
</script></head><body></body></html>`;
}

function parseTokenIdsFromEnv() {
  const raw = (process.env.AGENT_TOKEN_IDS || "").trim();
  if (!raw) {
    return null;
  }
  const ns = raw
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => !Number.isNaN(n));
  return ns.length ? ns : null;
}

/**
 * Accepts AGENT_TOKEN in either:
 * - raw token string
 * - JSON object string containing { key, licenseId, machineId, ... } from a client API response
 * The gateway only verifies the token value itself; ids are useful for app-side bookkeeping.
 */
function parseAgentTokenFromEnvOrConfig() {
  const raw = (process.env.AGENT_TOKEN || FILE_CONFIG.agentToken || "").trim();
  if (!raw) {
    return "";
  }
  if (raw.startsWith("{")) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && typeof parsed.key === "string" && parsed.key.trim()) {
        return parsed.key.trim();
      }
    } catch {
      // fall through to raw handling
    }
  }
  return raw;
}

/** Default config — override or use env: AGENT_TOKEN, AGENT_GATEWAY_WSS, AGENT_TOKEN_IDS */
const FILE_CONFIG = {
  // WebSocket to agent-gateway (HTTPS https://agents.prismacreative.online/ is the same host; path is /ws/agents)
  wss: "wss://agents.prismacreative.online/ws/agents",
  // Keygen-derived agent token (jwt/introspection token)
  agentToken: "activ-b357ff2159b5f3b81e1fbae559697fb1v3",
  // Optional machine/license hint (server may ignore; useful for debugging/audit trails).
  agentId: "",
  tokenIds: [1],
  userDataDir: path.join(__dirname, ".pc-agent-profile"),
  startUrl: "https://labs.google/fx/api/auth/providers",
  websiteKey: "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV",
  headless: false,
  settleMs: 3000,
};

const CONFIG = {
  ...FILE_CONFIG,
  wss: (process.env.AGENT_GATEWAY_WSS || FILE_CONFIG.wss).trim(),
  agentToken: parseAgentTokenFromEnvOrConfig(),
  agentId: (process.env.AGENT_ID || FILE_CONFIG.agentId || "").trim(),
  tokenIds: parseTokenIdsFromEnv() ?? FILE_CONFIG.tokenIds,
};

let _context;
let _playwrightBusy = Promise.resolve();

function enqueue(fn) {
  const next = _playwrightBusy.then(() => fn());
  _playwrightBusy = next.catch(() => {});
  return next;
}

async function ensureContext() {
  if (_context) {
    return _context;
  }
  _context = await chromium.launchPersistentContext(CONFIG.userDataDir, {
    headless: CONFIG.headless,
    channel: "chromium",
    args: ["--disable-blink-features=AutomationControlled"],
  });
  return _context;
}

/**
 * @param {{ projectId: string, action: string, tokenId: number }} _job
 */
async function runRecaptchaSolve(_job) {
  const context = await ensureContext();
  const page = await context.newPage();
  const start = CONFIG.startUrl.trim();
  const useStub = isStubProvidersPage(start);
  try {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });
    if (useStub) {
      const pageUrl = BROWSER_CAPTCHA_DEFAULT_PAGE_URL;
      await page.route("**/*", (route) => {
        const u = route.request().url();
        if (u.split("?")[0].replace(/\/$/, "") === pageUrl.split("?")[0].replace(/\/$/, "")) {
          return route.fulfill({
            status: 200,
            contentType: "text/html; charset=utf-8",
            body: stubPageHtml(CONFIG.websiteKey),
          });
        }
        if (["google.com", "gstatic.com", "recaptcha.net", "googleapis.com"].some((d) => u.includes(d))) {
          return route.continue();
        }
        return route.abort();
      });
      await page.goto(pageUrl, { waitUntil: "load", timeout: 20_000 });
    } else {
      await page.goto(start, { waitUntil: "domcontentloaded", timeout: 60_000 });
    }
    await page.waitForFunction(
      () =>
        typeof grecaptcha !== "undefined" &&
        grecaptcha.enterprise &&
        typeof grecaptcha.enterprise.execute === "function",
      { timeout: 30_000 }
    );
    const token = await page.evaluate(
      ({ key, actionName }) => {
        return grecaptcha.enterprise.execute(key, { action: actionName });
      },
      { key: CONFIG.websiteKey, actionName: _job.action }
    );
    if (typeof token !== "string" || !token) {
      throw new Error("empty reCAPTCHA token from enterprise.execute");
    }
    if (CONFIG.settleMs > 0) {
      await new Promise((r) => setTimeout(r, CONFIG.settleMs));
    }
    const userAgent = await page.evaluate(() => navigator.userAgent);
    return { token, userAgent };
  } finally {
    try {
      await page.close();
    } catch {
      // ignore
    }
  }
}

function sendJson(ws, obj) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function main() {
  if (!CONFIG.agentToken) {
    console.error(
      "Missing AGENT_TOKEN. Use one of:\n" +
        "  PowerShell:  $env:AGENT_TOKEN=\"<keygen token>\"; npm start\n" +
        "  PowerShell:  $env:AGENT_TOKEN='{\"key\":\"UTX4-...\",\"licenseId\":\"...\",\"machineId\":\"...\"}'; npm start\n" +
        "  bash:        export AGENT_TOKEN=... && npm start\n" +
        "  Or set FILE_CONFIG.agentToken in agent.mjs"
    );
    process.exit(1);
  }
  if (!Array.isArray(CONFIG.tokenIds) || CONFIG.tokenIds.length === 0) {
    console.error("Set FILE_CONFIG.tokenIds or AGENT_TOKEN_IDS (e.g. 1 or 1,2)");
    process.exit(1);
  }

  const ws = new WebSocket(CONFIG.wss, { handshakeTimeout: 20_000 });

  ws.on("open", () => {
    const reg = {
      type: "register",
      agent_token: CONFIG.agentToken,
      agent_id: CONFIG.agentId,
      token_ids: CONFIG.tokenIds,
    };
    ws.send(JSON.stringify(reg));
    console.log("connected → register claimed token_ids", reg.token_ids);
  });

  ws.on("message", (data) => {
    let msg;
    try {
      msg = JSON.parse(String(data));
    } catch {
      return;
    }
    if (msg.type === "registered") {
      const authorized = Array.isArray(msg.authorized_token_ids)
        ? msg.authorized_token_ids
        : msg.token_ids;
      console.log("registered", {
        auth_method: msg.auth_method || "unknown",
        subject: msg.subject || "",
        authorized_token_ids: authorized || [],
      });
      return;
    }
    if (msg.type === "error" && msg.detail) {
      console.error("server:", msg.detail);
      return;
    }
    if (msg.type === "solve_job") {
      const jobId = msg.job_id;
      const projectId = String(msg.project_id || "");
      const action = String(msg.action || "IMAGE_GENERATION");
      const tokenId = Number(msg.token_id);
      console.log("solve_job", { jobId, projectId, action, tokenId });
      void enqueue(async () => {
        try {
          const { token, userAgent } = await runRecaptchaSolve({ projectId, action, tokenId });
          const sessionId = crypto.randomUUID();
          sendJson(ws, {
            type: "solve_result",
            job_id: jobId,
            token,
            session_id: sessionId,
            fingerprint: { user_agent: userAgent, source: "flow2api-pc-agent" },
          });
          console.log("→ solve_result", jobId);
        } catch (e) {
          const err = e instanceof Error ? e.message : String(e);
          sendJson(ws, { type: "solve_error", job_id: jobId, error: err });
          console.error("solve_error", jobId, err);
        }
      });
    }
  });

  ws.on("close", (code, reason) => {
    console.log("ws closed", code, reason.toString());
    process.exit(code === 1000 || code === 1005 ? 0 : 1);
  });
  ws.on("error", (e) => {
    console.error("ws error", e.message);
  });

  process.on("SIGINT", async () => {
    try {
      await _context?.close();
    } catch {
      // ignore
    }
    ws.close(1000);
    process.exit(0);
  });
}

main();
