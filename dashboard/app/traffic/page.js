'use client';

import { useState, useEffect } from 'react';
import { fetchWithAuth } from '../../lib/api';

export default function TrafficPage() {
  const [queueInfo, setQueueInfo] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadQueue();
    const interval = setInterval(loadQueue, 3000);
    return () => clearInterval(interval);
  }, []);

  const loadQueue = async () => {
    try {
      const res = await fetchWithAuth('/admin/queue');
      if (res && res.ok) {
        const data = await res.json();
        setQueueInfo(data);
      }
    } catch (err) {
      console.error('Failed to load queue', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading && !queueInfo) {
    return <div className="text-slate-400">Loading traffic data...</div>;
  }

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="text-3xl font-bold mb-8 text-white flex items-center">
        <span className="mr-3">🚦</span> Traffic Management
      </h1>

      <div className="glass-panel p-6 mb-8">
        <h2 className="text-xl font-semibold mb-4 text-slate-200">Active Request Queues</h2>
        
        {queueInfo && Object.keys(queueInfo).length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {Object.entries(queueInfo).map(([route, info]) => (
              <div key={route} className="bg-slate-800/50 p-5 rounded-lg border border-slate-700/30 relative overflow-hidden group">
                <div className="absolute top-0 left-0 w-full h-1 bg-slate-700">
                  <div 
                    className="h-full bg-blue-500 transition-all duration-500" 
                    style={{ width: `${Math.min((info.current_size / Math.max(info.queue_capacity, 1)) * 100, 100)}%` }}
                  />
                </div>
                
                <div className="flex justify-between items-center mb-4 mt-2">
                  <h3 className="font-medium text-lg text-white">{route}</h3>
                  <span className={`px-2 py-1 rounded text-xs font-medium ${
                    info.current_size >= info.queue_capacity * 0.8 
                      ? 'bg-red-500/20 text-red-300' 
                      : info.current_size >= info.queue_capacity * 0.5 
                        ? 'bg-yellow-500/20 text-yellow-300' 
                        : 'bg-green-500/20 text-green-300'
                  }`}>
                    {info.current_size} / {info.queue_capacity} items
                  </span>
                </div>
                
                <div className="text-sm text-slate-400">
                  Worker tasks processing: <span className="text-slate-200 font-medium">{info.active_workers || 0}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-12 border-2 border-dashed border-slate-700/50 rounded-lg">
            <div className="text-4xl mb-3">📭</div>
            <p className="text-slate-400">No active queues or pending traffic</p>
          </div>
        )}
      </div>
    </div>
  );
}
