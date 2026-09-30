/** @type {import('next').NextConfig} */
export default {
  async redirects() {
    return [
      // /v2 was the preview URL for the page that is now "/". Exact match
      // only: redirects run before the filesystem, so a /v2/:path* rule
      // would also swallow any public asset under that prefix.
      { source: "/v2", destination: "/", permanent: true },
    ];
  },
};
