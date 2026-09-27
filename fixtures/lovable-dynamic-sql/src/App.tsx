import { supabase } from "@/integrations/supabase/client";

export default function App() {
  return <button onClick={() => supabase.from("bookings").insert({ slot: "09:00" })}>Book</button>;
}
