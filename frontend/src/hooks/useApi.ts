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

export function useTrades(days = 7) {
  const { data, error, isLoading } = useSWR<{ trades: Trade[] }>(
    `/api/trades?days=${days}`,
    fetcher,
    { refreshInterval: 60_000 }
  );
  return { trades: data?.trades ?? [], error, isLoading };
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
