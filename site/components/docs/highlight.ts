import { createHighlighter, createJavaScriptRegexEngine, type Highlighter } from "shiki";

// Build-time syntax highlighting for the docs. Pages are statically rendered,
// so this runs once per code block during the build and ships no JavaScript.
//
// The theme is the site's own terminal palette - the one the homepage's code
// cards use - so a docs code block reads as the same object: structure in
// grey, keys in near-white, strings teal, numbers blue, keywords violet.
// The JavaScript regex engine avoids loading Oniguruma's WebAssembly.

const INK = {
  bg: "#1a1b20",
  fg: "#e7e6ea",
  dim: "#8b8993",
  val: "#8ab4ff",
  str: "#6ee7d8",
  kw: "#c4b5fd",
  ok: "#3fb950",
};

const THEME = {
  name: "riffle-terminal",
  type: "dark" as const,
  colors: { "editor.background": INK.bg, "editor.foreground": INK.fg },
  tokenColors: [
    { scope: ["comment", "punctuation.definition.comment"], settings: { foreground: INK.dim, fontStyle: "italic" } },
    { scope: ["punctuation", "meta.brace", "punctuation.separator", "punctuation.definition"], settings: { foreground: INK.dim } },
    { scope: ["string", "string.quoted", "string.unquoted.plain.out.yaml"], settings: { foreground: INK.str } },
    { scope: ["constant.numeric", "constant.language", "constant.language.boolean"], settings: { foreground: INK.val } },
    { scope: ["support.type.property-name", "entity.name.tag", "entity.name.tag.yaml", "meta.mapping.key"], settings: { foreground: INK.fg } },
    { scope: ["keyword", "storage", "keyword.operator"], settings: { foreground: INK.kw } },
    { scope: ["entity.name.function", "support.function", "variable.parameter"], settings: { foreground: INK.fg, fontStyle: "bold" } },
    { scope: ["variable.other"], settings: { foreground: INK.fg } },
  ],
};

const LANGS = ["json", "yaml", "bash", "text"] as const;
export type CodeLang = (typeof LANGS)[number];

let highlighter: Promise<Highlighter> | null = null;
function get() {
  highlighter ??= createHighlighter({
    themes: [THEME],
    langs: LANGS.filter((l) => l !== "text"),
    engine: createJavaScriptRegexEngine(),
  });
  return highlighter;
}

/** Highlighted HTML for one code block. Unknown languages render as text. */
export async function highlight(code: string, lang: string | undefined): Promise<string> {
  const h = await get();
  const l = (LANGS as readonly string[]).includes(lang ?? "") ? (lang as CodeLang) : "text";
  return h.codeToHtml(code.replace(/\n$/, ""), { lang: l, theme: THEME.name });
}
