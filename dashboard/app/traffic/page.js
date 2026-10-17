'use client';
import { useEffect, useState } from 'react';
import { fetchWithAuth } from '../../lib/api';
export default function TrafficPage() {
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    async function load() {
      try {
        const response = await fetchWithAuth('/admin/queue');
        if (!response) return;
        if (!response.ok) throw new Error('Unable to load queue');
        setQueue(await response.json()); setError('');
      } catch (err) { setError(err.message); }
    }
    load(); const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);
  return <div><h1 className="text-3xl font-bold mb-8">Offline write queue</h1>
    {error && <p role="alert">{error}</p>}
    <div className="glass-panel p-6">
      {queue ? <><p>Stream: {queue.stream_key}</p><p>Unacknowledged entries: {queue.depth}</p><p>Delivered and pending: {queue.pending}</p></> : <p>Loading queue status...</p>}
      <p className="text-slate-400 mt-4">A queued write is accepted for replay, not confirmed committed. Check diagnostics if Redis is unavailable.</p>
    </div></div>;
}
