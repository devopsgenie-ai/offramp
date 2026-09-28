import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

serve(async () => {
  const apiKey = Deno.env.get("RESEND_API_KEY");
  // Summaries go through Lovable's AI gateway, which only works inside Lovable.
  const summary = await fetch("https://ai.gateway.lovable.dev/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${Deno.env.get("LOVABLE_API_KEY")}` },
  });
  return new Response(JSON.stringify({ ok: Boolean(apiKey) && summary.ok }), {
    headers: { "Content-Type": "application/json" },
  });
});
