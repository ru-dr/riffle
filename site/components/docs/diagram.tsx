import { createHash } from "node:crypto";
import { renderMermaidSVG } from "beautiful-mermaid";

// Diagrams are plain Mermaid in the markdown, so anyone can draw one and
// GitHub renders the same source. At build time each block becomes a static
// SVG coloured by the docs theme variables, so the light/dark switch
// repaints it with no script and no re-render.
//
// Authoring: a ```mermaid fence. An optional first line `%% caption: ...`
// (a Mermaid comment, ignored everywhere else) becomes the figure caption
// and the image's accessible name.

const THEME = {
  bg: "var(--rf-bg)",
  fg: "var(--rf-ink)",
  line: "var(--rf-grey)",
  accent: "var(--rf-accent)",
  muted: "var(--rf-grey)",
  surface: "var(--rf-surface)",
  border: "color-mix(in srgb, var(--rf-grey) 45%, var(--rf-bg))",
  font: "Geist",
  transparent: true,
};

/** Scope the renderer's output to this one figure. */
function contain(svg: string, id: string) {
  return (
    svg
      // No external font request: text uses the page's own Geist.
      .replace(/@import url\([^)]*\);?/g, "")
      .replace(/font-family:\s*'Geist'[^;]*;/g, "font-family: var(--font-geist), system-ui, sans-serif;")
      // A <style> inside inline SVG applies to the whole document. Nest each
      // block under this figure so it cannot restyle any other SVG.
      .replace(/<style>([\s\S]*?)<\/style>/g, (_, css: string) => `<style>[data-diagram="${id}"] { ${css.replace(/(^|\n)\s*svg\s*\{/g, "$1& svg {")} }</style>`)
      // Marker ids are document-global too.
      .replace(/id="([^"]+)"/g, `id="${id}-$1"`)
      .replace(/url\(#([^)]+)\)/g, `url(#${id}-$1)`)
  );
}

export function Diagram({ source }: { source: string }) {
  const code = source.replace(/\n$/, "");
  const caption = /^\s*%%\s*caption:\s*(.+)$/m.exec(code)?.[1].trim();
  const id = "d" + createHash("sha1").update(code).digest("hex").slice(0, 8);

  let svg = "";
  try {
    // The renderer reads the diagram type from the first line, so comments go.
    const body = code.split("\n").filter((l) => !/^\s*%%/.test(l)).join("\n");
    svg = contain(renderMermaidSVG(body, THEME), id);
  } catch (err) {
    // A broken diagram should fail the build, not ship as an empty box.
    throw new Error(`Mermaid diagram failed to render${caption ? ` (${caption})` : ""}: ${err instanceof Error ? err.message : err}`);
  }

  // Natural width, so phones scroll a wide diagram instead of shrinking it.
  const width = Math.round(Number(/viewBox="[\d.]+ [\d.]+ ([\d.]+)/.exec(svg)?.[1] ?? 0));

  return (
    <figure className="docs-diagram my-7 overflow-hidden rounded-md" style={{ outline: "1px solid var(--rf-stroke)", backgroundColor: "var(--rf-bg)" }}>
      <figcaption className="flex items-baseline justify-between gap-4 border-b px-5 py-2" style={{ borderColor: "var(--rf-stroke)", backgroundColor: "var(--rf-wash)" }}>
        <span className="font-geist text-[13px]" style={{ color: "var(--rf-nickel)" }}>
          {caption ?? "Diagram"}
        </span>
        <span className="font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
          Diagram
        </span>
      </figcaption>
      <div
        data-diagram={id}
        role="img"
        aria-label={caption ?? "Diagram"}
        className="docs-diagram-body overflow-x-auto px-3 py-4"
        style={width ? ({ "--dw": `${width}px` } as React.CSSProperties) : undefined}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      {/* The source doubles as the text alternative, and shows how it was drawn. */}
      <details className="group border-t" style={{ borderColor: "var(--rf-stroke)" }}>
        <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-2 font-mono text-[11px] tracking-[0.06em] uppercase select-none [&::-webkit-details-marker]:hidden" style={{ color: "var(--rf-grey)" }}>
          <span aria-hidden="true" className="transition-transform group-open:rotate-90">
            &#8250;
          </span>
          Mermaid source
        </summary>
        <pre className="overflow-x-auto px-5 pb-4 font-mono text-[12.5px] leading-[1.7]" style={{ color: "var(--rf-nickel)" }}>
          <code>{code}</code>
        </pre>
      </details>
    </figure>
  );
}
