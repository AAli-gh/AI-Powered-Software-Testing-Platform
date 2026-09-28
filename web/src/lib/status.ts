/** How a state is shown: a tone (colour role) plus its words; never colour alone. */
export type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "active";

const JOB: Record<string, Tone> = {
  queued: "active",
  running: "active",
  succeeded: "good",
  failed: "critical",
  cancelled: "neutral",
};

const OUTCOME: Record<string, Tone> = {
  passed: "good",
  failed: "critical",
  error: "critical",
  no_tests: "warning",
  timed_out: "serious",
  install_failed: "serious",
  sandbox_unavailable: "warning",
  skipped: "neutral",
  xfailed: "neutral",
};

const VERDICT: Record<string, Tone> = {
  regressions: "critical",
  clean: "good",
  inconclusive: "warning",
};

const CONCLUSION: Record<string, Tone> = {
  failure: "critical",
  success: "good",
  neutral: "warning",
};

const SEVERITY: Record<string, Tone> = {
  critical: "critical",
  high: "serious",
  medium: "warning",
  low: "neutral",
};

const MUTANT: Record<string, Tone> = {
  killed: "good",
  timeout: "good",
  survived: "critical",
  no_coverage: "serious",
  error: "warning",
};

const CHANGE: Record<string, Tone> = {
  regressed: "critical",
  fixed: "good",
  still_failing: "serious",
  added: "neutral",
  removed: "neutral",
  unchanged: "neutral",
  not_run: "warning",
};

const PROBE: Record<string, Tone> = {
  passed: "good",
  server_error: "critical",
  accepted_invalid: "serious",
  unexpected: "warning",
  not_run: "neutral",
};

const TABLES = {
  job: JOB,
  outcome: OUTCOME,
  verdict: VERDICT,
  conclusion: CONCLUSION,
  severity: SEVERITY,
  mutant: MUTANT,
  change: CHANGE,
  probe: PROBE,
};

export type StatusKind = keyof typeof TABLES;

export function toneOf(kind: StatusKind, value: string): Tone {
  return TABLES[kind][value] ?? "neutral";
}

export function isActive(status: string | null | undefined): boolean {
  return status === "queued" || status === "running";
}
