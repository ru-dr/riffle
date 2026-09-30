"use client";

import { fadeIn } from "./logo";

// A remote logo that fades in once decoded. A client component because the
// fade needs a ref and an onLoad handler, and server components can pass
// neither - using them inline in a server-rendered section crashed the page.
export function FadeImg({
  src,
  size,
  className = "",
}: {
  src: string;
  size: number;
  className?: string;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      ref={fadeIn.ref}
      onLoad={fadeIn.onLoad}
      className={`${fadeIn.className} ${className}`}
    />
  );
}
