import {
  createContext, useContext, useEffect, useRef, useState, useCallback,
} from "react";
import { usePositions } from "../hooks/useApi";

interface AlertSettings {
  enabled: boolean;
  peakTrigger: number;      // arm when unrealized >= this
  alertBelow: number;       // fire when unrealized drops below this (drawdown alert)
  absoluteEnabled: boolean; // enable absolute threshold alert
  absoluteThreshold: number; // fire directly when unrealized < this (no arm needed)
}

interface AlertStatus {
  armed: boolean;
  peakSeen: number;
  triggered: boolean;
  triggeredAbsolute: boolean;
  currentPnl: number;
}

interface AlertContextValue {
  settings: AlertSettings;
  status: AlertStatus;
  updateSettings: (s: Partial<AlertSettings>) => void;
  dismiss: () => void;
  reset: () => void;
}

const DEFAULT_SETTINGS: AlertSettings = {
  enabled: false, peakTrigger: 3, alertBelow: 0.5,
  absoluteEnabled: false, absoluteThreshold: -2,
};
const STORAGE_KEY = "pnl_alert_settings";

const AlertContext = createContext<AlertContextValue | null>(null);

function loadSettings(): AlertSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {}
  return DEFAULT_SETTINGS;
}

function fmt(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

export function AlertProvider({ children }: { children: React.ReactNode }) {
  const { positions } = usePositions();
  const [settings, setSettings] = useState<AlertSettings>(loadSettings);
  const [status, setStatus] = useState<AlertStatus>({
    armed: false, peakSeen: 0, triggered: false, triggeredAbsolute: false, currentPnl: 0,
  });
  // Use ref for status inside the effect to avoid stale closures
  const statusRef = useRef(status);
  statusRef.current = status;

  const updateSettings = useCallback((s: Partial<AlertSettings>) => {
    setSettings(prev => {
      const next = { ...prev, ...s };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const dismiss = useCallback(() => {
    setStatus(prev => ({ ...prev, triggered: false, triggeredAbsolute: false }));
  }, []);

  const reset = useCallback(() => {
    setStatus({ armed: false, peakSeen: 0, triggered: false, triggeredAbsolute: false, currentPnl: 0 });
  }, []);

  // Request browser notification permission when user enables alerts
  useEffect(() => {
    if (settings.enabled && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, [settings.enabled]);

  // Core monitoring logic — runs every time positions update (every 5s)
  useEffect(() => {
    const total = positions.reduce((s, p) => s + p.unrealized_pnl, 0);
    const s = statusRef.current;

    // Auto-reset when all positions close
    if (positions.length === 0 && (s.armed || s.triggeredAbsolute)) {
      setStatus({ armed: false, peakSeen: 0, triggered: false, triggeredAbsolute: false, currentPnl: 0 });
      return;
    }

    if (!settings.enabled) {
      setStatus(prev => ({ ...prev, currentPnl: total }));
      return;
    }

    let { armed, peakSeen, triggered, triggeredAbsolute } = s;

    // Arm: unrealized reached peak trigger
    if (!armed && total >= settings.peakTrigger) {
      armed = true;
      peakSeen = total;
    }

    // Update high-water mark
    if (armed && total > peakSeen) {
      peakSeen = total;
    }

    // Fire drawdown alert: armed and dropped below alertBelow
    if (armed && !triggered && total < settings.alertBelow) {
      triggered = true;

      if ("Notification" in window && Notification.permission === "granted") {
        try {
          new Notification("⚠️ Alerta P&L — Drawdown", {
            body: `P&L caiu para ${fmt(total)} (pico: ${fmt(peakSeen)})`,
            icon: "/favicon.ico",
          });
        } catch {}
      }
    }

    // Fire absolute alert: P&L dropped below absolute threshold (no arm needed)
    if (settings.absoluteEnabled && !triggeredAbsolute && total < settings.absoluteThreshold) {
      triggeredAbsolute = true;

      if ("Notification" in window && Notification.permission === "granted") {
        try {
          new Notification("⚠️ Alerta P&L — Limite Absoluto", {
            body: `P&L abaixo do limite: ${fmt(total)} (limite: ${fmt(settings.absoluteThreshold)})`,
            icon: "/favicon.ico",
          });
        } catch {}
      }
    }

    setStatus({ armed, peakSeen, triggered, triggeredAbsolute, currentPnl: total });
  }, [positions, settings.enabled, settings.peakTrigger, settings.alertBelow, settings.absoluteEnabled, settings.absoluteThreshold]);

  return (
    <AlertContext.Provider value={{ settings, status, updateSettings, dismiss, reset }}>
      {children}
    </AlertContext.Provider>
  );
}

export function useAlert(): AlertContextValue {
  const ctx = useContext(AlertContext);
  if (!ctx) throw new Error("useAlert must be used within AlertProvider");
  return ctx;
}
