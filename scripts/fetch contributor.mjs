/**
 * Fetch tastracker contributor assets via the live contributor page in a real
 * Chromium browser, so the page mints its own `x-csr-token` and
 * `x-turnstile-token` per request (FlareSolverr / paste-token flows cannot do
 * this — they're per-request, server-issued, single-use).
 *
 * Output: examples/contributor-data.json (array of asset objects, same field
 * names as the previous HTML/RSC scraper so downstream consumers keep working).
 *
 * --- One-time setup ---
 *   npx playwright install chromium
 *
 * --- Quick start ---
 *   set TRACK_ADOBE_COOKIES=<full Cookie header from a logged-in browser>
 *   node "scripts/fetch contributor.mjs"
 *
 * --- Inputs (all optional, sensible defaults) ---
 *   SEARCH_ID      contributor id (default "207618192")
 *   ORDER          creation | relevance | downloads (default "creation")
 *   PAGE           single page (default 1)
 *   PAGES          batch fetch, e.g. "1-5" or "1,3,4" (overrides PAGE)
 *   TITLE_FILTER   substring filter on title
 *   OUT_JSON       output path (default "examples/contributor-data.json")
 *   HEADLESS       0 to watch the browser visibly, 1 to run headless (default)
 *   USER_DATA_DIR  persistent browser profile path (default ".cache/tas-profile")
 *   DEVICE_ID      app-level stable device id (default "dev_d6u2k6_df8slvt12u5_mnyhbifj")
 *
 * --- Auth (required) ---
 *   TRACK_ADOBE_COOKIES   full Cookie header string from a logged-in browser
 *                         session (must contain __Secure-next-auth.session-token).
 *                         Falls back to the inline FALLBACK_COOKIE for legacy runs.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { chromium } from "playwright";

// ─── Config ────────────────────────────────────────────────────────────────

const SEARCH_ID = (process.env.SEARCH_ID ?? "207618192").trim();
const ORDER = (process.env.ORDER ?? "creation").trim();
const TITLE_FILTER = (process.env.TITLE_FILTER ?? "").trim().toLowerCase();
const OUT_JSON = process.env.OUT_JSON ?? "examples/contributor-data.json";
const HEADLESS = (process.env.HEADLESS ?? "1").trim() !== "0";
const PAGE_TIMEOUT_MS = Number(process.env.PAGE_TIMEOUT_MS ?? "60000") || 60_000;
const USER_DATA_DIR = resolve(
  process.cwd(),
  process.env.USER_DATA_DIR ?? ".cache/tas-profile",
);
const DEVICE_ID = (
  process.env.DEVICE_ID ?? "dev_d6u2k6_wabygqst2z9_mocsd0nz"
).trim();
const DEVICE_NAME = (process.env.DEVICE_NAME ?? "Chrome on Windows").trim();

const FALLBACK_COOKIE =
  "__Host-next-auth.csrf-token=bb0d296483695f9f6dd71e61b35e3c33bef2c85a1dd3b4f8bb37fc7df99a7945%7Cc86c4908c733a2a58e58fef0219abf2085f72e60b21dabd6b86040884048b529; __Secure-next-auth.callback-url=https%3A%2F%2Ftastracker.com; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..W2wyMfuHNvRjATOP.r2FMYyUDgX9vXS19d-EHBXHm2TpSWhKBcwNQi7kjblW3OAin5qH9_AwhS4qmj4pBNDVggShTrCDqtaCTMpZhiRfq2Kq15fOV-bjYKEV0TTV0E44A5FImZX4NdkDnigI-A8nqnRKQOJeTCin_LsoxJbalp5iA5DQ8YrsZNX0kx_mdXieyqz8kYFFEm1wJy30CBESX_G7AYpHQQEJ021-pzEOC2xhSNf-UURpUE5s64497ZthzcjHJmiC_t7EPjtRaED7DwabtA1R_aiDj3KS8tbrx0KFLz2VKfqUHG3f60a-YFMRApJ2OSoMBb1QjKAfr-U4leokG7khTVtZ9HeWkqRPrRIkLe04m7g.rKnlOKeC4bz_26PZMSO5lQ";

const AUTH_COOKIE = (process.env.TRACK_ADOBE_COOKIES ?? FALLBACK_COOKIE).trim();
const DEFAULT_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36";
const HOST = "tastracker.com";

// ─── Helpers ───────────────────────────────────────────────────────────────

function ensureDirForFile(absPath) {
  mkdirSync(dirname(absPath), { recursive: true });
}

function buildSeedUrl(page) {
  const qs = new URLSearchParams({
    search: SEARCH_ID,
    order: ORDER,
    content_type: "all",
    generative_ai: "all",
  });
  if (page > 1) qs.set("page", String(page));
  return `https://${HOST}/contributor?${qs}`;
}

function parsePageList() {
  const raw = (process.env.PAGES ?? "").trim();
  if (raw) {
    const set = new Set();
    for (const part of raw.split(",").map((s) => s.trim()).filter(Boolean)) {
      const m = part.match(/^(\d+)-(\d+)$/);
      if (m) {
        const [a, b] = [Number(m[1]), Number(m[2])].sort((x, y) => x - y);
        for (let i = a; i <= b; i++) set.add(i);
      } else if (/^\d+$/.test(part)) {
        set.add(Number(part));
      }
    }
    const list = [...set].filter((n) => n >= 1).sort((x, y) => x - y);
    if (list.length) return list;
  }
  const single = Math.max(1, Number(process.env.PAGE ?? "1") | 0 || 1);
  return [single];
}

/**
 * Convert a browser-style "Cookie:" header into Playwright cookie objects.
 * Handles the cookie-prefix rules:
 *   __Host-*   → must use exact host (no leading dot), path=/, secure=true
 *   __Secure-* → must be secure=true
 * Everything else gets a leading-dot host and Lax sameSite by default.
 */
function parseCookieHeaderToPwCookies(header) {
  return String(header || "")
    .split(";")
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => {
      const idx = p.indexOf("=");
      if (idx <= 0) return null;
      const name = p.slice(0, idx).trim();
      const value = p.slice(idx + 1).trim();
      if (!name) return null;

      const cookie = {
        name,
        value,
        domain: `.${HOST}`,
        path: "/",
        secure: false,
        httpOnly: false,
        sameSite: "Lax",
      };
      if (name.startsWith("__Host-")) {
        cookie.domain = HOST;
        cookie.path = "/";
        cookie.secure = true;
      } else if (name.startsWith("__Secure-")) {
        cookie.secure = true;
      }
      return cookie;
    })
    .filter(Boolean);
}

function mapImage(img) {
  if (!img || typeof img !== "object") return null;
  const id = String(img.id ?? img.assetId ?? "").trim();
  if (!id) return null;
  return {
    id,
    title: String(img.title ?? ""),
    downloads: Number(img.downloads ?? 0) || 0,
    keywords: String(img.keywords ?? ""),
    imageUrl: String(img.thumbnailUrl ?? img.imageUrl ?? ""),
    dimensions: String(img.dimensions ?? ""),
    mediaType: String(img.mediaType ?? ""),
    contentType: String(img.contentType ?? ""),
    category: String(img.category ?? ""),
    premium: String(img.premium ?? ""),
    updatedAt: String(img.creationDate ?? img.updatedAt ?? ""),
    isAI: Boolean(img.isAI),
    creator: String(img.creator ?? ""),
  };
}

function explainExpiredAuth() {
  console.error(
    [
      "",
      "Auth session looks expired or invalid.",
      "",
      "Fix:",
      "  1) Open tastracker.com in your browser and log in.",
      "  2) Open DevTools → Application → Cookies → https://tastracker.com",
      "     Copy the full Cookie header (or at minimum __Secure-next-auth.session-token).",
      "  3) Set it in this shell:",
      "       set TRACK_ADOBE_COOKIES=<full Cookie header>",
      "  4) Re-run the script.",
      "",
    ].join("\n"),
  );
}

function looksUnauthorized(body) {
  if (!body || typeof body !== "object") return false;
  const err = String(body.error ?? body.message ?? "").toLowerCase();
  return /unauthor|forbidden|not.*signed.?in|please.*log|session expired/i.test(err);
}

// ─── Main ──────────────────────────────────────────────────────────────────

async function main() {
  if (!AUTH_COOKIE || !/__Secure-next-auth\.session-token=/.test(AUTH_COOKIE)) {
    console.error(
      "Missing TRACK_ADOBE_COOKIES (must contain __Secure-next-auth.session-token).",
    );
    process.exit(2);
  }

  const pageList = parsePageList();
  const firstPage = pageList[0];
  const restPages = pageList.slice(1);

  console.error(
    `[playwright] launching chromium (headless=${HEADLESS}) profile=${USER_DATA_DIR} deviceId=${DEVICE_ID} → search=${SEARCH_ID} order=${ORDER} pages=${pageList.join(",")}`,
  );

  mkdirSync(USER_DATA_DIR, { recursive: true });
  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    headless: HEADLESS,
    userAgent: DEFAULT_UA,
    viewport: { width: 1280, height: 900 },
  });

  try {
    await context.addCookies(parseCookieHeaderToPwCookies(AUTH_COOKIE));

    const page = context.pages()[0] ?? (await context.newPage());
    page.setDefaultTimeout(PAGE_TIMEOUT_MS);

    await context.addInitScript(({ stableDeviceId, stableDeviceName }) => {
      try {
        // Only keep these hardcoded as requested.
        localStorage.setItem("_dvx.id", stableDeviceId);
        localStorage.setItem("_dvx.nm", stableDeviceName);

        sessionStorage.setItem("_dvx.id", stableDeviceId);
        sessionStorage.setItem("_dvx.nm", stableDeviceName);
      } catch {
        /* ignore storage errors */
      }

      // Keep app-level device header stable for in-page API calls.
      const originalFetch = window.fetch.bind(window);
      window.fetch = (input, init = {}) => {
        const headers = new Headers(init.headers || {});
        if (!headers.has("x-device-id")) headers.set("x-device-id", stableDeviceId);
        if (!headers.has("X-Device-Id")) headers.set("X-Device-Id", stableDeviceId);
        return originalFetch(input, { ...init, headers });
      };
    }, { stableDeviceId: DEVICE_ID, stableDeviceName: DEVICE_NAME });

    const seedUrl = buildSeedUrl(firstPage);
    console.error(`[playwright] navigating: ${seedUrl}`);

    let response;
    try {
      [response] = await Promise.all([
        page.waitForResponse(
          (r) =>
            r.url().includes("/api/contributor-search") &&
            r.request().method() === "GET",
          { timeout: PAGE_TIMEOUT_MS },
        ),
        page.goto(seedUrl, { waitUntil: "domcontentloaded" }),
      ]);
    } catch (waitErr) {
      const finalUrl = page.url();
      if (/\/login|\/auth\//.test(finalUrl)) {
        explainExpiredAuth();
        process.exit(1);
      }
      throw waitErr;
    }

    if (!response.ok()) {
      console.error(
        `[playwright] initial /api/contributor-search returned ${response.status()} ${response.statusText()}`,
      );
      try {
        const errJson = await response.json();
        if (looksUnauthorized(errJson)) {
          explainExpiredAuth();
        } else {
          console.error("body:", JSON.stringify(errJson).slice(0, 400));
        }
      } catch {
        /* no JSON body */
      }
      process.exit(1);
    }

    const firstBody = await response.json();
    if (looksUnauthorized(firstBody)) {
      explainExpiredAuth();
      process.exit(1);
    }

    const allBodies = [{ page: firstPage, body: firstBody }];

    if (restPages.length) {
      console.error(`[playwright] fetching extra pages in-page: ${restPages.join(",")}`);
      const extra = await page.evaluate(
        async ({ search, order, pages }) => {
          const out = [];
          for (const p of pages) {
            const qs = new URLSearchParams({ search, page: String(p), order });
            try {
              const r = await fetch(`/api/contributor-search?${qs}`, {
                method: "GET",
                credentials: "include",
              });
              const json = await r.json().catch(() => null);
              out.push({ page: p, status: r.status, body: json });
            } catch (err) {
              out.push({ page: p, status: 0, body: { error: String(err?.message || err) } });
            }
          }
          return out;
        },
        { search: SEARCH_ID, order: ORDER, pages: restPages },
      );

      for (const entry of extra) {
        if (entry.status >= 200 && entry.status < 300 && entry.body && !looksUnauthorized(entry.body)) {
          allBodies.push({ page: entry.page, body: entry.body });
        } else {
          console.error(
            `[playwright] page=${entry.page} failed: status=${entry.status} body=${JSON.stringify(entry.body).slice(0, 200)}`,
          );
          if (looksUnauthorized(entry.body)) {
            explainExpiredAuth();
            process.exit(1);
          }
        }
      }
    }

    const allImages = allBodies.flatMap(({ body }) =>
      Array.isArray(body?.images) ? body.images : [],
    );
    const mapped = allImages.map(mapImage).filter(Boolean);
    const filtered = TITLE_FILTER
      ? mapped.filter((r) => r.title.toLowerCase().includes(TITLE_FILTER))
      : mapped;

    const outPath = resolve(process.cwd(), OUT_JSON);
    ensureDirForFile(outPath);
    writeFileSync(outPath, JSON.stringify(filtered, null, 2), "utf8");

    const summary = {
      pages: pageList,
      received: allImages.length,
      written: filtered.length,
      hasMorePages: Boolean(allBodies.at(-1)?.body?.hasMorePages),
      totalAssets: allBodies[0]?.body?.totalAssets,
      fromCache: Boolean(allBodies[0]?.body?.fromCache),
      usageData: allBodies[0]?.body?.usageData,
    };

    console.log("status        ", response.status(), response.statusText());
    console.log("pages         ", pageList.join(","));
    console.log("totals        ", JSON.stringify(summary));
    console.log("out →         ", outPath);
  } finally {
    await context.close().catch(() => {});
  }
}

main().catch((err) => {
  console.error("fatal:", err?.stack || err?.message || String(err));
  process.exit(1);
});
