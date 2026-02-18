"""Daily P&L and cumulative ROI charts from Binance futures trade history."""

from collections import defaultdict
from datetime import datetime, timezone, timedelta

from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
from binance_common.configuration import ConfigurationRestAPI

from trader.config import BINANCE_API_KEY, BINANCE_SECRET_KEY, SYMBOL_CONFIGS


def _fetch_trades(days: int) -> list:
    """Fetch trade history for all configured symbols over the given number of days."""
    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        raise SystemExit(
            "BINANCE_API_KEY and BINANCE_SECRET_KEY must be set "
            "(in .env or as environment variables)"
        )

    rest_config = ConfigurationRestAPI(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_SECRET_KEY,
    )
    client = DerivativesTradingUsdsFutures(config_rest_api=rest_config)

    now = datetime.now(timezone.utc)
    end = now
    start = now - timedelta(days=days)

    all_trades = []

    for symbol in SYMBOL_CONFIGS:
        window_start = start
        while window_start < end:
            window_end = min(window_start + timedelta(days=7), end)
            start_ms = int(window_start.timestamp() * 1000)
            end_ms = int(window_end.timestamp() * 1000)

            resp = client.rest_api.account_trade_list(
                symbol=symbol,
                start_time=start_ms,
                end_time=end_ms,
                limit=1000,
            )
            trades = resp.data()
            all_trades.extend(trades)

            window_start = window_end

    return all_trades


def plot_pnl(days: int = 30) -> None:
    """Fetch trades and display daily P&L + cumulative net P&L charts."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    trades = _fetch_trades(days)

    if not trades:
        raise SystemExit(f"No trades found in the last {days} day(s)")

    # Group trades by (date, symbol)
    daily: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {
        "pnl": 0.0, "commission": 0.0, "capital": 0.0,
    }))

    for t in trades:
        ts = datetime.fromtimestamp(t.time / 1000, tz=timezone.utc)
        date_key = ts.strftime("%Y-%m-%d")
        symbol = t.symbol

        daily[date_key][symbol]["pnl"] += float(t.realized_pnl)
        daily[date_key][symbol]["commission"] += float(t.commission)

        if t.side == "SELL":
            daily[date_key][symbol]["capital"] += float(t.qty) * float(t.price)

    # Build sorted date list
    sorted_dates = sorted(daily.keys())
    symbols = sorted({s for d in daily.values() for s in d})

    # Compute daily net P&L per symbol and totals
    date_objs = [datetime.strptime(d, "%Y-%m-%d") for d in sorted_dates]

    # Per-symbol daily net P&L for stacked bars
    symbol_daily_net = {s: [] for s in symbols}
    cumulative_net = []
    running_total = 0.0

    for date_key in sorted_dates:
        day_total = 0.0
        for s in symbols:
            entry = daily[date_key].get(s)
            if entry:
                net = entry["pnl"] - entry["commission"]
            else:
                net = 0.0
            symbol_daily_net[s].append(net)
            day_total += net
        running_total += day_total
        cumulative_net.append(running_total)

    # Colors per symbol
    color_palette = ["#2196F3", "#FF9800", "#4CAF50", "#E91E63", "#9C27B0", "#00BCD4"]
    symbol_colors = {s: color_palette[i % len(color_palette)] for i, s in enumerate(symbols)}

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.suptitle(f"Futures P&L — Last {days} Days", fontsize=14, fontweight="bold")

    # --- Top: Daily net P&L stacked bars ---
    bar_width = 0.8
    bottoms_pos = [0.0] * len(sorted_dates)
    bottoms_neg = [0.0] * len(sorted_dates)

    for s in symbols:
        vals = symbol_daily_net[s]
        pos_vals = [v if v >= 0 else 0 for v in vals]
        neg_vals = [v if v < 0 else 0 for v in vals]

        ax1.bar(date_objs, pos_vals, width=bar_width, bottom=bottoms_pos,
                label=s, color=symbol_colors[s], alpha=0.85)
        ax1.bar(date_objs, neg_vals, width=bar_width, bottom=bottoms_neg,
                color=symbol_colors[s], alpha=0.85)

        bottoms_pos = [b + v for b, v in zip(bottoms_pos, pos_vals)]
        bottoms_neg = [b + v for b, v in zip(bottoms_neg, neg_vals)]

    ax1.axhline(y=0, color="gray", linewidth=0.5)
    ax1.set_ylabel("Daily Net P&L (USDT)")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(axis="y", alpha=0.3)

    # --- Bottom: Cumulative net P&L line ---
    ax2.plot(date_objs, cumulative_net, color="#2196F3", linewidth=2, marker="o", markersize=4)
    ax2.fill_between(
        date_objs, cumulative_net, 0,
        where=[v >= 0 for v in cumulative_net], color="#4CAF50", alpha=0.15,
    )
    ax2.fill_between(
        date_objs, cumulative_net, 0,
        where=[v < 0 for v in cumulative_net], color="#F44336", alpha=0.15,
    )
    ax2.axhline(y=0, color="gray", linewidth=0.5)
    ax2.set_ylabel("Cumulative Net P&L (USDT)")
    ax2.set_xlabel("Date (UTC)")
    ax2.grid(axis="y", alpha=0.3)

    # Format x-axis dates
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=45)

    plt.tight_layout()
    plt.show()
