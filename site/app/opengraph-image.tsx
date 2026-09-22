import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

// Social card: the lockup, centred, on the page's own base colour.
//
// No type. Satori only parses ttf/otf/woff and the site's Supply Mono ships
// as woff2 subsets, so any text here would render in a font that is not ours
// — worse than no text. The title and description already travel in the
// meta tags next to this image.
//
// Generated at build time (no request-time APIs), so it costs nothing per
// share and the file convention wires up og:image and its dimensions.

export const alt = "Riffle — review what matters first";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Same value as app/page.tsx. Duplicated rather than imported because that
// module is a Server Component tree and this only needs the one colour.
const BASE = "#14100c";

export default async function Image() {
  const lockup = await readFile(
    join(process.cwd(), "public/brand/lockup.svg"),
  );
  const src = `data:image/svg+xml;base64,${lockup.toString("base64")}`;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          // Flat, not the page's radial scrim: resvg dithers large soft
          // gradients into visible rings at this size, and a share card is
          // rendered once and scaled everywhere. Flat survives that.
          backgroundColor: BASE,
        }}
      >
        {/* 934x164 at source; 560 wide keeps generous margins at 1200x630. */}
        <img src={src} width={560} height={98} alt="" />
      </div>
    ),
    size,
  );
}
