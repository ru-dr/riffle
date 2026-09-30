// Palette taken from voidzero.dev's rendered surface (CSS Peeper export),
// which corrects a mistake made from reading their stylesheet alone: the
// page surface is #FBFAF7, a warm white, not the #f4f3ec that
// --color-beige holds.
//
// Their two typefaces (APK Protocol, KH Teka Mono) are commercial, so this
// substitutes what the repo already licenses: Geist for headings and body,
// Supply Mono for every mono role.
//
// Their brand purple (#6C3BFF / #B39AFF) is deliberately not used. It is the
// one token on their page that is theirs rather than structural, and Riffle
// borrowing it would make this a costume instead of a layout. The shine and
// the accents run in Riffle's own green instead.
export const T = {
  paper: "#ffffff",
  beige: "#fbfaf7", // page surface
  ink: "#16171d",
  inkDeep: "#14121a", // their near-black, for the darkest pills
  grey: "#867e8e",
  stroke: "#e5e4e7",
  nickel: "#3b3440",
  // Status green is a signalling colour, not a brand mark — same role and
  // same family as Riffle's mint, which is too pale to survive on white.
  live: "#00b442",
  // Riffle's mint taken down to where it can sit beside #16171d and carry
  // the headline shine.
  accent: "#0f7a4f",
  accentSoft: "rgba(0,180,66,0.1)",

  // The reference page alternates light and full-bleed dark blocks. Their
  // dark is #14121a; Riffle's own warm near-black is used instead, so the
  // two landing pages still read as the same product.
  darkBg: "#14100c",
  darkInk: "#fbfaf7",
  darkNickel: "rgba(251,250,247,0.66)",
  darkGrey: "rgba(251,250,247,0.42)",
  darkStroke: "rgba(251,250,247,0.11)",
  mint: "#7dd3a0", // Riffle's accent, which only works on the dark blocks
} as const;
