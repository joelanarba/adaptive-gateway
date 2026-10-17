'use client';
import { useEffect, useState } from 'react';
import { fetchWithAuth } from '../lib/api';
export default function OverviewPage() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    async function load() {
      try {
        const response = await fetchWithAuth('/admin/stats');
        if (!response) return;
        if (!response.ok) throw new Error('Unable to load request statistics');
        setStats(await response.json()); setError('');
      } catch (err) { setError(err.message); }
    }
    load(); const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);
  return <div>
    <h1 className="text-3xl font-bold mb-8">Dashboard Overview</h1>
    {error && <p role="alert">{error}</p>}
    <p className="text-slate-400 mb-6">Persisted request logs; updates every five seconds. Logging can lag or drop rows during outages.</p>
    <div className="glass-panel p-6 mb-6">Total logged requests: {stats?.total_requests ?? 'Loading'}</div>
    {['by_network_quality', 'by_cache_status'].map(group => <div className="glass-panel p-6 mb-6" key={group}>
      <h2 className="text-xl mb-4">{group === 'by_network_quality' ? 'Classifications' : 'Cache outcomes'}</h2>
      {Object.entries(stats?.[group] || {}).map(([label, count]) => <p key={label}>{label}: {count}</p>)}
      {stats && Object.keys(stats[group] || {}).length === 0 && <p>No recorded requests.</p>}
    </div>)}
  </div>;
}
