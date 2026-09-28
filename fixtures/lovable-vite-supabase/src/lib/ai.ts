// Calls the model straight from the browser.
const OPENAI_KEY = import.meta.env.VITE_OPENAI_API_KEY;

export async function summarise(tasks: unknown[]) {
  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${OPENAI_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: "gpt-4o-mini", messages: [{ role: "user", content: JSON.stringify(tasks) }] }),
  });
  return response.json();
}
