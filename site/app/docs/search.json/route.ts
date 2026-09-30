import { buildIndex } from "@/components/docs/search-index";

// The docs search index, rendered to a static file at build time.
export const dynamic = "force-static";

export async function GET() {
  return Response.json(await buildIndex());
}
