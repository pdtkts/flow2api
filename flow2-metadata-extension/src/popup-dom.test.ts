import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const html = readFileSync(resolve(import.meta.dirname, "../static/popup.html"), "utf8");

function popupDocument(): Document {
  return new DOMParser().parseFromString(html, "text/html");
}

describe("side-panel document", () => {
  it("uses unique control identifiers", () => {
    const ids = [...popupDocument().querySelectorAll<HTMLElement>("[id]")].map((element) => element.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("associates every visible text field with a label", () => {
    const document = popupDocument();
    const controls = [...document.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input:not([type=checkbox]), select, textarea")];
    for (const control of controls) {
      const explicit = control.id ? document.querySelector(`label[for="${control.id}"]`) : null;
      expect(explicit ?? control.closest("label"), control.id || control.getAttribute("name") || control.type).not.toBeNull();
    }
  });

  it("provides live status, progress semantics, and a collapsed settings disclosure", () => {
    const document = popupDocument();
    expect(document.querySelector("#status")?.getAttribute("aria-live")).toBe("polite");
    expect(document.querySelector("#connectionError")?.getAttribute("role")).toBe("alert");
    expect(document.querySelector("#progressTrack")?.getAttribute("role")).toBe("progressbar");
    expect(document.querySelector("#metadataDetails")?.hasAttribute("open")).toBe(false);
  });

  it("ships one primary run action and a hidden secondary reset action", () => {
    const document = popupDocument();
    expect(document.querySelectorAll("#appView .primary-button")).toHaveLength(1);
    expect(document.querySelector("#runActionButton")?.textContent).toBe("Start run");
    expect(document.querySelector("#startNewButton")?.hasAttribute("hidden")).toBe(true);
  });

  it("includes automatic workspace-context messaging and return-to-run affordance", () => {
    const document = popupDocument();
    expect(document.querySelector("#contextMessage")?.textContent).toContain("English or Canadian Uploads");
    expect(document.querySelector("#workspaceNotice")?.getAttribute("role")).toBe("status");
    expect(document.querySelector("#returnToRunButton")?.textContent).toBe("Return to Adobe run");
  });

  it("locks the workspace to the uploads mode", () => {
    const document = popupDocument();
    expect(document.querySelector("#uploadMode")?.textContent).toContain("/en/uploads");
    expect(document.querySelector("#uploadMode")?.textContent).toContain("/ca/uploads");
    expect(document.querySelector("#portfolioMode")).toBeNull();
  });

  it("exposes the complete Nexus generation choices", () => {
    const document = popupDocument();
    expect(document.querySelectorAll('input[name="titleStyle"]')).toHaveLength(4);
    expect(document.querySelectorAll('input[name="keywordStyle"]')).toHaveLength(3);
    expect(document.querySelectorAll(".platform-grid input")).toHaveLength(5);
    expect(document.querySelector("#platformAdobe")?.hasAttribute("disabled")).toBe(true);
    expect(document.querySelector("#markGenerativeAi")).not.toBeNull();
    expect(document.querySelector("#confirmFictionalPeopleProperty")).not.toBeNull();
  });
});
