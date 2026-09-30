import { supabase } from "@/integrations/supabase/client";

export default function App() {
  return <button onClick={() => supabase.from("order").select("*")}>Orders</button>;
}
