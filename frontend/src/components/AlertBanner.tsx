import { useAlert } from "../contexts/AlertContext";

function fmt(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

export default function AlertBanner() {
  const { status, settings, dismiss, reset } = useAlert();

  if (!status.triggered && !status.triggeredAbsolute) return null;

  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2 bg-red-950/80 border-b border-red-900/60 text-xs flex-shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-red-400 shrink-0">⚠</span>
        <div className="flex items-center gap-3 flex-wrap min-w-0">
          {status.triggered && (
            <span className="text-red-300">
              <span className="text-red-200 font-semibold">Drawdown</span>
              {" "}pico {fmt(status.peakSeen)} → caiu abaixo de {fmt(settings.alertBelow)}
            </span>
          )}
          {status.triggeredAbsolute && (
            <span className="text-red-300">
              <span className="text-red-200 font-semibold">Limite abs.</span>
              {" "}Open P&L abaixo de {fmt(settings.absoluteThreshold)}
            </span>
          )}
          <span className="text-red-400">
            atual: <span className={`font-semibold ${status.currentPnl >= 0 ? "text-white" : "text-red-300"}`}>
              {fmt(status.currentPnl)}
            </span>
          </span>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button onClick={reset} className="px-2 py-0.5 rounded bg-red-900 hover:bg-red-800 text-red-300 transition-colors">
          Rearmar
        </button>
        <button onClick={dismiss} className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-400 transition-colors">
          ✕
        </button>
      </div>
    </div>
  );
}
