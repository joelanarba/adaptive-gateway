'use client';

import { useAuth } from '../lib/auth';
import Sidebar from './Sidebar';
import { useRouter, usePathname } from 'next/navigation';
import { useEffect } from 'react';

export default function DashboardShell({ children }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user && pathname !== '/login') {
      router.push('/login');
    }
  }, [user, loading, pathname, router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
      </div>
    );
  }

  // If on login page, just render the page
  if (pathname === '/login') {
    return children;
  }

  // Don't render content if not logged in
  if (!user) {
    return null; 
  }

  return (
    <div className="flex min-h-screen bg-transparent">
      <Sidebar />
      <main className="flex-1 p-8 overflow-auto animate-fade-in relative">
        <div className="max-w-6xl mx-auto relative z-10">
          {children}
        </div>
      </main>
    </div>
  );
}
