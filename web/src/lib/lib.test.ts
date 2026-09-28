import {
  axisLabels,
  elapsed,
  formatCompactDateTime,
  formatDateTime,
  formatDuration,
  formatInteger,
  formatPercent,
  formatRatio,
  shortId,
  shortSha,
  words,
} from "./format";
import { isActive, toneOf } from "./status";

describe("format", () => {
  it("formats missing values as a dash", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatInteger(undefined)).toBe("—");
    expect(formatPercent(null)).toBe("—");
    expect(formatRatio(undefined)).toBe("—");
    expect(formatDuration(null)).toBe("—");
    expect(shortSha(null)).toBe("—");
  });

  it("formats numbers", () => {
    expect(formatPercent(88.235)).toBe("88.2 %");
    expect(formatPercent(50, 0)).toBe("50 %");
    expect(formatRatio(0.875)).toBe("87.5 %");
    expect(formatInteger(1234)).toMatch(/1.?234/);
  });

  it("formats durations at every scale", () => {
    expect(formatDuration(0.121)).toBe("121 ms");
    expect(formatDuration(3.84)).toBe("3.8 s");
    expect(formatDuration(125)).toBe("2 min 5 s");
    expect(elapsed("2026-09-27T10:00:00Z", "2026-09-27T10:00:02.5Z")).toBe(2.5);
    expect(elapsed(null, "2026-09-27T10:00:00Z")).toBeNull();
  });

  it("formats identifiers and enum words", () => {
    expect(shortSha("0123456789abcdef")).toBe("0123456");
    expect(shortId("11111111-2222")).toBe("11111111");
    expect(words("timed_out")).toBe("timed out");
    expect(formatDateTime("2026-09-27T10:00:00Z")).toContain("2026");
    expect(formatCompactDateTime("2026-09-27T10:00:00Z")).toMatch(/\d/);
  });

  it("labels an axis with times on one day and dates across days", () => {
    const sameDay = axisLabels(["2026-09-27T10:00:00Z", "2026-09-27T10:30:00Z"]);
    expect(sameDay[0]).toMatch(/\d{1,2}:\d{2}/);
    const days = axisLabels(["2026-09-01T10:00:00Z", "2026-09-27T10:00:00Z"]);
    expect(days[0]).not.toMatch(/\d{1,2}:\d{2}/);
    expect(days).toHaveLength(2);
  });
});

describe("status", () => {
  it("maps states to tones, unknown values to neutral", () => {
    expect(toneOf("job", "failed")).toBe("critical");
    expect(toneOf("job", "running")).toBe("active");
    expect(toneOf("outcome", "sandbox_unavailable")).toBe("warning");
    expect(toneOf("verdict", "clean")).toBe("good");
    expect(toneOf("conclusion", "neutral")).toBe("warning");
    expect(toneOf("severity", "high")).toBe("serious");
    expect(toneOf("mutant", "survived")).toBe("critical");
    expect(toneOf("change", "regressed")).toBe("critical");
    expect(toneOf("probe", "server_error")).toBe("critical");
    expect(toneOf("job", "something new")).toBe("neutral");
  });

  it("knows which jobs are still active", () => {
    expect(isActive("queued")).toBe(true);
    expect(isActive("running")).toBe(true);
    expect(isActive("succeeded")).toBe(false);
    expect(isActive(undefined)).toBe(false);
  });
});
