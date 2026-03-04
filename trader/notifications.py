"""Telegram notification helpers for trading events.

All public functions are fire-and-forget: they schedule an async task and return
immediately — never blocking the trading loop.

Usage in bots:
    from trader.notifications import notify_position_opened, notify_position_closed, ...
"""

import asyncio
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone

_log = logging.getLogger(__name__)

# Lazy-initialized from env on first call
_TOKEN: str = ""
_CHAT_ID: str = ""
_ENABLED: bool | None = None  # None = not yet initialised


def _init() -> None:
    global _TOKEN, _CHAT_ID, _ENABLED
    if _ENABLED is not None:
        return
    _TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    _CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    _ENABLED = bool(_TOKEN and _CHAT_ID)


async def _send(text: str) -> None:
    _init()
    if not _ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=5)
        )
    except Exception as exc:
        _log.warning("Telegram send error: %s", exc)


def _fire(text: str) -> None:
    """Schedule a Telegram message as a fire-and-forget asyncio task."""
    try:
        asyncio.get_running_loop().create_task(_send(text))
    except RuntimeError:
        _log.warning("_fire called outside async context — notification dropped")


# ---------------------------------------------------------------------------
# Public notification helpers
# ---------------------------------------------------------------------------

def notify_bot_started(
    symbol: str,
    strategy: str,
    interval: str,
    leverage: int,
    pos_size_pct: float,
) -> None:
    """Bot process just started and is ready to trade."""
    _fire(
        f"🤖 <b>Bot iniciado</b> — {symbol}\n"
        f"Strategy: <b>{strategy}</b> | TF: {interval}\n"
        f"Leverage: {leverage}x | Pos size: {pos_size_pct * 100:.0f}%"
    )


def notify_signal(
    symbol: str,
    direction: str,
    price: float,
    strategy: str = "",
) -> None:
    """A trade signal was detected — entry order is about to be placed."""
    emoji = "📈" if direction == "long" else "📉"
    dir_label = "LONG" if direction == "long" else "SHORT"
    extra = f" | {strategy}" if strategy else ""
    _fire(
        f"{emoji} <b>SINAL {dir_label}</b> — {symbol}\n"
        f"Preço: ${price}{extra}"
    )


def notify_position_opened(
    symbol: str,
    direction: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    qty: float,
    leverage: int,
) -> None:
    """A position was successfully opened."""
    emoji = "🟢" if direction == "long" else "🔴"
    dir_label = "LONG" if direction == "long" else "SHORT"
    if entry_price > 0:
        sl_pct = abs(sl_price - entry_price) / entry_price * 100
        tp_pct = abs(tp_price - entry_price) / entry_price * 100
        sl_str = f"${sl_price} (-{sl_pct:.1f}%)"
        tp_str = f"${tp_price} (+{tp_pct:.1f}%)"
    else:
        sl_str = f"${sl_price}"
        tp_str = f"${tp_price}"
    _fire(
        f"{emoji} <b>{dir_label} ABERTO</b> — {symbol}\n"
        f"Entry: <b>${entry_price}</b> | Qty: {qty}\n"
        f"SL: {sl_str}\n"
        f"TP: {tp_str}\n"
        f"Leverage: {leverage}x"
    )


def notify_position_closed(
    symbol: str,
    direction: str,
    entry_price: float,
    reason: str = "SL/TP",
) -> None:
    """A position was closed (SL/TP hit or manual). P&L available on dashboard."""
    dir_label = "LONG" if direction == "long" else "SHORT" if direction == "short" else direction.upper()
    _fire(
        f"⚪ <b>Posição fechada</b> — {symbol}\n"
        f"{dir_label} | Entry: ${entry_price}\n"
        f"Motivo: {reason}"
    )


def notify_eod_close(
    symbol: str,
    direction: str,
    entry_price: float,
    close_price: float,
    pnl: float,
) -> None:
    """Position was force-closed at end-of-day."""
    emoji = "🟢" if pnl >= 0 else "🔴"
    dir_label = "LONG" if direction == "long" else "SHORT"
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    _fire(
        f"{emoji} <b>EOD Close</b> — {symbol}\n"
        f"{dir_label} | Entry: ${entry_price} → ${close_price}\n"
        f"P&amp;L: <b>${pnl:+.2f}</b> | {ts}"
    )


def notify_error(
    symbol: str,
    error: str,
    context: str = "",
) -> None:
    """A non-fatal error occurred (e.g. failed entry, API error)."""
    ctx = f"{context}\n" if context else ""
    _fire(
        f"⚠️ <b>Erro</b> — {symbol}\n"
        f"{ctx}<code>{str(error)[:250]}</code>"
    )


def notify_startup_error_sync(symbol: str, error: str) -> None:
    """Send startup error immediately (safe to call before asyncio loop exists)."""
    _init()
    if not _ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": _CHAT_ID,
            "text": (
                f"⚠️ <b>Erro ao inciar bot</b> — {symbol}\n"
                f"<code>{str(error)[:250]}</code>"
            ),
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        _log.warning("Telegram startup error send failed: %s", exc)


def notify_bot_stopped(symbol: str, strategy: str) -> None:
    """Bot process is shutting down."""
    _fire(f"🛑 <b>Bot finalizado</b> — {symbol}\nStrategy: {strategy}")


def notify_cooldown(symbol: str, reason: str = "limite diário atingido") -> None:
    """Bot entered COOLDOWN state for today."""
    _fire(f"⏸ <b>Cooldown</b> — {symbol}\n{reason}")
