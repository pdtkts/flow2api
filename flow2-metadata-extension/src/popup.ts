import { DEFAULT_BASE_URL, DEFAULT_PREFERENCES, getPreferences, getRuntimeState, savePreferences } from "./storage";
import { ADOBE_UPLOADS_URL, isSupportedAdobeUrl } from "./adobe-url";
import type { KeywordStyle, LanguageCode, Preferences, RunActivity, RunPhase, RuntimeState, TitleStyle, TitleSuffix } from "./types";
import { deriveRunUiState, type RunAction } from "./ui-state";
import { ensureOriginPermission, normalizeBaseUrl } from "./url-policy";

const byId = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const connectionView = byId<HTMLElement>("connectionView");
const notAdobeView = byId<HTMLElement>("notAdobeView");
const appView = byId<HTMLElement>("appView");
const headerConnection = byId<HTMLElement>("headerConnection");
const baseUrlInput = byId<HTMLInputElement>("baseUrl");
const apiKeyInput = byId<HTMLInputElement>("apiKey");
const connectionError = byId<HTMLElement>("connectionError");
const runActionButton = byId<HTMLButtonElement>("runActionButton");
const startNewButton = byId<HTMLButtonElement>("startNewButton");
const statusElement = byId<HTMLElement>("status");
const progressTrack = byId<HTMLElement>("progressTrack");
const progressValue = byId<HTMLElement>("progressValue");
const progressNode = byId<HTMLElement>("progressNode");
const activityList = byId<HTMLOListElement>("activityList");

const languageNames: Record<LanguageCode, string> = {
  en: "English", fr: "French", de: "German", es: "Spanish", it: "Italian",
  pt: "Portuguese", ja: "Japanese", pl: "Polish", ko: "Korean",
};

const phaseLabels: Record<RunPhase, string> = {
  idle: "Ready",
  starting: "Starting",
  running: "In progress",
  pausing: "Pausing",
  paused: "Paused",
  completed: "Complete",
  error: "Needs attention",
};

const configControlIds = [
  "startIndex", "endIndex", "language", "titleSuffix", "titlePrefix", "customTitleSuffix",
  "titleMin", "titleMax", "keywordMin", "keywordMax", "descriptionMin", "descriptionMax", "customPlatforms",
  "platformShutterstock", "platformGetty", "platformIstock", "platformPond5", "includeCategory", "includeReleases",
  "transparentBackground", "markGenerativeAi", "confirmFictionalPeopleProperty", "customPromptEnabled", "customPrompt",
];

const platformControls: Array<[string, string]> = [
  ["platformShutterstock", "shutterstock"],
  ["platformGetty", "getty-images"],
  ["platformIstock", "istock"],
  ["platformPond5", "pond5"],
];

let preferences: Preferences = { ...DEFAULT_PREFERENCES };
let runtimeState: RuntimeState;
let currentAction: RunAction = "start";

function showError(message = "") {
  connectionError.textContent = message;
  connectionError.hidden = !message;
}

async function activeAdobeTab(): Promise<chrome.tabs.Tab | null> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return isSupportedAdobeUrl(tab?.url) ? tab : null;
}

function showConnection(baseUrl = DEFAULT_BASE_URL) {
  baseUrlInput.value = baseUrl;
  apiKeyInput.value = "";
  apiKeyInput.type = "password";
  byId<HTMLButtonElement>("toggleApiKey").setAttribute("aria-pressed", "false");
  byId<HTMLButtonElement>("toggleApiKey").setAttribute("aria-label", "Show API key");
  connectionView.hidden = false;
  appView.hidden = true;
  notAdobeView.hidden = true;
  headerConnection.hidden = true;
}

async function showApplication(keyLabel: string) {
  connectionView.hidden = true;
  headerConnection.hidden = false;
  byId("connectionLabel").textContent = keyLabel;
  const tab = await activeAdobeTab();
  notAdobeView.hidden = Boolean(tab);
  appView.hidden = !tab;
}

function numberValue(id: string, fallback: number): number {
  const value = Number(byId<HTMLInputElement>(id).value);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : fallback;
}

function rangedNumber(id: string, fallback: number, minimum: number, maximum: number): number {
  const value = Number(byId<HTMLInputElement>(id).value);
  return Number.isFinite(value) ? Math.min(maximum, Math.max(minimum, Math.round(value))) : fallback;
}

function selectedRadio<T extends string>(name: string, fallback: T): T {
  return (document.querySelector<HTMLInputElement>(`input[name="${name}"]:checked`)?.value as T | undefined) ?? fallback;
}

function readPreferences(): Preferences {
  return {
    mode: "upload",
    includeCategory: byId<HTMLInputElement>("includeCategory").checked,
    language: byId<HTMLSelectElement>("language").value as LanguageCode,
    titleSuffix: byId<HTMLSelectElement>("titleSuffix").value as TitleSuffix,
    titlePrefix: byId<HTMLInputElement>("titlePrefix").value.trim(),
    customTitleSuffix: byId<HTMLInputElement>("customTitleSuffix").value.trim(),
    titleMin: rangedNumber("titleMin", 70, 1, 195),
    titleMax: rangedNumber("titleMax", 140, 1, 195),
    keywordMin: rangedNumber("keywordMin", 25, 1, 50),
    keywordMax: rangedNumber("keywordMax", 45, 1, 50),
    descriptionMin: rangedNumber("descriptionMin", 0, 0, 500),
    descriptionMax: rangedNumber("descriptionMax", 0, 0, 500),
    platforms: ["adobe-stock", ...platformControls.filter(([id]) => byId<HTMLInputElement>(id).checked).map(([, value]) => value)],
    customPlatforms: byId<HTMLInputElement>("customPlatforms").value.trim(),
    includeReleases: byId<HTMLInputElement>("includeReleases").checked,
    titleStyle: selectedRadio<TitleStyle>("titleStyle", "seo-optimized"),
    keywordStyle: selectedRadio<KeywordStyle>("keywordStyle", "mixed"),
    transparentBackground: byId<HTMLInputElement>("transparentBackground").checked,
    markGenerativeAi: byId<HTMLInputElement>("markGenerativeAi").checked,
    confirmFictionalPeopleProperty: byId<HTMLInputElement>("markGenerativeAi").checked
      && byId<HTMLInputElement>("confirmFictionalPeopleProperty").checked,
    customPromptEnabled: byId<HTMLInputElement>("customPromptEnabled").checked,
    customPrompt: byId<HTMLTextAreaElement>("customPrompt").value,
  };
}

function updateRecipeSummary(value: Preferences) {
  byId("recipeSummary").textContent = `${languageNames[value.language]} · ${value.titleMin}–${value.titleMax} title · ${value.keywordMin}–${value.keywordMax} keywords`;
}

function updateRangeVisual(prefix: "title" | "keyword" | "description", minimum: number, maximum: number, ceiling: number) {
  byId(`${prefix}RangeValue`).textContent = `${minimum}–${maximum}`;
  const rail = byId<HTMLElement>(`${prefix}RangeRail`);
  rail.style.setProperty("--range-start", `${Math.max(0, Math.min(100, (minimum / ceiling) * 100))}%`);
  rail.style.setProperty("--range-end", `${Math.max(0, Math.min(100, (maximum / ceiling) * 100))}%`);
}

function updateAllRangeVisuals(value = readPreferences()) {
  updateRangeVisual("title", value.titleMin, value.titleMax, 195);
  updateRangeVisual("keyword", value.keywordMin, value.keywordMax, 50);
  updateRangeVisual("description", value.descriptionMin, value.descriptionMax, 500);
}

async function persistPreferences(): Promise<boolean> {
  const next = readPreferences();
  if (next.titleMin > next.titleMax || next.keywordMin > next.keywordMax || next.descriptionMin > next.descriptionMax) {
    statusElement.textContent = "Minimum values cannot exceed maximum values.";
    byId<HTMLDetailsElement>("metadataDetails").open = true;
    return false;
  }
  preferences = next;
  updateAllRangeVisuals(next);
  updateRecipeSummary(next);
  await savePreferences(next);
  return true;
}

function renderMode() {
  byId("modeSummary").textContent = "New uploads";
}

function renderPreferences(value: Preferences) {
  preferences = value;
  renderMode();
  byId<HTMLInputElement>("includeCategory").checked = value.includeCategory;
  byId<HTMLSelectElement>("language").value = value.language;
  byId<HTMLSelectElement>("titleSuffix").value = value.titleSuffix;
  byId<HTMLInputElement>("titlePrefix").value = value.titlePrefix;
  byId<HTMLInputElement>("customTitleSuffix").value = value.customTitleSuffix;
  byId<HTMLInputElement>("titleMin").value = String(value.titleMin);
  byId<HTMLInputElement>("titleMax").value = String(value.titleMax);
  byId<HTMLInputElement>("keywordMin").value = String(value.keywordMin);
  byId<HTMLInputElement>("keywordMax").value = String(value.keywordMax);
  byId<HTMLInputElement>("descriptionMin").value = String(value.descriptionMin);
  byId<HTMLInputElement>("descriptionMax").value = String(value.descriptionMax);
  byId<HTMLInputElement>("customPlatforms").value = value.customPlatforms;
  for (const [id, platform] of platformControls) byId<HTMLInputElement>(id).checked = value.platforms.includes(platform);
  byId<HTMLInputElement>("includeReleases").checked = value.includeReleases;
  document.querySelector<HTMLInputElement>(`input[name="titleStyle"][value="${value.titleStyle}"]`)!.checked = true;
  document.querySelector<HTMLInputElement>(`input[name="keywordStyle"][value="${value.keywordStyle}"]`)!.checked = true;
  byId<HTMLInputElement>("transparentBackground").checked = value.transparentBackground;
  byId<HTMLInputElement>("markGenerativeAi").checked = value.markGenerativeAi;
  byId<HTMLInputElement>("confirmFictionalPeopleProperty").checked = value.confirmFictionalPeopleProperty;
  byId<HTMLInputElement>("confirmFictionalPeopleProperty").disabled = !value.markGenerativeAi;
  byId<HTMLInputElement>("customPromptEnabled").checked = value.customPromptEnabled;
  byId<HTMLTextAreaElement>("customPrompt").value = value.customPrompt;
  byId<HTMLElement>("customPromptWrap").hidden = !value.customPromptEnabled;
  updateAllRangeVisuals(value);
  updateRecipeSummary(value);
}

function createActivityItem(activity: RunActivity): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "activity-item";
  item.dataset.phase = activity.phase;

  const node = document.createElement("i");
  node.className = "activity-node";
  node.setAttribute("aria-hidden", "true");

  const copy = document.createElement("div");
  copy.className = "activity-copy";
  const title = document.createElement("strong");
  title.textContent = `Asset ${String(activity.assetNumber).padStart(2, "0")}`;
  const message = document.createElement("span");
  message.textContent = activity.message;
  copy.append(title, message);

  const page = document.createElement("span");
  page.className = "activity-page";
  page.textContent = `P${String(activity.page).padStart(2, "0")}`;
  item.append(node, copy, page);
  return item;
}

function renderActivities(activities: RunActivity[]) {
  activityList.replaceChildren(...activities.map(createActivityItem));
  byId<HTMLElement>("activityEmpty").hidden = activities.length > 0;
  if (activities.length) activityList.scrollTop = activityList.scrollHeight;
}

function setConfigEnabled(enabled: boolean) {
  for (const id of configControlIds) {
    const control = byId<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement>(id);
    control.disabled = !enabled;
  }
  for (const control of document.querySelectorAll<HTMLInputElement>('input[name="titleStyle"], input[name="keywordStyle"]')) {
    control.disabled = !enabled;
  }
  byId<HTMLInputElement>("platformAdobe").disabled = true;
  byId<HTMLInputElement>("confirmFictionalPeopleProperty").disabled = !enabled || !byId<HTMLInputElement>("markGenerativeAi").checked;
}

function renderRuntime(runtime: RuntimeState) {
  runtimeState = runtime;
  const ui = deriveRunUiState(runtime);
  currentAction = ui.action;
  document.body.dataset.phase = runtime.phase;

  byId("processedCount").textContent = String(runtime.processed);
  byId("successCount").textContent = String(runtime.successes);
  byId("failureCount").textContent = String(Math.max(0, runtime.processed - runtime.successes));
  byId("pageCount").textContent = String(runtime.currentPage).padStart(2, "0");
  const rate = runtime.processed ? Math.round((runtime.successes / runtime.processed) * 100) : 0;
  byId("successRate").textContent = `${rate}% success`;
  statusElement.textContent = runtime.message;

  const phaseBadge = byId("phaseBadge");
  phaseBadge.textContent = phaseLabels[runtime.phase];
  phaseBadge.dataset.tone = ui.tone;

  const isActivelyMoving = runtime.phase === "starting" || runtime.phase === "running" || runtime.phase === "pausing";
  progressTrack.dataset.mode = ui.progressMode;
  progressTrack.dataset.active = String(isActivelyMoving);
  progressValue.style.width = ui.progressMode === "determinate" ? `${ui.progressPercent}%` : "";
  progressNode.style.left = ui.progressMode === "determinate" ? `${ui.progressPercent}%` : "";
  if (ui.progressMode === "determinate") {
    progressTrack.setAttribute("aria-valuenow", String(ui.progressPercent));
    progressTrack.setAttribute("aria-valuetext", `${ui.progressPercent}% complete`);
  } else {
    progressTrack.removeAttribute("aria-valuenow");
    progressTrack.setAttribute("aria-valuetext", runtime.phase === "idle" ? "Not started" : `${runtime.processed} assets processed`);
  }

  byId("currentAsset").textContent = runtime.currentIndex
    ? `Asset ${runtime.currentIndex}${runtime.pageTotal ? ` / ${runtime.pageTotal}` : ""}`
    : runtime.phase === "completed" ? "Run complete" : "Awaiting run";

  runActionButton.textContent = ui.actionLabel;
  runActionButton.disabled = ui.actionDisabled;
  startNewButton.hidden = !ui.showStartNew;
  setConfigEnabled(ui.canEdit);
  renderActivities(runtime.activities);
}

async function connect() {
  showError();
  const button = byId<HTMLButtonElement>("connectButton");
  button.disabled = true;
  button.textContent = "Validating…";
  try {
    const baseUrl = normalizeBaseUrl(baseUrlInput.value);
    if (!(await ensureOriginPermission(baseUrl))) throw new Error("Host permission is required to connect to this Flow2 API server.");
    const response = await chrome.runtime.sendMessage({ type: "VALIDATE_CONNECTION", baseUrl, apiKey: apiKeyInput.value });
    if (!response?.success) throw new Error(response?.error || "Connection validation failed.");
    apiKeyInput.value = "";
    await showApplication(response.keyLabel);
  } catch (error) {
    showError(error instanceof Error ? error.message : "Connection validation failed.");
  } finally {
    button.disabled = false;
    button.textContent = "Connect workspace";
  }
}

async function sendToActiveTab(message: unknown) {
  const tab = await activeAdobeTab();
  if (!tab?.id) throw new Error("Open an Adobe Stock Contributor Uploads or Portfolio page.");
  try {
    await chrome.tabs.sendMessage(tab.id, { action: "ping" });
  } catch {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  }
  try {
    return await chrome.tabs.sendMessage(tab.id, message);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Could not connect to the Adobe page. Reload the tab once and try again. ${detail}`);
  }
}

async function startFreshRun() {
  if (!(await persistPreferences())) throw new Error("Fix the metadata limit values before starting.");
  const start = numberValue("startIndex", 1);
  const rawEnd = byId<HTMLInputElement>("endIndex").value.trim();
  const end = rawEnd ? numberValue("endIndex", start) : 0;
  if (end && start > end) throw new Error("Start index cannot exceed end index.");
  const optimistic: RuntimeState = {
    ...runtimeState,
    processing: true,
    stopped: false,
    phase: "starting",
    processed: 0,
    successes: 0,
    currentPage: 1,
    currentIndex: 0,
    pageTotal: 0,
    targetTotal: end ? end - start + 1 : null,
    activities: [],
    message: "Preparing the run…",
  };
  renderRuntime(optimistic);
  const response = await sendToActiveTab({ action: "startProcessing", mode: preferences.mode, startIndex: start, endIndex: end });
  if (!response?.success) throw new Error(response?.error || "Unable to start processing.");
}

async function resumeRun() {
  renderRuntime({ ...runtimeState, processing: true, stopped: false, phase: "starting", message: "Resuming the run…" });
  const response = await sendToActiveTab({ action: "resumeProcessing" });
  if (!response?.success) throw new Error(response?.error || "Unable to resume processing.");
}

async function pauseRun() {
  renderRuntime({ ...runtimeState, phase: "pausing", message: "Finishing the current step before pausing…" });
  const response = await sendToActiveTab({ action: "stopProcessing" });
  if (!response?.success) throw new Error(response?.error || "Unable to pause processing.");
}

async function performRunAction() {
  try {
    if (currentAction === "start" || currentAction === "run-again") await startFreshRun();
    else if (currentAction === "pause") await pauseRun();
    else if (currentAction === "resume" || currentAction === "retry") await resumeRun();
  } catch (error) {
    renderRuntime({
      ...runtimeState,
      processing: false,
      stopped: true,
      phase: "error",
      message: error instanceof Error ? error.message : "Unable to update the run.",
    });
  }
}

byId<HTMLFormElement>("connectionForm").addEventListener("submit", (event) => {
  event.preventDefault();
  void connect();
});

byId("toggleApiKey").addEventListener("click", () => {
  const button = byId<HTMLButtonElement>("toggleApiKey");
  const visible = apiKeyInput.type === "text";
  apiKeyInput.type = visible ? "password" : "text";
  button.setAttribute("aria-pressed", String(!visible));
  button.setAttribute("aria-label", visible ? "Show API key" : "Hide API key");
});

byId("disconnectButton").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "DISCONNECT" });
  showConnection();
});

async function editConnection() {
  const response = await chrome.runtime.sendMessage({ type: "GET_CONNECTION_STATUS" });
  showConnection(response?.baseUrl || DEFAULT_BASE_URL);
}

byId("editConnection").addEventListener("click", () => void editConnection());
byId("editConnectionFromContext").addEventListener("click", () => void editConnection());
byId("openAdobeButton").addEventListener("click", () => void chrome.tabs.create({ url: ADOBE_UPLOADS_URL }));

for (const id of [
  "language", "titleSuffix", "titlePrefix", "customTitleSuffix", "titleMin", "titleMax", "keywordMin", "keywordMax",
  "descriptionMin", "descriptionMax", "customPlatforms", "platformShutterstock", "platformGetty", "platformIstock",
  "platformPond5", "includeCategory", "includeReleases", "transparentBackground", "markGenerativeAi",
  "confirmFictionalPeopleProperty", "customPromptEnabled", "customPrompt",
]) {
  byId(id).addEventListener("change", () => {
    if (id === "customPromptEnabled") byId("customPromptWrap").hidden = !byId<HTMLInputElement>(id).checked;
    if (id === "markGenerativeAi") {
      const enabled = byId<HTMLInputElement>(id).checked;
      const fictional = byId<HTMLInputElement>("confirmFictionalPeopleProperty");
      fictional.disabled = !enabled;
      if (!enabled) fictional.checked = false;
    }
    void persistPreferences();
  });
}

for (const control of document.querySelectorAll<HTMLInputElement>('input[name="titleStyle"], input[name="keywordStyle"]')) {
  control.addEventListener("change", () => void persistPreferences());
}

for (const id of ["titleMin", "titleMax", "keywordMin", "keywordMax", "descriptionMin", "descriptionMax"]) {
  byId(id).addEventListener("input", () => updateAllRangeVisuals());
}

runActionButton.addEventListener("click", () => void performRunAction());
startNewButton.addEventListener("click", () => void startFreshRun().catch((error) => {
  renderRuntime({
    ...runtimeState,
    processing: false,
    stopped: true,
    phase: "error",
    message: error instanceof Error ? error.message : "Unable to start a new run.",
  });
}));

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "PROCESSING_UPDATE") renderRuntime(message.state as RuntimeState);
  if (message?.type === "CONNECTION_INVALID") showConnection(message.baseUrl || DEFAULT_BASE_URL);
});

document.addEventListener("DOMContentLoaded", async () => {
  renderPreferences(await getPreferences());
  renderRuntime(await getRuntimeState());
  const response = await chrome.runtime.sendMessage({ type: "GET_CONNECTION_STATUS", action: "revalidate" });
  if (response?.connected) await showApplication(response.keyLabel);
  else {
    showConnection(response?.baseUrl || DEFAULT_BASE_URL);
    if (response?.error) showError(response.error);
  }
});
