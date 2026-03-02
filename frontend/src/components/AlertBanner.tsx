import { useAlert } from "../contexts/AlertContext";

function fmt(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

export default function AlertBanner() {
  const { status, settings, dismiss, reset } = useAlert();

  if (!status.triggered) return null;

  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3 bg-red-900/80 border-b border-red-700 text-sm flex-shrink-0">
      <div className="flex items-center gap-3 min-w-0">
        <span className="text-red-300 text-base flex-shrink-0">⚠️</span>
        <div className="min-w-0">
          <span className="font-semibold text-red-200">Alerta P&L — </span>
          <span className="text-red-300">
            Atingiu pico de{" "}
            <span className="font-bold text-white">{fmt(status.peakSeen)}</span>
            {" "}e caiu abaixo de{" "}
            <span className="font-bold text-white">{fmt(settings.alertBelow)}</span>
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3 flex-shrink-0 text-xs">
        <span className="text-red-400">
          Atual: <span className={`font-bold ${status.currentPnl >= 0 ? "text-white" : "text-red-300"}`}>
            {fmt(status.currentPnl)}
          </span>
        </span>
        <button
          onClick={reset}
          className="px-2.5 py-1 rounded bg-red-800 hover:bg-red-700 text-red-200 transition-colors"
        >
          Rearmar
        </button>
        <button
          onClick={dismiss}
          className="px-2.5 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
        >
          Dispensar
        </button>
      </div>
    </div>
  );
}
