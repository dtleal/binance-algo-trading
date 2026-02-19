import { NavLink } from "react-router-dom";

const links = [
  { to: "/",            label: "Overview",    icon: "▦" },
  { to: "/bots",        label: "Bots",        icon: "⚡" },
  { to: "/positions",   label: "Positions",   icon: "📊" },
  { to: "/history",     label: "History",     icon: "📋" },
  { to: "/commissions", label: "Commissions", icon: "💸" },
];

export default function Sidebar({ connected }: { connected: boolean }) {
  return (
    <aside className="w-52 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">
      {/* Brand */}
      <div className="px-5 py-5 border-b border-gray-800">
        <span className="text-emerald-400 font-bold text-lg tracking-tight">⬡ Trader</span>
        <div className="mt-1 flex items-center gap-1.5 text-xs text-gray-500">
          <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} />
          {connected ? "live" : "disconnected"}
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-4 flex flex-col gap-0.5 px-2">
        {links.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
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
  );
}
