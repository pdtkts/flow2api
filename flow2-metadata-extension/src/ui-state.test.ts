import { describe, expect, it } from "vitest";
import { DEFAULT_RUNTIME } from "./storage";
import type { RunPhase, RuntimeState } from "./types";
import { deriveRunUiState } from "./ui-state";

function runtime(phase: RunPhase, patch: Partial<RuntimeState> = {}): RuntimeState {
  return { ...DEFAULT_RUNTIME, phase, ...patch };
}

describe("run UI state", () => {
  it.each([
    ["idle", "start", "Start run", true],
    ["starting", "none", "Starting…", false],
    ["running", "pause", "Pause run", false],
    ["pausing", "none", "Pausing…", false],
    ["paused", "resume", "Resume run", true],
    ["completed", "run-again", "Run again", true],
    ["error", "retry", "Retry run", true],
  ] as const)("maps %s to its single contextual action", (phase, action, label, canEdit) => {
    const state = deriveRunUiState(runtime(phase));
    expect(state.action).toBe(action);
    expect(state.actionLabel).toBe(label);
    expect(state.canEdit).toBe(canEdit);
  });

  it("calculates bounded progress", () => {
    const state = deriveRunUiState(runtime("running", { processed: 3, targetTotal: 8 }));
    expect(state.progressMode).toBe("determinate");
    expect(state.progressPercent).toBe(38);
  });

  it("uses an indeterminate rail for an unbounded run", () => {
    expect(deriveRunUiState(runtime("running", { processed: 12 })).progressMode).toBe("indeterminate");
  });

  it("finishes the rail at 100 percent even when the run had no known total", () => {
    const state = deriveRunUiState(runtime("completed", { processed: 12 }));
    expect(state.progressMode).toBe("determinate");
    expect(state.progressPercent).toBe(100);
  });
});
