'use client';
import { useEffect, useState } from 'react';
import { fetchWithAuth } from '../../lib/api';
export default function KeysPage() {
  const [keys, setKeys] = useState([]);
  const [name, setName] = useState('');
  const [secret, setSecret] = useState('');
  const [message, setMessage] = useState('');
  async function load() {
    const response = await fetchWithAuth('/admin/api-keys');
    if (!response) return;
    if (!response.ok) throw new Error('Unable to load keys');
    setKeys(await response.json());
  }
  useEffect(() => { Promise.resolve().then(load).catch(err => setMessage(err.message)); }, []);
  async function issue(event) {
    event.preventDefault(); setSecret('');
    try {
      const response = await fetchWithAuth('/admin/api-keys', { method: 'POST', body: JSON.stringify({ name }) });
      if (!response) return;
      if (!response.ok) throw new Error('Unable to issue key');
      setSecret((await response.json()).api_key); setName(''); await load();
    } catch (err) { setMessage(err.message); }
  }
  async function revoke(id) {
    try {
      const response = await fetchWithAuth(`/admin/api-keys/${id}`, { method: 'DELETE' });
      if (!response) return;
      if (!response.ok) throw new Error('Unable to revoke key');
      await load();
    } catch (err) { setMessage(err.message); }
  }
  return <div><h1 className="text-3xl font-bold mb-8">API Keys</h1><p role="status">{message}</p>
    <form className="glass-panel p-6 mb-6" onSubmit={issue}>
      <label htmlFor="key-name">Key name</label><input id="key-name" className="input-glass mx-3" required maxLength={128} value={name} onChange={e => setName(e.target.value)} />
      <button className="btn-primary" type="submit">Issue key</button>
    </form>
    {secret && <div className="glass-panel p-6 mb-6"><p>Copy this key now. It is shown only once.</p><code className="break-all">{secret}</code><button className="btn-secondary ml-3" onClick={() => setSecret('')}>Dismiss</button></div>}
    <div className="glass-panel p-6">{keys.map(key => <div className="mb-4" key={key.id}>
      <span>{key.name}: agw_{key.prefix}_•••• ({key.is_active ? 'Active' : 'Revoked'})</span>
      {key.is_active && <button className="btn-secondary ml-3" onClick={() => revoke(key.id)}>Revoke</button>}
    </div>)}{keys.length === 0 && <p>No API keys issued.</p>}</div>
  </div>;
}
