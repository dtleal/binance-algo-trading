import argparse
import asyncio
import os
import signal

from trader.config import DEFAULT_SYMBOL, DEFAULT_STOP_LOSS_PCT, DEFAULT_LEVERAGE, ALL_STREAMS, SYMBOL_CONFIGS
from trader.monitor import run_monitor

ALLOW_CONFIG_FALLBACK = os.getenv("ALLOW_CONFIG_FALLBACK", "0").lower() in {"1", "true", "yes"}


def _load_symbol_config(symbol: str, strategy_name: str):
    """Load (SymbolConfig, extras) from DB; fallback to Python config if DB unavailable.

    Also checks active flag — exits with 0 if symbol or strategy is disabled.
    extras dict has: ema_period, max_trades_per_day, fast_period, slow_period,
                     range_mins, pdhl_prox_pct, be_r, trail_step, leverage,
                     be_profit_usd, active.
    """
    from dotenv import load_dotenv as _ld
    _ld()

    async def _fetch():
        try:
            import db
            from db.queries.symbol_config import get_symbol_config as _db_cfg
            from db.queries.strategy import is_bot_active
            await db.init_pool()
            pool = db.get_pool()
            active, reason = await is_bot_active(pool, symbol, strategy_name)
            if not active:
                await db.close_pool()
                return None, reason
            cfg, extras = await _db_cfg(pool, symbol)
            await db.close_pool()
            return (cfg, extras), ""
        except Exception as e:
            return "fallback", str(e)

    result, reason = asyncio.run(_fetch())

    if result is None:
        print(f"⛔ {symbol} ({strategy_name}): {reason} — bot não iniciado")
        raise SystemExit(0)

    if result == "fallback":
        if not ALLOW_CONFIG_FALLBACK:
            from trader.notifications import notify_startup_error_sync
            interval = None
            leverage = None
            pos_size_pct = None
            try:
                from trader.config import get_symbol_config
                local_cfg = get_symbol_config(symbol)
                interval = local_cfg.interval
                leverage = local_cfg.leverage
                pos_size_pct = local_cfg.pos_size_pct
            except Exception:
                pass
            notify_startup_error_sync(
                symbol=symbol,
                strategy=strategy_name,
                interval=interval,
                leverage=leverage,
                pos_size_pct=pos_size_pct,
                error=f"DB unavailable ({strategy_name}): {reason}",
                stage="config-load",
            )
            print(
                f"⛔ DB unavailable for {symbol} ({strategy_name}) — bot não iniciado. "
                f"Reason: {reason}"
            )
            raise SystemExit(1)
        # Optional fallback mode (explicit opt-in via ALLOW_CONFIG_FALLBACK=1)
        from trader.config import get_symbol_config
        cfg = get_symbol_config(symbol)
        print(
            f"⚠️ DB unavailable for {symbol} ({strategy_name}) — using trader/config.py "
            f"(ALLOW_CONFIG_FALLBACK=1). Reason: {reason}"
        )
        extras = {
            "strategy_name": strategy_name,
            "ema_period": None, "max_trades_per_day": None,
            "fast_period": None, "slow_period": None,
            "range_mins": None, "pdhl_prox_pct": None,
            "be_r": None, "trail_step": None,
            "be_profit_usd": None,
            "leverage": cfg.leverage, "active": True,
            "_source": "fallback",
        }
        return cfg, extras

    cfg, extras = result
    extras["_source"] = "db"
    return cfg, extras


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
        "--leverage", type=int, default=None,
        help="Leverage multiplier (default: from DB/config)",
    )
    pb_parser.add_argument(
        "--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect from account)",
    )
    pb_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without placing orders (log signals only)",
    )
    pb_parser.add_argument("--tp", type=float, default=None, help="Take-profit %% (default: from DB/config)")
    pb_parser.add_argument("--sl", type=float, default=None, help="Stop-loss %% (default: from DB/config)")
    pb_parser.add_argument("--min-bars", type=int, default=None, help="Min consolidation bars (default: from DB/config)")
    pb_parser.add_argument("--confirm-bars", type=int, default=None, help="Confirmation bars (default: from DB/config)")
    pb_parser.add_argument("--vwap-prox", type=float, default=None, help="VWAP proximity threshold (default: from DB/config)")
    pb_parser.add_argument("--vwap-window-days", type=int, default=10, help="VWAP rolling window in days (default: 10)")
    pb_parser.add_argument("--pos-size", type=float, default=None, help="Position size as fraction of capital (default: from DB/config)")
    pb_parser.add_argument("--ema-period", type=int, default=None, help="EMA period for trend detection (default: from DB/config)")
    pb_parser.add_argument("--max-trades", type=int, default=None, help="Max trades per UTC day (default: from DB/config)")

    # --- pullback-v2 ---
    pb2_parser = subparsers.add_parser(
        "pullback-v2", help="Run VWAPPullback V2 bot (R-multiple trailing stop, no fixed TP)"
    )
    pb2_parser.add_argument(
        "--symbol", required=True,
        help="Futures trading pair (e.g. btcusdt, ethusdt, solusdt)",
    )
    pb2_parser.add_argument(
        "--leverage", type=int, default=None,
        help="Leverage multiplier (default: from DB/config)",
    )
    pb2_parser.add_argument(
        "--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect from account)",
    )
    pb2_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without placing orders (log signals only)",
    )
    pb2_parser.add_argument("--sl", type=float, default=None, help="Initial stop-loss %% (default: from DB/config)")
    pb2_parser.add_argument("--min-bars", type=int, default=None, help="Min consolidation bars (default: from DB/config)")
    pb2_parser.add_argument("--confirm-bars", type=int, default=None, help="Confirmation bars (default: from DB/config)")
    pb2_parser.add_argument("--vwap-prox", type=float, default=None, help="VWAP proximity threshold (default: from DB/config)")
    pb2_parser.add_argument("--vwap-window-days", type=int, default=10, help="VWAP rolling window in days (default: 10)")
    pb2_parser.add_argument("--pos-size", type=float, default=None, help="Position size as fraction of capital (default: from DB/config)")
    pb2_parser.add_argument("--ema-period", type=int, default=None, help="EMA period for trend detection (default: from DB/config)")
    pb2_parser.add_argument("--max-trades", type=int, default=None, help="Max trades per UTC day (default: from DB/config)")

    # --- ema-scalp ---
    ema_parser = subparsers.add_parser(
        "ema-scalp", help="Run EMAScalp bot (bidirectional EMA crossover, trailing stop)"
    )
    ema_parser.add_argument("--symbol", required=True, help="Futures trading pair (e.g. btcusdt)")
    ema_parser.add_argument("--leverage", type=int, default=None,
        help="Leverage multiplier (default: from DB/config)")
    ema_parser.add_argument("--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect)")
    ema_parser.add_argument("--dry-run", action="store_true",
        help="Run without placing orders (log signals only)")
    ema_parser.add_argument("--sl", type=float, default=None,
        help="Initial stop-loss %% (default: from DB/config)")
    ema_parser.add_argument("--fast-period", type=int, default=None,
        help="Fast EMA period (default: from DB/config)")
    ema_parser.add_argument("--slow-period", type=int, default=None,
        help="Slow EMA period (default: from DB/config)")
    ema_parser.add_argument("--vol-filter", action="store_true",
        help="Only enter on above-average volume candles")
    ema_parser.add_argument("--max-trades", type=int, default=None,
        help="Max trades per UTC day (default: from DB/config)")
    ema_parser.add_argument("--pos-size", type=float, default=None,
        help="Position size as fraction of capital (default: from DB/config)")
    ema_parser.add_argument("--be-r", type=float, default=None,
        help="R multiple at which SL reaches breakeven (default: from DB/config)")
    ema_parser.add_argument("--trail-step", type=float, default=None,
        help="Trailing step size in R after breakeven (default: from DB/config)")

    # --- orb ---
    orb_parser = subparsers.add_parser(
        "orb", help="Run ORB bot (Opening Range Breakout, trailing stop)"
    )
    orb_parser.add_argument("--symbol", required=True, help="Futures trading pair (e.g. btcusdt)")
    orb_parser.add_argument("--leverage", type=int, default=None,
        help="Leverage multiplier (default: from DB/config)")
    orb_parser.add_argument("--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect)")
    orb_parser.add_argument("--dry-run", action="store_true",
        help="Run without placing orders (log signals only)")
    orb_parser.add_argument("--sl", type=float, default=None,
        help="Initial stop-loss %% (default: from DB/config)")
    orb_parser.add_argument("--range-mins", type=int, default=None,
        help="Opening range duration in minutes (default: from DB/config)")
    orb_parser.add_argument("--buffer-pct", type=float, default=0.001,
        help="Breakout buffer above/below range (default: 0.001)")
    orb_parser.add_argument("--vol-filter", action="store_true",
        help="Only enter on above-average volume candles")
    orb_parser.add_argument("--max-trades", type=int, default=None,
        help="Max trades per UTC day (default: from DB/config)")
    orb_parser.add_argument("--pos-size", type=float, default=None,
        help="Position size as fraction of capital (default: from DB/config)")
    orb_parser.add_argument("--be-r", type=float, default=None,
        help="R multiple at which SL reaches breakeven (default: from DB/config)")
    orb_parser.add_argument("--trail-step", type=float, default=None,
        help="Trailing step size in R after breakeven (default: from DB/config)")

    # --- pdhl ---
    pdhl_parser = subparsers.add_parser(
        "pdhl", help="Run PDHL bot (Previous Day High/Low rejection, trailing stop)"
    )
    pdhl_parser.add_argument("--symbol", required=True, help="Futures trading pair (e.g. btcusdt)")
    pdhl_parser.add_argument("--leverage", type=int, default=None,
        help="Leverage multiplier (default: from DB/config)")
    pdhl_parser.add_argument("--capital", type=float, default=None,
        help="Trading capital in USDT (default: auto-detect)")
    pdhl_parser.add_argument("--dry-run", action="store_true",
        help="Run without placing orders (log signals only)")
    pdhl_parser.add_argument("--sl", type=float, default=None,
        help="Initial stop-loss %% (default: from DB/config)")
    pdhl_parser.add_argument("--prox-pct", type=float, default=None,
        help="Proximity threshold to PDH/PDL (default: from DB/config)")
    pdhl_parser.add_argument("--confirm-bars", type=int, default=None,
        help="Confirmation bars for rejection (default: from DB/config)")
    pdhl_parser.add_argument("--max-trades", type=int, default=None,
        help="Max trades per UTC day (default: from DB/config)")
    pdhl_parser.add_argument("--pos-size", type=float, default=None,
        help="Position size as fraction of capital (default: from DB/config)")
    pdhl_parser.add_argument("--be-r", type=float, default=None,
        help="R multiple at which SL reaches breakeven (default: from DB/config)")
    pdhl_parser.add_argument("--trail-step", type=float, default=None,
        help="Trailing step size in R after breakeven (default: from DB/config)")
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
        default=None,
        help="Leverage multiplier (default: from DB/config)",
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

    def _resolve(db_val=None, cli_val=None, cfg_val=None, hardcoded_default=None):
        """Resolve value precedence: DB > CLI > symbol config > hardcoded default."""
        if db_val is not None:
            return db_val
        if cli_val is not None:
            return cli_val
        if cfg_val is not None:
            return cfg_val
        return hardcoded_default

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
        cfg, extras = _load_symbol_config(args.symbol.upper(), "VWAPPullback")
        _db = extras.get("_source") == "db"
        from trader.bot_vwap_pullback import VWAPPullbackBot
        bot = VWAPPullbackBot(
            symbol=args.symbol,
            leverage=_resolve(extras["leverage"] if _db else None, args.leverage, cfg.leverage, DEFAULT_LEVERAGE),
            capital=args.capital,
            dry_run=args.dry_run,
            tp_pct=_resolve(cfg.tp_pct if _db else None, args.tp, cfg.tp_pct, 5.0),
            sl_pct=_resolve(cfg.sl_pct if _db else None, args.sl, cfg.sl_pct, 2.5),
            min_bars=_resolve(cfg.min_bars if _db else None, args.min_bars, cfg.min_bars, 3),
            confirm_bars=_resolve(cfg.confirm_bars if _db else None, args.confirm_bars, cfg.confirm_bars, 2),
            vwap_prox=_resolve(cfg.vwap_prox if _db else None, args.vwap_prox, cfg.vwap_prox, 0.005),
            vwap_window_days=args.vwap_window_days,
            pos_size_pct=_resolve(cfg.pos_size_pct if _db else None, args.pos_size, cfg.pos_size_pct, 0.20),
            ema_period=_resolve(extras.get("ema_period") if _db else None, args.ema_period, None, 200),
            max_trades_per_day=_resolve(extras.get("max_trades_per_day") if _db else None, args.max_trades, None, 4),
            be_profit_usd=_resolve(extras.get("be_profit_usd") if _db else None, None, None, 0.50),
            interval=cfg.interval,
            vwap_dist_stop=cfg.vwap_dist_stop,
            price_decimals=cfg.price_decimals if _db else None,
            qty_decimals=cfg.qty_decimals if _db else None,
        )
        _run_async(bot.run())

    elif args.command == "pullback-v2":
        cfg, extras = _load_symbol_config(args.symbol.upper(), "VWAPPullback")
        _db = extras.get("_source") == "db"
        from trader.bot_vwap_pullback_v2 import VWAPPullbackBotV2
        bot = VWAPPullbackBotV2(
            symbol=args.symbol,
            leverage=_resolve(extras["leverage"] if _db else None, args.leverage, cfg.leverage, DEFAULT_LEVERAGE),
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=_resolve(cfg.sl_pct if _db else None, args.sl, cfg.sl_pct, 2.5),
            min_bars=_resolve(cfg.min_bars if _db else None, args.min_bars, cfg.min_bars, 3),
            confirm_bars=_resolve(cfg.confirm_bars if _db else None, args.confirm_bars, cfg.confirm_bars, 2),
            vwap_prox=_resolve(cfg.vwap_prox if _db else None, args.vwap_prox, cfg.vwap_prox, 0.005),
            vwap_window_days=args.vwap_window_days,
            pos_size_pct=_resolve(cfg.pos_size_pct if _db else None, args.pos_size, cfg.pos_size_pct, 0.20),
            ema_period=_resolve(extras.get("ema_period") if _db else None, args.ema_period, None, 200),
            max_trades_per_day=_resolve(extras.get("max_trades_per_day") if _db else None, args.max_trades, None, 4),
            interval=cfg.interval,
        )
        _run_async(bot.run())

    elif args.command == "ema-scalp":
        cfg, extras = _load_symbol_config(args.symbol.upper(), "EMAScalp")
        _db = extras.get("_source") == "db"
        from trader.bot_ema_scalp import EMAScalpBot
        bot = EMAScalpBot(
            symbol=args.symbol,
            leverage=_resolve(extras["leverage"] if _db else None, args.leverage, cfg.leverage, DEFAULT_LEVERAGE),
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=_resolve(cfg.sl_pct if _db else None, args.sl, cfg.sl_pct, 0.3),
            fast_period=_resolve(extras.get("fast_period") if _db else None, args.fast_period, None, 8),
            slow_period=_resolve(extras.get("slow_period") if _db else None, args.slow_period, None, 21),
            vol_filter=cfg.vol_filter if _db else (args.vol_filter or cfg.vol_filter),
            max_trades_per_day=_resolve(extras.get("max_trades_per_day") if _db else None, args.max_trades, None, 4),
            pos_size_pct=_resolve(cfg.pos_size_pct if _db else None, args.pos_size, cfg.pos_size_pct, 0.20),
            be_r=_resolve(extras.get("be_r") if _db else None, args.be_r, None, 2.0),
            trail_step=_resolve(extras.get("trail_step") if _db else None, args.trail_step, None, 0.5),
            be_profit_usd=_resolve(extras.get("be_profit_usd") if _db else None, None, None, 0.50),
            interval=cfg.interval,
            price_decimals=cfg.price_decimals if _db else None,
            qty_decimals=cfg.qty_decimals if _db else None,
        )
        _run_async(bot.run())

    elif args.command == "orb":
        cfg, extras = _load_symbol_config(args.symbol.upper(), "ORB")
        _db = extras.get("_source") == "db"
        from trader.bot_orb import ORBBot
        bot = ORBBot(
            symbol=args.symbol,
            leverage=_resolve(extras["leverage"] if _db else None, args.leverage, cfg.leverage, DEFAULT_LEVERAGE),
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=_resolve(cfg.sl_pct if _db else None, args.sl, cfg.sl_pct, 0.5),
            range_mins=_resolve(extras.get("range_mins") if _db else None, args.range_mins, None, 30),
            buffer_pct=args.buffer_pct,
            vol_filter=cfg.vol_filter if _db else (args.vol_filter or cfg.vol_filter),
            max_trades_per_day=_resolve(extras.get("max_trades_per_day") if _db else None, args.max_trades, None, 4),
            pos_size_pct=_resolve(cfg.pos_size_pct if _db else None, args.pos_size, cfg.pos_size_pct, 0.20),
            be_r=_resolve(extras.get("be_r") if _db else None, args.be_r, None, 2.0),
            trail_step=_resolve(extras.get("trail_step") if _db else None, args.trail_step, None, 0.5),
            be_profit_usd=_resolve(extras.get("be_profit_usd") if _db else None, None, None, 0.50),
            interval=cfg.interval,
            price_decimals=cfg.price_decimals if _db else None,
            qty_decimals=cfg.qty_decimals if _db else None,
        )
        _run_async(bot.run())

    elif args.command == "pdhl":
        cfg, extras = _load_symbol_config(args.symbol.upper(), "PDHL")
        _db = extras.get("_source") == "db"
        from trader.bot_pdhl import PDHLBot
        bot = PDHLBot(
            symbol=args.symbol,
            leverage=_resolve(extras["leverage"] if _db else None, args.leverage, cfg.leverage, DEFAULT_LEVERAGE),
            capital=args.capital,
            dry_run=args.dry_run,
            sl_pct=_resolve(cfg.sl_pct if _db else None, args.sl, cfg.sl_pct, 0.3),
            prox_pct=_resolve(extras.get("pdhl_prox_pct") if _db else None, args.prox_pct, None, 0.002),
            confirm_bars=_resolve(cfg.confirm_bars if _db else None, args.confirm_bars, cfg.confirm_bars, 1),
            max_trades_per_day=_resolve(extras.get("max_trades_per_day") if _db else None, args.max_trades, None, 4),
            pos_size_pct=_resolve(cfg.pos_size_pct if _db else None, args.pos_size, cfg.pos_size_pct, 0.20),
            be_r=_resolve(extras.get("be_r") if _db else None, args.be_r, None, 2.0),
            trail_step=_resolve(extras.get("trail_step") if _db else None, args.trail_step, None, 0.5),
            be_profit_usd=_resolve(extras.get("be_profit_usd") if _db else None, None, None, 0.50),
            tp_pct=_resolve(cfg.tp_pct if _db else None, args.tp, cfg.tp_pct, None),
            interval=cfg.interval,
            price_decimals=cfg.price_decimals if _db else None,
            qty_decimals=cfg.qty_decimals if _db else None,
        )
        _run_async(bot.run())

    elif args.command == "plot":
        if args.days < 1 or args.days > 180:
            parser.error("--days must be between 1 and 180")
        from trader.plot import plot_pnl
        plot_pnl(days=args.days)

    elif args.command == "bot":
        cfg, extras = _load_symbol_config(args.symbol.upper(), "MomShort")
        _db = extras.get("_source") == "db"
        from trader.bot import MomShortBot
        bot = MomShortBot(
            cfg=cfg,
            leverage=_resolve(extras["leverage"] if _db else None, args.leverage, cfg.leverage, DEFAULT_LEVERAGE),
            capital=args.capital,
            dry_run=args.dry_run,
            be_profit_usd=_resolve(extras.get("be_profit_usd") if _db else None, None, None, 0.50),
        )
        _run_async(bot.run())

    else:
        parser.print_help()


async def _load_cfg_async(symbol: str, strategy_name: str):
    """Async DB config load for use inside async context (e.g. _serve)."""
    try:
        import db
        from db.queries.symbol_config import get_symbol_config as _db_cfg
        pool = await db.init_pool()
        cfg, extras = await _db_cfg(pool, symbol.upper())
        await db.close_pool()
        extras["_source"] = "db"
        return cfg, extras
    except Exception as e:
        if not ALLOW_CONFIG_FALLBACK:
            raise RuntimeError(
                f"DB unavailable for {symbol.upper()} ({strategy_name}); "
                "set ALLOW_CONFIG_FALLBACK=1 to allow config.py fallback"
            ) from e
        from trader.config import get_symbol_config
        cfg = get_symbol_config(symbol)
        print(
            f"⚠️ DB unavailable for {symbol.upper()} ({strategy_name}) — using trader/config.py "
            f"(ALLOW_CONFIG_FALLBACK=1). Reason: {e}"
        )
        return cfg, {"leverage": cfg.leverage, "be_profit_usd": 0.50, "_source": "fallback"}


async def _serve(args):
    """Start FastAPI dashboard, optionally co-located with bots."""
    import uvicorn
    from trader.api import app

    tasks = []

    # Start VWAPPullback bots
    for sym in args.pullback_symbols:
        from trader.bot_vwap_pullback import VWAPPullbackBot
        cfg, extras = await _load_cfg_async(sym, "VWAPPullback")
        _db = extras.get("_source") == "db"
        bot = VWAPPullbackBot(
            symbol=sym,
            leverage=extras["leverage"],
            dry_run=args.dry_run,
            tp_pct=cfg.tp_pct,
            sl_pct=cfg.sl_pct,
            min_bars=cfg.min_bars,
            confirm_bars=cfg.confirm_bars,
            vwap_prox=cfg.vwap_prox,
            pos_size_pct=cfg.pos_size_pct,
            vol_filter=cfg.vol_filter,
            be_profit_usd=extras.get("be_profit_usd", 0.50),
            interval=cfg.interval,
            vwap_dist_stop=cfg.vwap_dist_stop,
            price_decimals=cfg.price_decimals if _db else None,
            qty_decimals=cfg.qty_decimals if _db else None,
        )
        tasks.append(asyncio.create_task(bot.run()))

    # Start MomShort bots
    for sym in args.momshort_symbols:
        from trader.bot import MomShortBot
        cfg, extras = await _load_cfg_async(sym, "MomShort")
        bot = MomShortBot(
            cfg=cfg,
            leverage=extras["leverage"],
            dry_run=args.dry_run,
            be_profit_usd=extras.get("be_profit_usd", 0.50),
        )
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
