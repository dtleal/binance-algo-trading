import { useState } from "react";
import { useAlert } from "../contexts/AlertContext";

function fmt(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function CompactInput({
  value, onChange, onCommit, disabled,
}: { value: string; onChange: (v: string) => void; onCommit: () => void; disabled?: boolean }) {
  return (
    <input
      type="number"
      step="0.5"
      value={value}
      onChange={e => onChange(e.target.value)}
      onBlur={onCommit}
      onKeyDown={e => e.key === "Enter" && onCommit()}
      disabled={disabled}
      className="w-14 bg-gray-900 border border-gray-700 rounded px-1.5 py-0.5 text-xs text-white text-center
        outline-none focus:border-gray-500 disabled:opacity-40 transition-colors [appearance:textfield]
        [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
    />
  );
}

export default function AlertSettings() {
  const { settings, status, updateSettings, reset } = useAlert();
  const [peakInput, setPeakInput] = useState(String(settings.peakTrigger));
  const [belowInput, setBelowInput] = useState(String(settings.alertBelow));
  const [absInput, setAbsInput] = useState(String(settings.absoluteThreshold));

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
  function commitAbs() {
    const v = parseFloat(absInput);
    if (!isNaN(v)) updateSettings({ absoluteThreshold: v });
    else setAbsInput(String(settings.absoluteThreshold));
  }

  const anyTriggered = status.triggered || status.triggeredAbsolute;

  const statusDot = anyTriggered
    ? "bg-red-400"
    : status.armed
    ? "bg-emerald-400 animate-pulse"
    : settings.enabled
    ? "bg-gray-600"
    : "bg-gray-700";

  const statusLabel = anyTriggered
    ? "Disparado"
    : status.armed
    ? `Armado · pico ${fmt(status.peakSeen)}`
    : settings.enabled
    ? "Aguardando"
    : null;

  return (
    <div className="flex items-center gap-3 bg-gray-800/40 border border-gray-700/50 rounded-lg px-3 py-2 text-xs flex-wrap">
      {/* Icon + label */}
      <div className="flex items-center gap-1.5 shrink-0">
        <span className="text-gray-500 text-sm">🔔</span>
        <span className="text-gray-500 font-medium">Alertas</span>
      </div>

      {/* Master toggle */}
      <button
        onClick={() => updateSettings({ enabled: !settings.enabled })}
        className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors ${
          settings.enabled ? "bg-emerald-600" : "bg-gray-700"
        }`}
      >
        <span className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
          settings.enabled ? "translate-x-3.5" : "translate-x-0.5"
        }`} />
      </button>

      {settings.enabled && (
        <>
          <div className="h-3 w-px bg-gray-700 shrink-0" />

          {/* Drawdown config */}
          <span className="text-gray-600 shrink-0">arm ≥</span>
          <CompactInput value={peakInput} onChange={setPeakInput} onCommit={commitPeak} />
          <span className="text-gray-600 shrink-0">alerta &lt;</span>
          <CompactInput value={belowInput} onChange={setBelowInput} onCommit={commitBelow} />

          <div className="h-3 w-px bg-gray-700 shrink-0" />

          {/* Absolute threshold */}
          <span className="text-gray-600 shrink-0">abs &lt;</span>
          <CompactInput value={absInput} onChange={setAbsInput} onCommit={commitAbs} disabled={!settings.absoluteEnabled} />
          <button
            onClick={() => updateSettings({ absoluteEnabled: !settings.absoluteEnabled })}
            className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors ${
              settings.absoluteEnabled ? "bg-orange-600" : "bg-gray-700"
            }`}
          >
            <span className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
              settings.absoluteEnabled ? "translate-x-3.5" : "translate-x-0.5"
            }`} />
          </button>
        </>
      )}

      {/* Status — pushed to right */}
      <div className="ml-auto flex items-center gap-2 shrink-0">
        {settings.enabled && statusLabel && (
          <div className="flex items-center gap-1.5">
            <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot}`} />
            <span className={anyTriggered ? "text-red-400" : status.armed ? "text-emerald-400" : "text-gray-500"}>
              {statusLabel}
            </span>
          </div>
        )}
        {settings.enabled && status.armed && !anyTriggered && (
          <button
            onClick={reset}
            className="text-gray-600 hover:text-gray-400 transition-colors"
          >
            reset
          </button>
        )}
        {settings.enabled && anyTriggered && (
          <button
            onClick={reset}
            className="text-red-500 hover:text-red-300 transition-colors"
          >
            rearmar
          </button>
        )}
      </div>
    </div>
  );
}
