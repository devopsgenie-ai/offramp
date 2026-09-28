import { useEffect, useState } from "react";
import { supabase } from "@/integrations/supabase/client";
import { summarise } from "@/lib/ai";

export default function Board() {
  const [tasks, setTasks] = useState<any[]>([]);
  useEffect(() => {
    supabase.from("tasks").select("*").then(({ data }) => setTasks(data ?? []));
  }, []);
  return <ul onClick={() => summarise(tasks)}>{tasks.map((t) => <li key={t.id}>{t.title}</li>)}</ul>;
}
