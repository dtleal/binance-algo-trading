import useSWR from "swr";
import { Balance, Position, Trade, BotState } from "../types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useBalance() {
  const { data, error, isLoading } = useSWR<{ usdt: Balance }>("/api/balance", fetcher, {
    refreshInterval: 30_000,
  });
  return { balance: data?.usdt ?? null, error, isLoading };
}

export function usePositions() {
  const { data, error, isLoading } = useSWR<{ positions: Position[] }>("/api/positions", fetcher, {
    refreshInterval: 10_000,
  });
  return { positions: data?.positions ?? [], error, isLoading };
}

export function useTrades(
  days = 7,
  dateFrom: string | null = null,
  dateTo: string | null = null,
) {
  // When a custom range is set, fetch enough days to cover dateFrom
  const fetchDays = dateFrom
    ? Math.max(days, Math.ceil((Date.now() - new Date(dateFrom).getTime()) / 86_400_000) + 2)
    : days;

  const { data, error, isLoading } = useSWR<{ trades: Trade[] }>(
    `/api/trades?days=${fetchDays}`,
    fetcher,
    { refreshInterval: 60_000 }
  );

  const allTrades = data?.trades ?? [];
  const trades = (dateFrom || dateTo)
    ? allTrades.filter(t => {
        const d = new Date(t.time).toISOString().slice(0, 10);
        if (dateFrom && d < dateFrom) return false;
        if (dateTo   && d > dateTo)   return false;
        return true;
      })
    : allTrades;

  return { trades, error, isLoading };
}

export function useKlines(symbol: string, interval = "15m", limit = 200) {
  const { data, error, isLoading } = useSWR(
    symbol ? `/api/klines/${symbol}?interval=${interval}&limit=${limit}` : null,
    fetcher,
    { revalidateOnFocus: false }
  );
  return { klines: data?.klines ?? [], error, isLoading };
}

export function useCommissions(days = 30) {
  const { data, error, isLoading } = useSWR(
    `/api/commissions?days=${days}`,
    fetcher,
    { refreshInterval: 120_000 }
  );
  return { commissions: data ?? null, error, isLoading };
}

export function useBotStates() {
  const { data, error } = useSWR<{ bots: Record<string, BotState> }>(
    "/api/bot_states",
    fetcher,
    { refreshInterval: 5_000 }
  );
  return { bots: data?.bots ?? {}, error };
}

export function useAccountSummary() {
  const { data, error, isLoading } = useSWR<{
    total_balance: number;
    available_balance: number;
    total_equity: number;
    unrealized_pnl: number;
    position_margin: number;
    open_positions: number;
    pnl_24h: number;
    equity_change_24h_pct: number;
  }>("/api/account_summary", fetcher, {
    refreshInterval: 10_000,
  });
  return { summary: data ?? null, error, isLoading };
}

export function useMarketData() {
  const { data, error, isLoading } = useSWR<{
    market_data: Record<string, {
      symbol: string;
      last_price: number;
      price_change_pct: number;
      high_24h: number;
      low_24h: number;
      volume_24h: number;
      quote_volume_24h: number;
      trades_24h: number;
    }>;
  }>("/api/market_data", fetcher, {
    refreshInterval: 30_000,
  });
  return { marketData: data?.market_data ?? {}, error, isLoading };
}

export function usePerformance() {
  const { data, error, isLoading } = useSWR<{
    bots: Record<string, {
      symbol: string;
      strategy: string;
      state: string;
      total_trades: number;
      winning_trades: number;
      win_rate: number;
      total_pnl: number;
      unrealized_pnl: number;
    }>;
    portfolio: {
      total_trades: number;
      winning_trades: number;
      win_rate: number;
      total_pnl: number;
    };
  }>("/api/performance", fetcher, {
    refreshInterval: 60_000,
  });
  return { performance: data ?? null, error, isLoading };
}
