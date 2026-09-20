import React, { useEffect, useState } from "react";

const API = process.env.REACT_APP_BACKEND_URL;

export default function App() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    fetch(`${API}/api/items`).then((r) => r.json()).then(setItems);
  }, []);
  return <ul>{items.map((item) => <li key={item.id}>{item.name}</li>)}</ul>;
}
