import { useEffect, useState } from 'react';
import axios from 'axios';
import './App.css';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

function App() {
  const [checks, setChecks] = useState([]);
  const [name, setName] = useState('');

  const load = async () => {
    const response = await axios.get(`${API}/status`);
    setChecks(response.data);
  };

  useEffect(() => {
    load();
  }, []);

  const add = async (event) => {
    event.preventDefault();
    if (!name) return;
    await axios.post(`${API}/status`, { client_name: name });
    setName('');
    load();
  };

  return (
    <div className="App">
      <h1>Status board</h1>
      <form onSubmit={add}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="client name" />
        <button type="submit">Add</button>
      </form>
      <ul>
        {checks.map((check) => (
          <li key={check.id}>
            {check.client_name} — {check.timestamp}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default App;
