import { supabase } from "@/integrations/supabase/client";

export default function App() {
  const save = async (entry: string) => {
    const { data } = await supabase.auth.getUser();
    // The owner is set explicitly here, and defaulted in the schema as well.
    await supabase.from("journal").insert({ entry, user_id: data.user?.id });
  };
  return <button onClick={() => save("hello")}>Save</button>;
}
