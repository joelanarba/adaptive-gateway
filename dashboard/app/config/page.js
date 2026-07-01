'use client';

import { useState, useEffect } from 'react';
import { fetchWithAuth } from '../../lib/api';

export default function ConfigPage() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const res = await fetchWithAuth('/admin/config');
      if (res && res.ok) {
        const data = await res.json();
        setConfig(data);
      }
    } catch (err) {
      console.error('Failed to load config', err);
    } finally {
      setLoading(false);
    }
  };

  const handleReload = async () => {
    try {
      const res = await fetchWithAuth('/admin/reload-config', { method: 'POST' });
      if (res && res.ok) {
        setMessage('Configuration reloaded successfully from gateway.yaml!');
        setTimeout(() => setMessage(''), 3000);
        await loadConfig();
      }
    } catch (err) {
      setMessage('Failed to reload configuration.');
      setTimeout(() => setMessage(''), 3000);
    }
  };

  if (loading) {
    return <div className="text-slate-400">Loading configuration...</div>;
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold text-white flex items-center">
          <span className="mr-3">⚙️</span> Configuration
        </h1>
        <button onClick={handleReload} className="btn-secondary flex items-center">
          <span className="mr-2">🔄</span> Reload from YAML
        </button>
      </div>

      {message && (
        <div className="bg-green-500/20 border border-green-500/50 text-green-200 p-4 rounded mb-6 animate-fade-in">
          {message}
        </div>
      )}

      <div className="glass-panel p-6">
        <h2 className="text-xl font-semibold mb-4 border-b border-slate-700/50 pb-4 text-slate-200">
          Active Route Rules
        </h2>
        
        {config?.ROUTE_RULES ? (
          <div className="space-y-4">
            {Object.entries(config.ROUTE_RULES).map(([service, rule]) => (
              <div key={service} className="bg-slate-800/50 p-4 rounded-lg border border-slate-700/30">
                <div className="flex justify-between items-start mb-2">
                  <h3 className="text-lg font-medium text-blue-300">{service}</h3>
                  <span className="bg-blue-500/20 text-blue-300 px-2 py-1 rounded text-xs">
                    Target: {rule.target}
                  </span>
                </div>
                
                <div className="grid grid-cols-2 gap-4 mt-4 text-sm">
                  <div>
                    <p className="text-slate-400">Priority</p>
                    <p className="text-slate-200">{rule.priority}</p>
                  </div>
                  <div>
                    <p className="text-slate-400">Cache TTL</p>
                    <p className="text-slate-200">{rule.cache_ttl}s</p>
                  </div>
                  <div>
                    <p className="text-slate-400">Batching</p>
                    <p className="text-slate-200">
                      {rule.batching ? (
                        <span className="text-green-400">Enabled ({rule.batch_window_ms}ms)</span>
                      ) : (
                        <span className="text-slate-500">Disabled</span>
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-slate-400">Rate Limit</p>
                    <p className="text-slate-200">{rule.rate_limit_rpm} req/min</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-slate-400">No route rules configured.</p>
        )}
      </div>
    </div>
  );
}
