import type { RunPhase, RuntimeState } from "./types";

export type WorkspaceStatus = "checking" | "supported" | "unsupported" | "run-in-another-tab" | "owner-tab-lost";

export interface WorkspaceContextInput {
  activeTabId: number | null;
  activeSupported: boolean;
  ownerTabId: number | null;
  ownerPresent: boolean;
  ownerSupported: boolean;
  phase: RunPhase;
}

export interface WorkspaceContext {
  status: WorkspaceStatus;
  canShowConsole: boolean;
  actionsTargetOwner: boolean;
}

const ownerPhases = new Set<RunPhase>(["starting", "running", "pausing", "paused", "error"]);

export function hasOwnedRun(runtime: Pick<RuntimeState, "ownerTabId" | "phase">): boolean {
  return runtime.ownerTabId !== null && ownerPhases.has(runtime.phase);
}

export function classifyWorkspaceContext(input: WorkspaceContextInput): WorkspaceContext {
  const ownerActive = input.ownerTabId !== null && ownerPhases.has(input.phase);
  if (ownerActive && (!input.ownerPresent || !input.ownerSupported)) {
    return { status: "owner-tab-lost", canShowConsole: true, actionsTargetOwner: false };
  }
  if (ownerActive && input.activeTabId !== input.ownerTabId) {
    return { status: "run-in-another-tab", canShowConsole: true, actionsTargetOwner: true };
  }
  if (input.activeSupported) {
    return { status: "supported", canShowConsole: true, actionsTargetOwner: ownerActive };
  }
  return { status: "unsupported", canShowConsole: false, actionsTargetOwner: false };
}
