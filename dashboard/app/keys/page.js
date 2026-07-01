'use client';

import { useState, useEffect } from 'react';
import { fetchWithAuth } from '../../lib/api';

export default function KeysPage() {
  const [keys, setKeys] = useState({});
  const [loading, setLoading] = useState(true);
  const [copiedKey, setCopiedKey] = useState(null);

  useEffect(() => {
    loadKeys();
  }, []);

  const loadKeys = async () => {
    try {
      const res = await fetchWithAuth('/admin/keys');
      if (res && res.ok) {
        const data = await res.json();
        setKeys(data);
      }
    } catch (err) {
      console.error('Failed to load keys', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = (key) => {
    navigator.clipboard.writeText(key);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  if (loading) {
    return <div className="text-slate-400">Loading API keys...</div>;
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-3xl font-bold mb-8 text-white flex items-center">
        <span className="mr-3">🔑</span> API Keys
      </h1>

      <div className="glass-panel p-6">
        <h2 className="text-xl font-semibold mb-6 text-slate-200">Registered Services</h2>
        
        {Object.keys(keys).length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-700 text-slate-400 text-sm">
                  <th className="py-3 px-4 font-medium">Service Name</th>
                  <th className="py-3 px-4 font-medium">API Key</th>
                  <th className="py-3 px-4 font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {Object.entries(keys).map(([service, key]) => (
                  <tr key={service} className="hover:bg-slate-800/30 transition-colors">
                    <td className="py-4 px-4 font-medium text-blue-300">{service}</td>
                    <td className="py-4 px-4 font-mono text-sm text-slate-300">
                      {key.substring(0, 8)}••••••••••••••••
                    </td>
                    <td className="py-4 px-4">
                      <button 
                        onClick={() => handleCopy(key)}
                        className={`text-sm px-3 py-1.5 rounded transition-all ${
                          copiedKey === key 
                            ? 'bg-green-500/20 text-green-300 border border-green-500/50' 
                            : 'bg-slate-700/50 text-slate-300 hover:bg-slate-700 hover:text-white border border-slate-600/50'
                        }`}
                      >
                        {copiedKey === key ? 'Copied!' : 'Copy Key'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-slate-400">No API keys registered.</p>
        )}
      </div>
    </div>
  );
}
