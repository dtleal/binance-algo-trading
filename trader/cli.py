import argparse
import asyncio
import signal

from trader.config import DEFAULT_SYMBOL, DEFAULT_STOP_LOSS_PCT, DEFAULT_LEVERAGE, ALL_STREAMS, SYMBOL_CONFIGS
from trader.monitor import run_monitor


def main():
    parser = argparse.ArgumentParser(description="Binance AXS Trader")
    subparsers = parser.add_subparsers(dest="command")

    # --- monitor ---
    monitor_parser = subparsers.add_parser("monitor", help="Start market monitoring")
    monitor_parser.add_argument(
        "--symbol",
        default=DEFAULT_SYMBOL,
        help=f"Trading pair to monitor (default: {DEFAULT_SYMBOL.upper()})",
    )
    monitor_parser.add_argument(
        "--streams",
        default=None,
        help=f"Comma-separated streams to subscribe to (default: all). Options: {','.join(ALL_STREAMS)}",
    )

    # --- short ---
    short_parser = subparsers.add_parser("short", help="Open a futures short position")
    short_parser.add_argument(
        "--quantity", type=float, required=True, help="Quantity of AXS to short"
    )
    short_parser.add_argument(
        "--stop-loss",
        type=float,
        default=DEFAULT_STOP_LOSS_PCT,
        help=f"Stop-loss percentage above entry (default: {DEFAULT_STOP_LOSS_PCT}%%)",
    )
    short_parser.add_argument(
        "--leverage",
        type=int,
        default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}x)",
    )

    # --- status ---
    subparsers.add_parser("status", help="Show current futures position status")

    # --- close ---
    subparsers.add_parser("close", help="Close futures short position")

    # --- history ---
    history_parser = subparsers.add_parser("history", help="Show trade history with P&L")
    history_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back (default: 7, max: 180)",
    )

    # --- pullback ---
    pb_parser = subparsers.add_parser(
        "pullback", help="Run VWAPPullback bot (bidirectional, any symbol)"
    )
    pb_parser.add_argument(
        "--symbol", required=True,
        help="Futures trading pair (e.g. btcusdt, ethusdt, solusdt)",
    )
    pb_parser.add_argument(
        "--leverage", type=int, default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}x)",
    )
    pb_parser.add_argument(
        "--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect from account)",
    )
    pb_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without placing orders (log signals only)",
    )
    pb_parser.add_argument("--tp", type=float, default=5.0, help="Take-profit %% (default: 5.0)")
    pb_parser.add_argument("--sl", type=float, default=2.5, help="Stop-loss %% (default: 2.5)")
    pb_parser.add_argument("--min-bars", type=int, default=3, help="Min consolidation bars (default: 3)")
    pb_parser.add_argument("--confirm-bars", type=int, default=2, help="Confirmation bars (default: 2)")
    pb_parser.add_argument("--vwap-prox", type=float, default=0.005, help="VWAP proximity threshold (default: 0.005)")
    pb_parser.add_argument("--vwap-window-days", type=int, default=10, help="VWAP rolling window in days (default: 10)")
    pb_parser.add_argument("--pos-size", type=float, default=0.20, help="Position size as fraction of capital (default: 0.20)")
    pb_parser.add_argument("--ema-period", type=int, default=200, help="EMA period for trend detection (default: 200)")
    pb_parser.add_argument("--max-trades", type=int, default=4, help="Max trades per UTC day (default: 4)")

    # --- pullback-v2 ---
    pb2_parser = subparsers.add_parser(
        "pullback-v2", help="Run VWAPPullback V2 bot (R-multiple trailing stop, no fixed TP)"
    )
    pb2_parser.add_argument(
        "--symbol", required=True,
        help="Futures trading pair (e.g. btcusdt, ethusdt, solusdt)",
    )
    pb2_parser.add_argument(
        "--leverage", type=int, default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}x)",
    )
    pb2_parser.add_argument(
        "--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect from account)",
    )
    pb2_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without placing orders (log signals only)",
    )
    pb2_parser.add_argument("--sl", type=float, default=2.5, help="Initial stop-loss %% (default: 2.5)")
    pb2_parser.add_argument("--min-bars", type=int, default=3, help="Min consolidation bars (default: 3)")
    pb2_parser.add_argument("--confirm-bars", type=int, default=2, help="Confirmation bars (default: 2)")
    pb2_parser.add_argument("--vwap-prox", type=float, default=0.005, help="VWAP proximity threshold (default: 0.005)")
    pb2_parser.add_argument("--vwap-window-days", type=int, default=10, help="VWAP rolling window in days (default: 10)")
    pb2_parser.add_argument("--pos-size", type=float, default=0.20, help="Position size as fraction of capital (default: 0.20)")
    pb2_parser.add_argument("--ema-period", type=int, default=200, help="EMA period for trend detection (default: 200)")
    pb2_parser.add_argument("--max-trades", type=int, default=4, help="Max trades per UTC day (default: 4)")

    # --- ema-scalp ---
    ema_parser = subparsers.add_parser(
        "ema-scalp", help="Run EMAScalp bot (bidirectional EMA crossover, trailing stop)"
    )
    ema_parser.add_argument("--symbol", required=True, help="Futures trading pair (e.g. btcusdt)")
    ema_parser.add_argument("--leverage", type=int, default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}x)")
    ema_parser.add_argument("--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect)")
    ema_parser.add_argument("--dry-run", action="store_true",
        help="Run without placing orders (log signals only)")
    ema_parser.add_argument("--sl", type=float, default=0.3,
        help="Initial stop-loss %% (default: 0.3)")
    ema_parser.add_argument("--fast-period", type=int, default=8,
        help="Fast EMA period (default: 8)")
    ema_parser.add_argument("--slow-period", type=int, default=21,
        help="Slow EMA period (default: 21)")
    ema_parser.add_argument("--vol-filter", action="store_true",
        help="Only enter on above-average volume candles")
    ema_parser.add_argument("--max-trades", type=int, default=10,
        help="Max trades per UTC day (default: 10)")
    ema_parser.add_argument("--pos-size", type=float, default=0.20,
        help="Position size as fraction of capital (default: 0.20)")
    ema_parser.add_argument("--be-r", type=float, default=2.0,
        help="R multiple at which SL reaches breakeven (default: 2.0)")
    ema_parser.add_argument("--trail-step", type=float, default=0.5,
        help="Trailing step size in R after breakeven (default: 0.5)")

    # --- orb ---
    orb_parser = subparsers.add_parser(
        "orb", help="Run ORB bot (Opening Range Breakout, trailing stop)"
    )
    orb_parser.add_argument("--symbol", required=True, help="Futures trading pair (e.g. btcusdt)")
    orb_parser.add_argument("--leverage", type=int, default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}x)")
    orb_parser.add_argument("--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect)")
    orb_parser.add_argument("--dry-run", action="store_true",
        help="Run without placing orders (log signals only)")
    orb_parser.add_argument("--sl", type=float, default=0.5,
        help="Initial stop-loss %% (default: 0.5)")
    orb_parser.add_argument("--range-mins", type=int, default=30,
        help="Opening range duration in minutes (default: 30)")
    orb_parser.add_argument("--buffer-pct", type=float, default=0.001,
        help="Breakout buffer above/below range (default: 0.001)")
    orb_parser.add_argument("--vol-filter", action="store_true",
        help="Only enter on above-average volume candles")
    orb_parser.add_argument("--max-trades", type=int, default=4,
        help="Max trades per UTC day (default: 4)")
    orb_parser.add_argument("--pos-size", type=float, default=0.20,
        help="Position size as fraction of capital (default: 0.20)")
    orb_parser.add_argument("--be-r", type=float, default=2.0,
        help="R multiple at which SL reaches breakeven (default: 2.0)")
    orb_parser.add_argument("--trail-step", type=float, default=0.5,
        help="Trailing step size in R after breakeven (default: 0.5)")

    # --- pdhl ---
    pdhl_parser = subparsers.add_parser(
        "pdhl", help="Run PDHL bot (Previous Day High/Low rejection, trailing stop)"
    )
    pdhl_parser.add_argument("--symbol", required=True, help="Futures trading pair (e.g. btcusdt)")
    pdhl_parser.add_argument("--leverage", type=int, default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}x)")
    pdhl_parser.add_argument("--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect)")
    pdhl_parser.add_argument("--dry-run", action="store_true",
        help="Run without placing orders (log signals only)")
    pdhl_parser.add_argument("--sl", type=float, default=0.3,
        help="Initial stop-loss %% (default: 0.3)")
    pdhl_parser.add_argument("--prox-pct", type=float, default=0.002,
        help="Proximity threshold to PDH/PDL (default: 0.002 = 0.2%%)")
    pdhl_parser.add_argument("--confirm-bars", type=int, default=1,
        help="Confirmation bars for rejection (default: 1)")
    pdhl_parser.add_argument("--max-trades", type=int, default=4,
        help="Max trades per UTC day (default: 4)")
    pdhl_parser.add_argument("--pos-size", type=float, default=0.20,
        help="Position size as fraction of capital (default: 0.20)")
    pdhl_parser.add_argument("--be-r", type=float, default=2.0,
        help="R multiple at which SL reaches breakeven (default: 2.0)")
    pdhl_parser.add_argument("--trail-step", type=float, default=0.5,
        help="Trailing step size in R after breakeven (default: 0.5)")
    pdhl_parser.add_argument("--tp", type=float, default=None,
        help="Fixed take-profit %% (default: None = trailing stop mode)")

    # --- plot ---
    plot_parser = subparsers.add_parser("plot", help="Show daily P&L and cumulative charts")
    plot_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back (default: 30, max: 180)",
    )

    # --- serve ---
    serve_parser = subparsers.add_parser(
        "serve", help="Start the web dashboard (FastAPI + WebSocket feed)"
    )
    serve_parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    serve_parser.add_argument(
        "--with-pullback",
        dest="pullback_symbols",
        metavar="SYMBOL",
        action="append",
        default=[],
        help="Also start VWAPPullback bot for SYMBOL (repeatable). E.g. --with-pullback axsusdt",
    )
    serve_parser.add_argument(
        "--with-momshort",
        dest="momshort_symbols",
        metavar="SYMBOL",
        action="append",
        default=[],
        help="Also start MomShort bot for SYMBOL (repeatable). E.g. --with-momshort dogeusdt",
    )
    serve_parser.add_argument(
        "--leverage",
        type=int,
        default=DEFAULT_LEVERAGE,
        help=f"Leverage for co-located bots (default: {DEFAULT_LEVERAGE}x)",
    )
    serve_parser.add_argument(
        "--dry-run", action="store_true",
        help="Start co-located bots in dry-run mode",
    )

    # --- bot ---
    bot_parser = subparsers.add_parser("bot", help="Run MomShort automated trading bot")
    bot_parser.add_argument(
        "--symbol",
        default="axsusdt",
        help="Trading pair symbol (default: axsusdt). Available: axsusdt, sandusdt, manausdt, galausdt",
    )
    bot_parser.add_argument(
        "--leverage",
        type=int,
        default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}x)",
    )
    bot_parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help="Trading capital in USDT (default: auto-detect from account)",
    )
    bot_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without placing orders (log signals only)",
    )

    args = parser.parse_args()

    if args.command == "monitor":
        streams = args.streams.split(",") if args.streams else ALL_STREAMS
        invalid = [s for s in streams if s not in ALL_STREAMS]
        if invalid:
            parser.error(f"Invalid streams: {', '.join(invalid)}. Valid: {', '.join(ALL_STREAMS)}")
        _run_async(run_monitor(args.symbol, streams))

    elif args.command == "short":
        from trader.short import FuturesShort
        fs = FuturesShort()
        _run_async(fs.open(args.quantity, args.stop_loss, args.leverage))

    elif args.command == "status":
        _run_async(_status_all())

    elif args.command == "close":
        from trader.short import FuturesShort
        fs = FuturesShort()
        _run_async(fs.close())

    elif args.command == "history":
        if args.days < 1 or args.days > 180:
            parser.error("--days must be between 1 and 180")
        from trader.short import FuturesShort
        _run_async(FuturesShort.history(days=args.days))

    elif args.command == "serve":
        _run_async(_serve(args))

    elif args.command == "pullback":
        from trader.bot_vwap_pullback import VWAPPullbackBot
        # Auto-detect interval and vwap_dist_stop from SYMBOL_CONFIGS if available
        interval = "1m"
        vwap_dist_stop = 0.0
        if args.symbol.upper() in SYMBOL_CONFIGS:
            cfg = SYMBOL_CONFIGS[args.symbol.upper()]
            interval = cfg.interval
            vwap_dist_stop = cfg.vwap_dist_stop
        bot = VWAPPullbackBot(
            symbol=args.symbol,
            leverage=args.leverage,
            capital=args.capital,
            dry_run=args.dry_run,
            tp_pct=args.tp,
            sl_pct=args.sl,
            min_bars=args.min_bars,
            confirm_bars=args.confirm_bars,
            vwap_prox=args.vwap_prox,
            vwap_window_days=args.vwap_window_days,
            pos_size_pct=args.pos_size,
            ema_period=args.ema_period,
            max_trades_per_day=args.max_trades,
            interval=interval,
            vwap_dist_stop=vwap_dist_stop,
        )
        _run_async(bot.run())

    elif args.command == "pullback-v2":
        from trader.bot_vwap_pullback_v2 import VWAPPullbackBotV2
        interval = "1m"
        if args.symbol.upper() in SYMBOL_CONFIGS:
            interval = SYMBOL_CONFIGS[args.symbol.upper()].interval
        bot = VWAPPullbackBotV2(
            symbol=args.symbol,
            leverage=args.leverage,
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=args.sl,
            min_bars=args.min_bars,
            confirm_bars=args.confirm_bars,
            vwap_prox=args.vwap_prox,
            vwap_window_days=args.vwap_window_days,
            pos_size_pct=args.pos_size,
            ema_period=args.ema_period,
            max_trades_per_day=args.max_trades,
            interval=interval,
        )
        _run_async(bot.run())

    elif args.command == "ema-scalp":
        from trader.bot_ema_scalp import EMAScalpBot
        interval = "1m"
        if args.symbol.upper() in SYMBOL_CONFIGS:
            interval = SYMBOL_CONFIGS[args.symbol.upper()].interval
        bot = EMAScalpBot(
            symbol=args.symbol,
            leverage=args.leverage,
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=args.sl,
            fast_period=args.fast_period,
            slow_period=args.slow_period,
            vol_filter=args.vol_filter,
            max_trades_per_day=args.max_trades,
            pos_size_pct=args.pos_size,
            be_r=args.be_r,
            trail_step=args.trail_step,
            interval=interval,
        )
        _run_async(bot.run())

    elif args.command == "orb":
        from trader.bot_orb import ORBBot
        interval = "1m"
        if args.symbol.upper() in SYMBOL_CONFIGS:
            interval = SYMBOL_CONFIGS[args.symbol.upper()].interval
        bot = ORBBot(
            symbol=args.symbol,
            leverage=args.leverage,
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=args.sl,
            range_mins=args.range_mins,
            buffer_pct=args.buffer_pct,
            vol_filter=args.vol_filter,
            max_trades_per_day=args.max_trades,
            pos_size_pct=args.pos_size,
            be_r=args.be_r,
            trail_step=args.trail_step,
            interval=interval,
        )
        _run_async(bot.run())

    elif args.command == "pdhl":
        from trader.bot_pdhl import PDHLBot
        interval = "1m"
        if args.symbol.upper() in SYMBOL_CONFIGS:
            interval = SYMBOL_CONFIGS[args.symbol.upper()].interval
        bot = PDHLBot(
            symbol=args.symbol,
            leverage=args.leverage,
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=args.sl,
            prox_pct=args.prox_pct,
            confirm_bars=args.confirm_bars,
            max_trades_per_day=args.max_trades,
            pos_size_pct=args.pos_size,
            be_r=args.be_r,
            trail_step=args.trail_step,
            tp_pct=args.tp,
            interval=interval,
        )
        _run_async(bot.run())

    elif args.command == "plot":
        if args.days < 1 or args.days > 180:
            parser.error("--days must be between 1 and 180")
        from trader.plot import plot_pnl
        plot_pnl(days=args.days)

    elif args.command == "bot":
        from trader.bot import MomShortBot
        from trader.config import get_symbol_config
        cfg = get_symbol_config(args.symbol)
        bot = MomShortBot(
            cfg=cfg,
            leverage=args.leverage,
            capital=args.capital,
            dry_run=args.dry_run,
        )
        _run_async(bot.run())

    else:
        parser.print_help()


async def _serve(args):
    """Start FastAPI dashboard, optionally co-located with bots."""
    import uvicorn
    from trader.api import app

    tasks = []

    # Start VWAPPullback bots
    for sym in args.pullback_symbols:
        from trader.bot_vwap_pullback import VWAPPullbackBot
        from trader.config import get_symbol_config
        # Use pre-configured settings if available
        if sym.upper() in SYMBOL_CONFIGS:
            cfg = get_symbol_config(sym)
            bot = VWAPPullbackBot(
                symbol=sym,
                leverage=args.leverage,
                dry_run=args.dry_run,
                tp_pct=cfg.tp_pct,
                sl_pct=cfg.sl_pct,
                min_bars=cfg.min_bars,
                confirm_bars=cfg.confirm_bars,
                vwap_prox=cfg.vwap_prox,
                pos_size_pct=cfg.pos_size_pct,
                vol_filter=cfg.vol_filter,
                interval=cfg.interval,
                vwap_dist_stop=cfg.vwap_dist_stop,
            )
        else:
            bot = VWAPPullbackBot(symbol=sym, leverage=args.leverage, dry_run=args.dry_run)
        tasks.append(asyncio.create_task(bot.run()))

    # Start MomShort bots
    for sym in args.momshort_symbols:
        from trader.bot import MomShortBot
        from trader.config import get_symbol_config
        cfg = get_symbol_config(sym)
        bot = MomShortBot(cfg=cfg, leverage=args.leverage, dry_run=args.dry_run)
        tasks.append(asyncio.create_task(bot.run()))

    config = uvicorn.Config(app, host=args.host, port=args.port, loop="none")
    server = uvicorn.Server(config)
    tasks.append(asyncio.create_task(server.serve()))

    print(f"Dashboard: http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}")
    await asyncio.gather(*tasks)


async def _status_all():
    """Show status for all configured symbols."""
    from trader.short import FuturesShort
    for symbol, cfg in SYMBOL_CONFIGS.items():
        fs = FuturesShort(symbol=symbol, asset=cfg.asset)
        await fs.status()
        print()


def _run_async(coro):
    """Run an async coroutine with graceful Ctrl+C handling."""
    loop = asyncio.new_event_loop()
    task = loop.create_task(coro)

    def shutdown(sig, frame):
        task.cancel()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
