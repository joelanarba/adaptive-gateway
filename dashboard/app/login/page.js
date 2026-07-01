'use client';

import { useState } from 'react';
import { useAuth } from '../../lib/auth';
import { useRouter } from 'next/navigation';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const { login } = useAuth();
  const router = useRouter();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    try {
      await login(username, password);
      router.push('/');
    } catch (err) {
      setError('Invalid username or password');
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-transparent">
      <div className="glass-panel p-8 max-w-md w-full animate-fade-in relative overflow-hidden">
        {/* Glow effect */}
        <div className="absolute top-[-50%] left-[-50%] w-[200%] h-[200%] bg-blue-500/10 blur-[100px] rounded-full pointer-events-none" />
        
        <div className="relative z-10">
          <h1 className="text-3xl font-bold text-center mb-2 bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-cyan-300">
            Adaptive Gateway
          </h1>
          <p className="text-slate-400 text-center mb-8">Sign in to manage the gateway</p>

          {error && (
            <div className="bg-red-500/20 border border-red-500/50 text-red-200 p-3 rounded mb-6 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Username</label>
              <input
                type="text"
                required
                className="input-glass w-full"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
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
                placeholder="••••••••"
              />
            </div>

            <button type="submit" className="btn-primary w-full mt-4">
              Sign In
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
