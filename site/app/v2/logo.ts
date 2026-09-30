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
