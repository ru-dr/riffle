// logo.dev image URLs. The publishable key is designed to appear in client
// markup; it is still read from the environment so it can rotate without a
// code change. Returns null without a key so callers can fall back to text.
const TOKEN = process.env.NEXT_PUBLIC_LOGO_DEV_TOKEN;

export function logoUrl(domain: string, { size = 64, greyscale = false } = {}): string | null {
  if (!TOKEN) return null;
  const q = new URLSearchParams({ token: TOKEN, size: String(size), format: "png", retina: "true" });
  if (greyscale) q.set("greyscale", "true");
  return `https://img.logo.dev/${domain}?${q}`;
}

// Fade-in for remote logos, safe for cached images. `onLoad` alone missed
// every image that finished before React attached the handler - on any
// repeat visit that is most of them - leaving them stuck at opacity 0. The
// ref callback catches those; onLoad catches the rest.
const markLoaded = (img: HTMLImageElement | null) => {
  if (img?.complete && img.naturalWidth > 0) img.classList.add("is-loaded");
};
export const fadeIn = {
  ref: markLoaded,
  onLoad: (e: { currentTarget: HTMLImageElement }) => e.currentTarget.classList.add("is-loaded"),
  className: "v2-fade",
} as const;
