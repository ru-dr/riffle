import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

// Social card, on the reference's pattern: a full-bleed streak texture, the
// white lockup centred, and one line of type beneath it.
//
// The type is Geist, read from the geist package's TTFs. Satori parses only
// ttf/otf/woff, which is why the earlier card had no text at all: the site's
// other faces ship here as woff2 only.
//
// Rendered at build time (no request-time APIs), so a share costs nothing.

export const alt = "Riffle — every repo breaks differently";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image() {
  // Literal paths: a helper that assembled them from arguments hid the
  // files from static analysis, and the whole project was traced into the
  // build output to be safe.
  const [bg, lockup, regular] = await Promise.all([
    readFile(join(process.cwd(), "public/art/og-bg.jpg")),
    readFile(join(process.cwd(), "public/brand/lockup.svg")),
    readFile(join(process.cwd(), "node_modules/geist/dist/fonts/geist-sans/Geist-Regular.ttf")),
  ]);
  const bgSrc = `data:image/jpeg;base64,${bg.toString("base64")}`;
  const lockupSrc = `data:image/svg+xml;base64,${lockup.toString("base64")}`;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          position: "relative",
          backgroundColor: "#16171d",
        }}
      >
        <img src={bgSrc} width={1200} height={630} alt="" style={{ position: "absolute", top: 0, left: 0 }} />
        {/* A soft central darkening, so white type holds contrast over the
            brightest streaks without flattening the texture. */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: 1200,
            height: 630,
            backgroundImage: "radial-gradient(ellipse 60% 55% at 50% 50%, rgba(14,12,18,0.45) 0%, rgba(14,12,18,0) 100%)",
          }}
        />
        {/* Lockup is 934x164 at source. */}
        <img src={lockupSrc} width={400} height={70} alt="" style={{ position: "relative" }} />
        <div
          style={{
            position: "relative",
            marginTop: 34,
            fontFamily: "Geist",
            fontSize: 40,
            letterSpacing: "-0.02em",
            color: "#ffffff",
          }}
        >
          Every repo breaks differently
        </div>
      </div>
    ),
    { ...size, fonts: [{ name: "Geist", data: regular, weight: 400, style: "normal" }] },
  );
}
