import type { DocStatus } from "./registry";

// A page's standing, shown where the reader decides how far to rely on it.
// A rounded rectangle, each state with its own colour for text and tint.
// Mid-weight tones on a 12% tint of themselves, so one tag reads on both the
// light and the dark theme without a second palette.
const STYLE: Record<DocStatus, { label: string; color: string; bg: string }> = {
  accepted: { label: "Accepted", color: "#16a34a", bg: "rgba(22,163,74,0.12)" },
  proposed: { label: "Proposed", color: "#d97706", bg: "rgba(217,119,6,0.12)" },
  planned: { label: "Planned", color: "var(--v2-nickel)", bg: "var(--v2-wash)" },
  generated: { label: "Generated from schemas", color: "#3b82f6", bg: "rgba(59,130,246,0.12)" },
  draft: { label: "Draft", color: "#dc2626", bg: "rgba(220,38,38,0.1)" },
};

export function StatusTag({ status }: { status: DocStatus }) {
  const s = STYLE[status];
  return (
    <span
      className="inline-flex items-center rounded-[4px] px-1.5 py-0.5 font-mono text-[10.5px] tracking-[0.06em] whitespace-nowrap uppercase"
      style={{ color: s.color, backgroundColor: s.bg }}
    >
      {s.label}
    </span>
  );
}
