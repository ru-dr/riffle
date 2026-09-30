// Question words carry no topic; left in a search they outvote the words
// that do. Shared by the search dialog and Ask AI's retrieval.
export const STOP = new Set(
  "a an and are as at be by can could do does for from go goes how i if in into is it its me my of on or should so than that the their them then there these this to use using was we what when where which who why will with would you your".split(" "),
);

/** A query without its stop words, or the query itself if nothing is left. */
export function topical(q: string) {
  const terms = q.toLowerCase().split(/[^\p{L}\p{N}_.\/*-]+/u).filter((t) => t && !STOP.has(t));
  return terms.length ? terms.join(" ") : q;
}
