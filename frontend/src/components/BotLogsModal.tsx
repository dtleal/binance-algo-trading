import { useEffect, useRef, useState } from "react";

interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  bot: string;
}

interface BotLogsModalProps {
  botKey: string;
  symbol: string;
  onClose: () => void;
}

export default function BotLogsModal({ botKey, symbol, onClose }: BotLogsModalProps) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Connect to WebSocket for real-time logs
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const ws = new WebSocket(`${protocol}//${host}/ws/logs/${botKey}`);

    ws.onopen = () => {
      setConnected(true);
    };

    ws.onmessage = (event) => {
      const logEntry: LogEntry = JSON.parse(event.data);
      setLogs((prev) => [...prev, logEntry]);
    };

    ws.onerror = () => {
      setConnected(false);
    };

    ws.onclose = () => {
      setConnected(false);
    };

    wsRef.current = ws;

    return () => {
      ws.close();
    };
  }, [botKey]);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // Get log color based on level
  const getLogColor = (level: string) => {
    switch (level) {
      case "ERROR":
        return "text-red-400";
      case "WARNING":
        return "text-amber-400";
      case "INFO":
        return "text-gray-300";
      default:
        return "text-gray-400";
    }
  };

  // Format timestamp for display
  const formatTime = (timestamp: string) => {
    try {
      return new Date(timestamp).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    } catch {
      return timestamp;
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-6xl h-[80vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-bold text-white">{symbol} Logs</h2>
            <span className="flex items-center gap-2 text-xs px-2 py-1 rounded-full bg-gray-800">
              <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
              {connected ? "Connected" : "Disconnected"}
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors p-2 hover:bg-gray-800 rounded-lg"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Logs display */}
        <div className="flex-1 overflow-y-auto p-4 font-mono text-xs bg-gray-950/50">
          {logs.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <div className="animate-spin w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full mx-auto mb-3" />
                <p className="text-gray-500">Loading logs...</p>
              </div>
            </div>
          ) : (
            <div className="space-y-1">
              {logs.map((log, idx) => (
                <div key={idx} className="flex gap-3 hover:bg-gray-900/50 px-2 py-1 rounded">
                  <span className="text-gray-600 shrink-0 select-none">
                    {formatTime(log.timestamp)}
                  </span>
                  <span className={`shrink-0 w-16 ${getLogColor(log.level)}`}>
                    [{log.level}]
                  </span>
                  <span className={getLogColor(log.level)}>{log.message}</span>
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-700 flex items-center justify-between text-xs text-gray-500">
          <span>{logs.length} log entries</span>
          <span>Streaming live from {botKey}</span>
        </div>
      </div>
    </div>
  );
}
