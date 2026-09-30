// The empty state for a page that is planned but not written yet. It says so
// plainly, and lists what the page will cover - so a reader knows the gap is
// known, and the team has the outline to fill it from.
export function Draft({ outline }: { outline: string[] }) {
  return (
    <div className="rounded-md border" style={{ borderColor: "var(--rf-stroke)" }}>
      <div className="flex items-start gap-4 border-b px-6 py-5" style={{ borderColor: "var(--rf-stroke)", backgroundColor: "var(--rf-wash)" }}>
        <span
          aria-hidden="true"
          className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md"
          style={{ outline: "1px solid var(--rf-stroke)", backgroundColor: "var(--rf-surface)", color: "var(--rf-grey)" }}
        >
          <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.3">
            <path d="M3.5 1.75h6l3 3v9.5h-9z" strokeLinejoin="round" />
            <path d="M9.5 1.75v3h3M5.5 8h5M5.5 10.5h3.5" strokeLinecap="round" />
          </svg>
        </span>
        <div>
          <p className="font-geist text-[15px] font-medium" style={{ color: "var(--rf-ink)" }}>
            This page is being written
          </p>
          <p className="mt-1 font-geist text-[14px] leading-[1.55]" style={{ color: "var(--rf-nickel)" }}>
            It is part of the docs Riffle ships with. Until it lands, here is what it will cover.
          </p>
        </div>
      </div>
      <ol className="px-6 py-4">
        {outline.map((item, i) => (
          <li key={item} className="flex gap-4 border-b py-3 last:border-b-0" style={{ borderColor: "var(--rf-stroke)" }}>
            <span className="w-6 shrink-0 font-mono text-[12px] leading-[1.6]" style={{ color: "var(--rf-grey)" }}>
              {String(i + 1).padStart(2, "0")}
            </span>
            <span className="font-geist text-[15px] leading-[1.55]" style={{ color: "var(--rf-nickel)" }}>
              {item}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
