import { describe, expect, it } from "vitest";
import { DEFAULT_RUNTIME } from "./storage";
import { classifyWorkspaceContext, hasOwnedRun } from "./workspace-context";

describe("workspace context", () => {
  it("treats an active allowed Uploads page as supported", () => {
    expect(classifyWorkspaceContext({
      activeTabId: 12,
      activeSupported: true,
      ownerTabId: null,
      ownerPresent: false,
      ownerSupported: false,
      phase: "idle",
    }).status).toBe("supported");
  });

  it("keeps an active run attached to its owner tab when another tab is active", () => {
    const context = classifyWorkspaceContext({
      activeTabId: 2,
      activeSupported: false,
      ownerTabId: 9,
      ownerPresent: true,
      ownerSupported: true,
      phase: "running",
    });
    expect(context.status).toBe("run-in-another-tab");
    expect(context.actionsTargetOwner).toBe(true);
    expect(context.canShowConsole).toBe(true);
  });

  it("flags owner loss when the owned run tab disappears or leaves Uploads", () => {
    expect(classifyWorkspaceContext({
      activeTabId: 2,
      activeSupported: false,
      ownerTabId: 9,
      ownerPresent: false,
      ownerSupported: false,
      phase: "paused",
    }).status).toBe("owner-tab-lost");
  });

  it("does not keep completed runs attached to another tab", () => {
    expect(classifyWorkspaceContext({
      activeTabId: 2,
      activeSupported: false,
      ownerTabId: 9,
      ownerPresent: true,
      ownerSupported: true,
      phase: "completed",
    }).status).toBe("unsupported");
  });

  it("recognizes only active recoverable phases as owned runs", () => {
    expect(hasOwnedRun({ ...DEFAULT_RUNTIME, ownerTabId: 7, phase: "running" })).toBe(true);
    expect(hasOwnedRun({ ...DEFAULT_RUNTIME, ownerTabId: 7, phase: "completed" })).toBe(false);
    expect(hasOwnedRun({ ...DEFAULT_RUNTIME, ownerTabId: null, phase: "running" })).toBe(false);
  });
});
