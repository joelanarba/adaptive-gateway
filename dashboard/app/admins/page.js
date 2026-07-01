'use client';

import { useState } from 'react';
import { fetchWithAuth } from '../../lib/api';

export default function AdminsPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState({ type: '', text: '' });
  const [loading, setLoading] = useState(false);

  const handleCreateAdmin = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage({ type: '', text: '' });

    try {
      const res = await fetchWithAuth('/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          username,
          password,
          is_admin: true,
          is_active: true
        })
      });

      if (!res) return; // Handled by fetchWithAuth if unauthorized

      if (res.ok) {
        setMessage({ type: 'success', text: `Admin user '${username}' created successfully.` });
        setUsername('');
        setPassword('');
      } else {
        const err = await res.json();
        setMessage({ type: 'error', text: err.detail || 'Failed to create admin' });
      }
    } catch (err) {
      setMessage({ type: 'error', text: 'Network error or server unavailable' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-3xl font-bold mb-8 text-white flex items-center">
        <span className="mr-3">👥</span> Admin Management
      </h1>

      <div className="glass-panel p-8">
        <h2 className="text-xl font-semibold mb-6 text-slate-200">Create New Admin User</h2>
        
        {message.text && (
          <div className={`p-4 rounded mb-6 text-sm border ${message.type === 'success' ? 'bg-green-500/20 border-green-500/50 text-green-200' : 'bg-red-500/20 border-red-500/50 text-red-200'}`}>
            {message.text}
          </div>
        )}

        <form onSubmit={handleCreateAdmin} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-2">Username</label>
            <input
              type="text"
              required
              className="input-glass w-full"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="new_admin"
            />
          </div>
          
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-2">Password</label>
            <input
              type="password"
              required
              className="input-glass w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Secure password"
              minLength={6}
            />
          </div>

          <button 
            type="submit" 
            className="btn-primary w-full mt-4 flex justify-center items-center"
            disabled={loading}
          >
            {loading ? (
              <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              'Create Admin'
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
