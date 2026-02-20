import { useState } from "react";
import { NavLink } from "react-router-dom";

const links = [
  { to: "/",            label: "Overview",    icon: "▦" },
  { to: "/bots",        label: "Bots",        icon: "⚡" },
  { to: "/positions",   label: "Positions",   icon: "📊" },
  { to: "/history",     label: "History",     icon: "📋" },
  { to: "/commissions", label: "Commissions", icon: "💸" },
];

export default function Sidebar({ connected }: { connected: boolean }) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <>
      {/* Mobile Header */}
      <header className="lg:hidden fixed top-0 left-0 right-0 z-50 bg-gray-900 border-b border-gray-800 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="text-gray-400 hover:text-white p-2 -ml-2"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              {mobileMenuOpen ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              )}
            </svg>
          </button>
          <span className="text-emerald-400 font-bold text-lg tracking-tight">⬡ Trader</span>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} />
          {connected ? "live" : "off"}
        </div>
      </header>

      {/* Mobile Menu Overlay */}
      {mobileMenuOpen && (
        <div
          className="lg:hidden fixed inset-0 bg-black/50 z-40"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`
        w-52 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col
        lg:relative lg:translate-x-0
        fixed inset-y-0 left-0 z-50 transition-transform duration-300
        ${mobileMenuOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        {/* Brand - Desktop only */}
        <div className="hidden lg:block px-5 py-5 border-b border-gray-800">
          <span className="text-emerald-400 font-bold text-lg tracking-tight">⬡ Trader</span>
          <div className="mt-1 flex items-center gap-1.5 text-xs text-gray-500">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} />
            {connected ? "live" : "disconnected"}
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-4 flex flex-col gap-0.5 px-2 mt-14 lg:mt-0">
          {links.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setMobileMenuOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ` +
                (isActive
                  ? "bg-emerald-500/20 text-emerald-400 font-medium"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800")
              }
            >
              <span className="text-base">{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="px-5 py-4 border-t border-gray-800 text-xs text-gray-600">
          USDT-M Futures
        </div>
      </aside>
    </>
  );
}
