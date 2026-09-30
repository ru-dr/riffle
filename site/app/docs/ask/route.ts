import { prompt, retrieve, type Source } from "@/components/docs/ask";

// "Ask AI" for the docs, on Cloudflare Workers AI. POST { question } -> a
// text stream: the first line is the JSON list of sources, the rest is the
// answer as it is generated. The token stays on the server; the model sees
// only retrieved doc excerpts.
//
// Cost: Workers AI includes 10,000 neurons a day at no charge. On the
// Workers Free plan, requests past that are refused rather than billed, and
// the reader is told to come back tomorrow.

export const maxDuration = 60;

const ACCOUNT = process.env.CLOUDFLARE_ACCOUNT_ID;
const TOKEN = process.env.CLOUDFLARE_AI_TOKEN;
// Qwen3 30B (A3B): on the Workers Free plan, about 30 neurons per docs
// question - roughly 300 a day inside the free allowance - and it cites
// well. Its reasoning arrives in reasoning_content, which is not streamed.
// DOCS_ASK_REASONING sets reasoning_effort for models that take it.
const MODEL = process.env.DOCS_ASK_MODEL ?? "@cf/qwen/qwen3-30b-a3b-fp8";
const REASONING = process.env.DOCS_ASK_REASONING ?? "";
const MAX_QUESTION = 400;

// Our own limits, on top of Cloudflare's allowance: per visitor a minute and
// a day, and for the whole site a day, set under what the free neurons cover
// (~310 questions at ~30 neurons each) so one visitor or a script cannot
// use it all up. Cached
// answers cost nothing and are not counted.
//
// The counters live in this server instance's memory. On a single instance
// they are exact; across several they are per instance, so a durable limit
// would need a shared store (e.g. Upstash Redis). Env overrides the numbers.
const LIMITS = {
  perMinute: Number(process.env.DOCS_ASK_PER_MINUTE ?? 5),
  perDay: Number(process.env.DOCS_ASK_PER_DAY ?? 30),
  siteDay: Number(process.env.DOCS_ASK_SITE_PER_DAY ?? 300),
};
const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;
const visitors = new Map<string, { minute: number[]; day: number[] }>();
let site: number[] = [];

/** Seconds until the caller may ask again, or 0 if allowed. Counts the call when allowed. */
function admit(ip: string): { wait: number; reason?: string } {
  const now = Date.now();
  site = site.filter((t) => now - t < DAY);
  const v = visitors.get(ip) ?? { minute: [], day: [] };
  v.minute = v.minute.filter((t) => now - t < MINUTE);
  v.day = v.day.filter((t) => now - t < DAY);
  const until = (oldest: number, span: number) => Math.max(1, Math.ceil((oldest + span - now) / 1000));
  if (site.length >= LIMITS.siteDay) return { wait: until(site[0], DAY), reason: "Ask AI has reached today's limit for the site. Try again tomorrow, or use search." };
  if (v.day.length >= LIMITS.perDay) return { wait: until(v.day[0], DAY), reason: `You've asked ${LIMITS.perDay} questions today, the daily limit. Try again tomorrow, or use search.` };
  if (v.minute.length >= LIMITS.perMinute) return { wait: until(v.minute[0], MINUTE), reason: "That's a lot of questions in a minute. Try again in a moment." };
  v.minute.push(now);
  v.day.push(now);
  site.push(now);
  visitors.set(ip, v);
  if (visitors.size > 10_000) visitors.delete(visitors.keys().next().value!);
  return { wait: 0 };
}

// Repeated questions are answered from memory for an hour, so the free
// allowance goes to new questions.
const CACHE_MS = 60 * 60_000;
const cache = new Map<string, { at: number; sources: Source[]; text: string }>();
const keyOf = (q: string) => q.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

const fail = (status: number, message: string) => Response.json({ error: message }, { status });
const enc = new TextEncoder();

function streamOf(sources: Source[], text: string) {
  return new Response(JSON.stringify(sources) + "\n" + text, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
}

export async function POST(req: Request) {
  if (!ACCOUNT || !TOKEN) return fail(503, "Ask AI is not configured on this deployment.");

  // Vercel sets x-forwarded-for; its first entry is the visitor.
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() || req.headers.get("x-real-ip") || "local";

  let question = "";
  try {
    question = String((await req.json())?.question ?? "").trim();
  } catch {}
  if (!question) return fail(400, "Ask a question.");
  if (question.length > MAX_QUESTION) return fail(400, `Keep questions under ${MAX_QUESTION} characters.`);

  const key = keyOf(question);
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return streamOf(hit.sources, hit.text);

  const gate = admit(ip);
  if (gate.wait) return Response.json({ error: gate.reason }, { status: 429, headers: { "retry-after": String(gate.wait) } });

  const { sources, context } = await retrieve(question);

  let res: Response;
  try {
    res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${ACCOUNT}/ai/v1/chat/completions`, {
      method: "POST",
      headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" },
      body: JSON.stringify({
        model: MODEL,
        messages: [{ role: "user", content: prompt(question, context) }],
        stream: true,
        // Room for the model's reasoning plus a short answer.
        max_completion_tokens: 2048,
        // Deterministic: the same question should get the same answer.
        temperature: 0,
        ...(REASONING ? { reasoning_effort: REASONING } : {}),
      }),
      signal: req.signal,
    });
  } catch (err) {
    if (req.signal.aborted) return new Response(null, { status: 499 });
    console.error("docs ask:", err);
    return fail(502, "The model did not respond. Try again, or use search.");
  }
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    console.error("docs ask:", res.status, detail.slice(0, 500));
    // Workers AI answers 429 (or a neuron-limit error) once the day's free
    // allowance is used up.
    if (/not available on the Workers Free plan/i.test(detail)) return fail(503, "Ask AI's model is not available on this account's plan.");
    if (res.status === 429 || /neuron|limit|quota/i.test(detail)) return fail(429, "Ask AI has used today's free allowance. Try again tomorrow, or use search.");
    return fail(502, "The model did not respond. Try again, or use search.");
  }

  const upstream = res.body.getReader();
  const dec = new TextDecoder();
  const body = new ReadableStream({
    async start(controller) {
      controller.enqueue(enc.encode(JSON.stringify(sources) + "\n"));
      let buf = "";
      let text = "";
      // Some models open with a <think> block; readers get the answer only.
      let thinking: boolean | null = null;
      const emit = (piece: string) => {
        if (thinking === null) {
          const lead = (text + piece).trimStart();
          if (!lead) return;
          if ("<think>".startsWith(lead.slice(0, 7)) && lead.length < 7) {
            text += piece;
            return;
          }
          thinking = lead.startsWith("<think>");
          piece = text + piece;
          text = "";
        }
        if (thinking) {
          const end = piece.indexOf("</think>");
          if (end < 0) return;
          thinking = false;
          piece = piece.slice(end + 8).replace(/^\s+/, "");
        }
        if (!piece) return;
        text += piece;
        controller.enqueue(enc.encode(piece));
      };
      try {
        for (;;) {
          const { done, value } = await upstream.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let nl: number;
          while ((nl = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, nl).trim();
            buf = buf.slice(nl + 1);
            if (!line.startsWith("data:")) continue;
            const data = line.slice(5).trim();
            if (data === "[DONE]") continue;
            try {
              // OpenAI shape; reasoning_content, where a model sends it, is skipped.
              // Workers AI sends digit-only tokens as JSON numbers
              // ({"content":3}), so numbers count as text too.
              const delta = JSON.parse(data)?.choices?.[0]?.delta?.content;
              if (typeof delta === "string" || typeof delta === "number") emit(String(delta));
            } catch {}
          }
        }
        if (text.trim()) cache.set(key, { at: Date.now(), sources, text });
        else controller.enqueue(enc.encode("_No answer came back. Try again, or use search._"));
        if (cache.size > 300) cache.delete(cache.keys().next().value!);
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
