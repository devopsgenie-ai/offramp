import { useEffect, useState } from "react";
import { supabase } from "@/integrations/supabase/client";

export default function App() {
  const [recipes, setRecipes] = useState<any[]>([]);
  useEffect(() => {
    supabase.from("recipes").select("*").then(({ data }) => setRecipes(data ?? []));
  }, []);
  return <ul>{recipes.map((r) => <li key={r.id}>{r.name}</li>)}</ul>;
}
