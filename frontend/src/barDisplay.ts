import type { BarRange, BarRef, BarSummary } from "./types";

function formatMarketTime(ref: BarRef): string {
  const timeZone = ref.session === "CME" ? "America/Chicago" : "America/New_York";
  return new Intl.DateTimeFormat("en-US", {
    timeZone, hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(ref.bar_timestamp));
}

export function formatBarRef(ref: BarRef): string {
  return `#${ref.day_index} · ${formatMarketTime(ref)} · ${ref.timeframe}`;
}

export function formatBarRange(range: BarRange | null | undefined): string {
  if (!range) return "不适用";
  return `${formatBarRef(range.start)} → ${formatBarRef(range.end)}`;
}

export function barSummaryDisplay(
  summary: Pick<BarSummary, "bar_ref">,
): { label: string; sequence: number } {
  return {
    label: formatBarRef(summary.bar_ref),
    sequence: summary.bar_ref.day_index,
  };
}
