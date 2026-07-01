'use client';

import { useState, useEffect } from 'react';
import { fetchWithAuth } from '../lib/api';

export default function OverviewPage() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadStats() {
      try {
        const res = await fetchWithAuth('/admin/stats');
        if (res) {
          const data = await res.json();
          setStats(data);
        }
      } catch (err) {
        console.error('Failed to load stats', err);
      } finally {
        setLoading(false);
      }
    }
    loadStats();
    
    // Refresh every 5s
    const interval = setInterval(loadStats, 5000);
    return () => clearInterval(interval);
  }, []);

  if (loading && !stats) {
    return <div className="text-slate-400">Loading metrics...</div>;
  }

  return (
    <div>
      <h1 className="text-3xl font-bold mb-8 text-white">Dashboard Overview</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <StatCard title="Total Requests" value={stats?.total_requests || 0} icon="🌐" />
        <StatCard title="Optimizations" value={stats?.optimizations_applied || 0} icon="⚡" />
        <StatCard title="Errors" value={stats?.total_errors || 0} icon="❌" trend="down" />
        <StatCard title="Cache Hits" value={stats?.cache_hits || 0} icon="💾" />
      </div>

      <div className="glass-panel p-6">
        <h2 className="text-xl font-semibold mb-4 text-white flex items-center">
          <span className="mr-2">📈</span> Real-time Traffic
        </h2>
        <div className="h-64 flex items-end space-x-2">
          {/* Mock chart bars since we don't have historical data series yet */}
          {[...Array(20)].map((_, i) => (
            <div 
              key={i} 
              className="flex-1 bg-gradient-to-t from-blue-600 to-cyan-400 rounded-t-sm opacity-80"
              style={{ height: `${Math.random() * 100}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function StatCard({ title, value, icon }) {
  return (
    <div className="glass-panel p-6 relative overflow-hidden group">
      <div className="absolute -right-4 -top-4 text-6xl opacity-10 group-hover:scale-110 transition-transform">
        {icon}
      </div>
      <p className="text-slate-400 text-sm font-medium mb-1">{title}</p>
      <h3 className="text-3xl font-bold text-white">{value}</h3>
    </div>
  );
}
