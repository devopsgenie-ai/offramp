import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

serve(async () => {
  const apiKey = Deno.env.get("RESEND_API_KEY");
  return new Response(JSON.stringify({ ok: Boolean(apiKey) }), {
    headers: { "Content-Type": "application/json" },
  });
});
