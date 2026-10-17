'use client';

import { createContext, useContext, useState, useEffect } from 'react';
import { API_BASE_URL } from './api';
import { useRouter } from 'next/navigation';

const AuthContext = createContext();
function readUser(token) {
  const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
  const payload = JSON.parse(atob(part));
  if (payload.exp * 1000 <= Date.now()) throw new Error('Session expired');
  return { username: payload.sub, isAdmin: payload.is_admin === true };
}
export function AuthProvider({ children }) {
  const router = useRouter();
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const unauthorized = () => { setUser(null); router.replace('/login'); };
    window.addEventListener('adaptive-gateway:unauthorized', unauthorized);
    return () => window.removeEventListener('adaptive-gateway:unauthorized', unauthorized);
  }, [router]);
  useEffect(() => {
    Promise.resolve().then(() => {
      try {
        const token = sessionStorage.getItem('token');
        if (token) setUser(readUser(token));
      } catch {
        sessionStorage.removeItem('token');
        sessionStorage.removeItem('refresh_token');
      } finally {
        setLoading(false);
      }
    });
  }, []);
  const login = async (email, password) => {
    const response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) throw new Error('Login failed');
    const data = await response.json();
    const nextUser = readUser(data.access_token);
    if (!nextUser.isAdmin) throw new Error('Administrator access required');
    sessionStorage.setItem('token', data.access_token);
    sessionStorage.setItem('refresh_token', data.refresh_token);
    setUser(nextUser);
  };
  const logout = async () => {
    const refresh = sessionStorage.getItem('refresh_token');
    sessionStorage.removeItem('token');
    sessionStorage.removeItem('refresh_token');
    setUser(null);
    try {
      if (refresh) await fetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
    } finally { router.replace('/login'); }
  };
  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>;
}
export const useAuth = () => useContext(AuthContext);
