import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { GeistSans } from "geist/font/sans";
import { Instrument_Serif } from "next/font/google";
import "./globals.css";

const url = "https://riffle.dev";
const title = "Riffle — review what matters first";
const description =
  "Riffle is an open-source GitHub App, in development, that ranks your pull request queue by risk — trained on your own repository's history.";

// Display serif for the tagline. High-contrast, genuinely distinctive at
// large sizes, and contemporary — editorial presence against the painting
// without reading as period pastiche.
const tagline = Instrument_Serif({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-tagline",
});

const supplyMono = localFont({
  src: [
    { path: "../public/fonts/PPSupplyMono-Regular.otf", weight: "400" },
    { path: "../public/fonts/PPSupplyMono-Ultralight.otf", weight: "200" },
  ],
  display: "swap",
  variable: "--font-supply-mono",
});

export const metadata: Metadata = {
  metadataBase: new URL(url),
  title,
  description,
  icons: { icon: "/favicon.svg", apple: "/apple-touch-icon.svg" },
  openGraph: {
    title,
    description,
    url,
    siteName: "Riffle",
    type: "website",
    images: [{ url: "/og.jpg", width: 1200, height: 630, alt: title }],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: ["/og.jpg"],
  },
};

export const viewport: Viewport = { themeColor: "#14100c" };

const schema = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Riffle",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "Any",
  description,
  url,
  license: "https://www.apache.org/licenses/LICENSE-2.0",
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
};

const criticalCss = `
html,body{height:100%;margin:0;background:#14100c}
body{color:#f4f5f7}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${tagline.variable} ${GeistSans.variable} ${supplyMono.variable}`}
    >
      <head>
        <style dangerouslySetInnerHTML={{ __html: criticalCss }} />
        <link
          rel="preload"
          as="image"
          href="/heroes/hero-desktop.jpg"
          imageSrcSet="/heroes/hero-phone.jpg 1170w, /heroes/hero-tablet.jpg 1536w, /heroes/hero-desktop.jpg 2560w"
          imageSizes="100vw"
          fetchPriority="high"
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
