import { GoogleGenAI, ThinkingLevel } from "@google/genai";
import { prompt, retrieve } from "@/components/docs/ask";

// "Ask AI" for the docs. POST { question } -> a text stream: the first line
// is the JSON list of sources, the rest is the answer as it is generated.
// The key stays on the server; the model sees only retrieved doc excerpts.

export const maxDuration = 30;

const MODEL = process.env.DOCS_ASK_MODEL ?? "gemma-4-26b-a4b-it";
const MAX_QUESTION = 400;

// Best-effort per-IP limit. It is per server instance, so it caps a burst
// rather than guaranteeing a quota; the provider's own quota is the backstop.
const WINDOW_MS = 60_000;
const PER_WINDOW = 8;
const seen = new Map<string, number[]>();
function limited(ip: string) {
  const now = Date.now();
  const recent = (seen.get(ip) ?? []).filter((t) => now - t < WINDOW_MS);
  recent.push(now);
  seen.set(ip, recent);
  if (seen.size > 5000) seen.clear();
  return recent.length > PER_WINDOW;
}

const fail = (status: number, message: string) => Response.json({ error: message }, { status });

export async function POST(req: Request) {
  const key = process.env.GEMINI_API_KEY;
  if (!key) return fail(503, "Ask AI is not configured on this deployment.");

  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "local";
  if (limited(ip)) return fail(429, "Too many questions in a minute. Try again shortly.");

  let question = "";
  try {
    question = String((await req.json())?.question ?? "").trim();
  } catch {}
  if (!question) return fail(400, "Ask a question.");
  if (question.length > MAX_QUESTION) return fail(400, `Keep questions under ${MAX_QUESTION} characters.`);

  const { sources, context } = await retrieve(question);
  const ai = new GoogleGenAI({ apiKey: key });

  let stream: AsyncGenerator<{ text?: string }>;
  try {
    stream = await ai.models.generateContentStream({
      model: MODEL,
      contents: [{ role: "user", parts: [{ text: prompt(question, context) }] }],
      config: {
        thinkingConfig: { thinkingLevel: ThinkingLevel.LOW },
        maxOutputTokens: 1024,
        temperature: 0.2,
        abortSignal: req.signal,
      },
    });
  } catch (err) {
    console.error("docs ask:", err);
    return fail(502, "The model did not respond. Try again, or use search.");
  }

  const enc = new TextEncoder();
  const body = new ReadableStream({
    async start(controller) {
      controller.enqueue(enc.encode(JSON.stringify(sources) + "\n"));
      try {
        for await (const chunk of stream) if (chunk.text) controller.enqueue(enc.encode(chunk.text));
      } catch (err) {
        if (!req.signal.aborted) {
          console.error("docs ask stream:", err);
          controller.enqueue(enc.encode("\n\n_The answer was cut off. Try again._"));
        }
      }
      controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
}
