import { useState } from "react";
import { useAlert } from "../contexts/AlertContext";

function fmt(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

export default function AlertSettings() {
  const { settings, status, updateSettings, reset } = useAlert();
  const [peakInput, setPeakInput] = useState(String(settings.peakTrigger));
  const [belowInput, setBelowInput] = useState(String(settings.alertBelow));

  function commitPeak() {
    const v = parseFloat(peakInput);
    if (!isNaN(v)) updateSettings({ peakTrigger: v });
    else setPeakInput(String(settings.peakTrigger));
  }
  function commitBelow() {
    const v = parseFloat(belowInput);
    if (!isNaN(v)) updateSettings({ alertBelow: v });
    else setBelowInput(String(settings.alertBelow));
  }

  const statusColor = status.triggered
    ? "text-red-400"
    : status.armed
    ? "text-emerald-400"
    : "text-gray-500";

  const statusLabel = status.triggered
    ? "Disparado"
    : status.armed
    ? `Armado · Pico: ${fmt(status.peakSeen)}`
    : settings.enabled
    ? "Aguardando…"
    : "Desativado";

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="text-base">🔔</span>
          <p className="text-sm font-semibold text-gray-300">Alerta de P&L</p>
          <span className={`text-xs ${statusColor}`}>· {statusLabel}</span>
        </div>
        <div className="flex items-center gap-3">
          {status.armed && (
            <button
              onClick={reset}
              className="text-xs text-gray-400 hover:text-gray-200 transition-colors"
            >
              Resetar
            </button>
          )}
          {/* Toggle */}
          <button
            onClick={() => updateSettings({ enabled: !settings.enabled })}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
              settings.enabled ? "bg-emerald-600" : "bg-gray-600"
            }`}
          >
            <span
              className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                settings.enabled ? "translate-x-4" : "translate-x-1"
              }`}
            />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-gray-500 mb-1.5">
            Monitorar quando P&L ≥
          </label>
          <div className="flex items-center bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 focus-within:border-emerald-500 transition-colors">
            <span className="text-gray-500 text-sm mr-1">$</span>
            <input
              type="number"
              step="0.5"
              min="0"
              value={peakInput}
              onChange={e => setPeakInput(e.target.value)}
              onBlur={commitPeak}
              onKeyDown={e => e.key === "Enter" && commitPeak()}
              disabled={!settings.enabled}
              className="flex-1 bg-transparent text-sm text-white outline-none disabled:opacity-40 w-full"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs text-gray-500 mb-1.5">
            Alertar quando cair abaixo de
          </label>
          <div className="flex items-center bg-gray-900 border border-gray-600 rounded-lg px-3 py-2 focus-within:border-amber-500 transition-colors">
            <span className="text-gray-500 text-sm mr-1">$</span>
            <input
              type="number"
              step="0.5"
              value={belowInput}
              onChange={e => setBelowInput(e.target.value)}
              onBlur={commitBelow}
              onKeyDown={e => e.key === "Enter" && commitBelow()}
              disabled={!settings.enabled}
              className="flex-1 bg-transparent text-sm text-white outline-none disabled:opacity-40 w-full"
            />
          </div>
        </div>
      </div>

      {settings.enabled && status.armed && (
        <div className="mt-3 flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <p className="text-xs text-gray-400">
            Monitorando · P&L atual:{" "}
            <span className={status.currentPnl >= 0 ? "text-emerald-400" : "text-red-400"}>
              {fmt(status.currentPnl)}
            </span>
            {" "}· Pico:{" "}
            <span className="text-white">{fmt(status.peakSeen)}</span>
          </p>
        </div>
      )}
    </div>
  );
}
