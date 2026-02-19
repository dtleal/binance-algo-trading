export interface Balance {
  asset: string;
  balance: number;
  available: number;
}

export interface Position {
  symbol: string;
  side: "LONG" | "SHORT";
  qty: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  leverage: number;
}

export interface Trade {
  symbol: string;
  side: "BUY" | "SELL";
  price: number;
  qty: number;
  realized_pnl: number;
  commission: number;
  commission_asset: string;
  time: number;
  order_id: string;
  buyer: boolean;
}

export interface BotState {
  symbol: string;
  strategy: string;
  state: "SCANNING" | "IN_POSITION" | "COOLDOWN";
  price?: number;
  vwap?: number;
  ema?: number;
  trend?: "up" | "down";
  counter?: number;
  confirming?: boolean;
  confirm_count?: number;
  confirm_bars?: number;
  trades_today?: number;
  max_trades_per_day?: number;
  direction?: "long" | "short";
  entry_price?: number;
  sl_price?: number;
  tp_price?: number;
  position_qty?: number;
  unrealized_pnl?: number;
  unrealized_pnl_pct?: number;
  dry_run?: boolean;
}

export type WsEvent =
  | { type: "candle"; symbol: string; strategy: string; ts: string; price: number; vwap: number; ema: number | null; trend: string; state: string; counter?: number; confirming?: boolean; unrealized_pnl?: number; unrealized_pnl_pct?: number }
  | { type: "signal"; symbol: string; strategy: string; direction: string; price: number; ts: string }
  | { type: "order";  symbol: string; strategy: string; direction: string; entry_price: number; qty: number; sl_price: number; tp_price: number; dry_run: boolean }
  | { type: "position_closed"; symbol: string; strategy: string; reason: string };
