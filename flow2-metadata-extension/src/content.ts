import { imageCount, recoverAfterNavigation, startProcessing, stopProcessing } from "./adobe/automation";
import { isSupportedAdobeUrl } from "./adobe-url";
import type { ProcessingMode } from "./types";

interface ContentMessage {
  action?: string;
  mode?: ProcessingMode;
  startIndex?: number;
  endIndex?: number;
}

if (isSupportedAdobeUrl(location.href)) chrome.runtime.onMessage.addListener((message: ContentMessage, _sender, sendResponse) => {
  if (message.action === "ping") {
    sendResponse({ success: true, status: "alive" });
    return false;
  }
  if (message.action === "getImageCount") {
    void imageCount(message.mode || "upload").then((count) => sendResponse({ success: true, count }));
    return true;
  }
  if (message.action === "startProcessing") {
    void startProcessing(message.mode || "upload", message.startIndex || 1, message.endIndex || 0)
      .then(() => undefined)
      .catch(() => undefined);
    sendResponse({ success: true });
    return false;
  }
  if (message.action === "stopProcessing") {
    void stopProcessing().then(() => sendResponse({ success: true }));
    return true;
  }
  if (message.action === "resumeProcessing") {
    void startProcessing(message.mode || "upload", 1, 0, true)
      .then(() => undefined)
      .catch(() => undefined);
    sendResponse({ success: true });
    return false;
  }
  return false;
});

if (isSupportedAdobeUrl(location.href)) void recoverAfterNavigation();
