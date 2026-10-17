'use client';
import { useEffect, useState } from 'react';
import { fetchWithAuth } from '../../lib/api';
export default function ConfigPage() {
  const [config, setConfig] = useState(null);
  const [message, setMessage] = useState('');
  async function load() {
    const response = await fetchWithAuth('/admin/config');
    if (!response) return;
    if (!response.ok) throw new Error('Unable to load configuration');
    setConfig(await response.json());
  }
  useEffect(() => { Promise.resolve().then(load).catch(err => setMessage(err.message)); }, []);
  async function reload() {
    try {
      const response = await fetchWithAuth('/admin/reload-config', { method: 'POST' });
      if (!response) return;
      if (!response.ok) throw new Error('Invalid configuration; active policy retained');
      await load(); setMessage('Configuration reloaded for this worker.');
    } catch (err) { setMessage(err.message); }
  }
  return <div><h1 className="text-3xl font-bold mb-8">Configuration</h1>
    <button className="btn-secondary mb-6" onClick={reload}>Reload YAML</button>
    <p role="status">{message}</p>
    {Object.entries(config?.route_rules || {}).map(([service, rule]) => <div className="glass-panel p-6 mb-4" key={service}>
      <h2 className="text-xl mb-3">{service}</h2>
      <p>Target: {config.upstream_services[service] || 'Default policy'}</p>
      <p>Cache: {rule.cacheable ? `${rule.cache_ttl}s` : 'Disabled'}</p>
      <p>Optional fields: {rule.optional_fields.join(', ') || 'None'}</p>
      <p>Timeout: {rule.upstream_timeout ?? 'Global default'}</p>
      <p>Idempotent replay contract: {rule.queue_writes ? 'Enabled' : 'Disabled'}</p>
    </div>)}
  </div>;
}
