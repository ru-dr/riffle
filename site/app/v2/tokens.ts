// Palette lifted from voidzero.dev's own custom properties, which is the
// layer that makes their pages read the way they do: a beige field, one
// near-black ink, a single grey for everything secondary, and hairlines
// thin enough to be structure rather than decoration.
//
// Their two typefaces (APK Protocol, KH Teka Mono) are commercial, so this
// substitutes what the repo already licenses: Geist for headings and body,
// Supply Mono for every mono role.
export const T = {
  paper: "#ffffff",
  beige: "#f4f3ec",
  ink: "#16171d",
  grey: "#867e8e",
  stroke: "#e5e4e7",
  nickel: "#3b3440",
  // Riffle's mint is a glow colour and vanishes on beige; this is the same
  // hue taken down to where it can sit next to #16171d.
  accent: "#0f7a4f",
  accentSoft: "rgba(15,122,79,0.09)",
} as const;
