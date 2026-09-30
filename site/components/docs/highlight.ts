import { createHighlighter, createJavaScriptRegexEngine, type Highlighter } from "shiki";

// Build-time syntax highlighting for the docs. Pages are statically rendered,
// so this runs once per code block during the build and ships no JavaScript.
//
// Two palettes of the same scheme - structure in grey, keys in ink, strings
// teal, numbers blue, keywords violet: the homepage's terminal colours for
// the dark theme, and deeper tones of the same hues for the light one. Each
// token carries both as CSS variables (--shiki-light / --shiki-dark) and the
// docs theme picks one, so the switch recolours code with no script.
// The JavaScript regex engine avoids loading Oniguruma's WebAssembly.

type Ink = { fg: string; dim: string; val: string; str: string; kw: string };
const DARK: Ink = { fg: "#e7e6ea", dim: "#8b8993", val: "#8ab4ff", str: "#6ee7d8", kw: "#c4b5fd" };
const LIGHT: Ink = { fg: "#16171d", dim: "#867e8e", val: "#2f5bd3", str: "#0f766e", kw: "#6d4fb8" };

function theme(name: string, type: "light" | "dark", ink: Ink) {
  return {
    name,
    type,
    colors: { "editor.background": "transparent", "editor.foreground": ink.fg },
    tokenColors: [
      { scope: ["comment", "punctuation.definition.comment"], settings: { foreground: ink.dim, fontStyle: "italic" } },
      { scope: ["punctuation", "meta.brace", "punctuation.separator", "punctuation.definition"], settings: { foreground: ink.dim } },
      { scope: ["string", "string.quoted", "string.unquoted.plain.out.yaml"], settings: { foreground: ink.str } },
      { scope: ["constant.numeric", "constant.language", "constant.language.boolean"], settings: { foreground: ink.val } },
      { scope: ["support.type.property-name", "entity.name.tag", "entity.name.tag.yaml", "meta.mapping.key"], settings: { foreground: ink.fg } },
      { scope: ["keyword", "storage", "keyword.operator"], settings: { foreground: ink.kw } },
      { scope: ["entity.name.function", "support.function", "variable.parameter"], settings: { foreground: ink.fg, fontStyle: "bold" } },
      { scope: ["variable.other"], settings: { foreground: ink.fg } },
    ],
  };
}
const THEMES = { light: theme("riffle-light", "light", LIGHT), dark: theme("riffle-dark", "dark", DARK) };

const LANGS = ["json", "yaml", "bash", "text"] as const;
export type CodeLang = (typeof LANGS)[number];

let highlighter: Promise<Highlighter> | null = null;
function get() {
  highlighter ??= createHighlighter({
    themes: [THEMES.light, THEMES.dark],
    langs: LANGS.filter((l) => l !== "text"),
    engine: createJavaScriptRegexEngine(),
  });
  return highlighter;
}

/** Highlighted HTML for one code block. Unknown languages render as text. */
export async function highlight(code: string, lang: string | undefined): Promise<string> {
  const h = await get();
  const l = (LANGS as readonly string[]).includes(lang ?? "") ? (lang as CodeLang) : "text";
  return h.codeToHtml(code.replace(/\n$/, ""), {
    lang: l,
    themes: { light: THEMES.light.name, dark: THEMES.dark.name },
    defaultColor: false,
  });
}
