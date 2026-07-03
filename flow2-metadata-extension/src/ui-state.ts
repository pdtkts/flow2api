import type { RunPhase, RuntimeState } from "./types";

export type RunAction = "start" | "pause" | "resume" | "retry" | "run-again" | "none";

export interface RunUiState {
  action: RunAction;
  actionLabel: string;
  actionDisabled: boolean;
  showStartNew: boolean;
  canEdit: boolean;
  progressMode: "determinate" | "indeterminate" | "idle";
  progressPercent: number;
  tone: "neutral" | "active" | "success" | "warning" | "danger";
}

const phaseUi: Record<RunPhase, Pick<RunUiState, "action" | "actionLabel" | "actionDisabled" | "showStartNew" | "canEdit" | "tone">> = {
  idle: { action: "start", actionLabel: "Start run", actionDisabled: false, showStartNew: false, canEdit: true, tone: "neutral" },
  starting: { action: "none", actionLabel: "Starting…", actionDisabled: true, showStartNew: false, canEdit: false, tone: "active" },
  running: { action: "pause", actionLabel: "Pause run", actionDisabled: false, showStartNew: false, canEdit: false, tone: "active" },
  pausing: { action: "none", actionLabel: "Pausing…", actionDisabled: true, showStartNew: false, canEdit: false, tone: "warning" },
  paused: { action: "resume", actionLabel: "Resume run", actionDisabled: false, showStartNew: true, canEdit: true, tone: "warning" },
  completed: { action: "run-again", actionLabel: "Run again", actionDisabled: false, showStartNew: false, canEdit: true, tone: "success" },
  error: { action: "retry", actionLabel: "Retry run", actionDisabled: false, showStartNew: true, canEdit: true, tone: "danger" },
};

export function deriveRunUiState(runtime: RuntimeState): RunUiState {
  const base = phaseUi[runtime.phase];
  const progressMode = runtime.phase === "completed"
    ? "determinate"
    : runtime.phase === "idle"
    ? "idle"
    : runtime.targetTotal && runtime.targetTotal > 0 ? "determinate" : "indeterminate";
  const progressPercent = runtime.phase === "completed"
    ? 100
    : runtime.targetTotal
    ? Math.min(100, Math.round((runtime.processed / runtime.targetTotal) * 100))
    : 0;
  return { ...base, progressMode, progressPercent };
}
