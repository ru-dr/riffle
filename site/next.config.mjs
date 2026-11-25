/** @type {import('next').NextConfig} */
export default {
  async redirects() {
    return [
      // /v2 was the preview URL for the page that is now "/". Exact match
      // only: redirects run before the filesystem, so a /v2/:path* rule
      // would also swallow any public asset under that prefix.
      { source: "/v2", destination: "/", permanent: true },
      // The first landing page lived here. Retired; old links land on the
      // current one.
      { source: "/v1", destination: "/", permanent: true },
    ];
  },
  async rewrites() {
    return [
      // The architecture canvas is a standalone HTML page in
      // public/architecture/. Public folders do not serve index.html for a
      // bare directory path, so map /architecture to it.
      { source: "/architecture", destination: "/architecture/index.html" },
    ];
  },
};
