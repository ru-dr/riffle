"use client";

import { useState } from "react";

// Copies a code block's text. The source text is passed in, not read from
// the highlighted DOM, so what lands on the clipboard is exactly what the
// docs author wrote - no stray markup, no trailing newline.
export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {}
  };
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? "Copied" : "Copy code"}
      className="flex items-center gap-1.5 rounded-[5px] px-2 py-1 font-mono text-[11px] tracking-[0.04em] transition-colors hover:bg-white/10"
      style={{ color: copied ? "#3fb950" : "#8b8993" }}
    >
      <svg viewBox="0 0 16 16" className="size-[13px]" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
        {copied ? (
          <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
        ) : (
          <>
            <rect x="5" y="5" width="8.5" height="8.5" rx="1.6" />
            <path d="M10.5 5V3.6A1.6 1.6 0 0 0 8.9 2H3.6A1.6 1.6 0 0 0 2 3.6v5.3a1.6 1.6 0 0 0 1.6 1.6H5" />
          </>
        )}
      </svg>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
