'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '../lib/auth';

export default function Sidebar() {
  const pathname = usePathname();
  const { logout, user } = useAuth();

  const navItems = [
    { name: 'Overview', href: '/', icon: '📊' },
    { name: 'Traffic Management', href: '/traffic', icon: '🚦' },
    { name: 'Configuration', href: '/config', icon: '⚙️' },
    { name: 'API Keys', href: '/keys', icon: '🔑' },
    { name: 'Admins', href: '/admins', icon: '👥', adminOnly: true },
  ];

  return (
    <aside className="w-64 glass-panel border-r border-slate-700/50 flex flex-col h-screen sticky top-0">
      <div className="p-6">
        <h2 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-cyan-300">
          Gateway Admin
        </h2>
        {user && <p className="text-xs text-slate-400 mt-1">Logged in as {user.username}</p>}
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        {navItems.map((item) => {
          if (item.adminOnly && user && !user.isAdmin) return null;
          
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center px-4 py-3 rounded-lg transition-all duration-200 ${
                isActive
                  ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30 shadow-[0_0_15px_rgba(59,130,246,0.15)]'
                  : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
              }`}
            >
              <span className="mr-3">{item.icon}</span>
              <span className="font-medium">{item.name}</span>
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-slate-700/50">
        <button
          onClick={logout}
          className="w-full flex items-center justify-center px-4 py-2 text-sm text-red-400 hover:bg-red-500/10 hover:text-red-300 rounded transition-colors"
        >
          <span className="mr-2">🚪</span> Sign Out
        </button>
      </div>
    </aside>
  );
}
