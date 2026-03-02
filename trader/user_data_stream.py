"""Binance USDT-M Futures user data stream.

Opens a single authenticated WebSocket connection per bot process that delivers:
  - AccountUpdate  — position changes, unrealized P&L updates in real-time
  - OrderTradeUpdate — order fills / cancellations

Keeps the listen key alive every 30 minutes and auto-reconnects on disconnect.

Usage (inside a bot's run() coroutine):
    from trader.user_data_stream import UserDataStream

    uds = UserDataStream(self._client, self._ws_factory, self._ws_url, self._ConfigWS)
    uds.register(self._on_user_data)           # async callback(event) -> None
    self._uds_task = asyncio.create_task(uds.run())
    ...
    # in finally block:
    if self._uds_task and not self._uds_task.done():
        self._uds_task.cancel()
"""

import asyncio
import logging
from typing import Callable

logger = logging.getLogger("trader.user_data_stream")


class UserDataStream:
    def __init__(self, client, ws_factory, ws_url: str, ConfigWS):
        self._client   = client
        self._ws_factory = ws_factory
        self._ws_url   = ws_url
        self._ConfigWS = ConfigWS
        self._callbacks: list[Callable] = []
        self._listen_key: str | None = None

    def register(self, callback: Callable) -> None:
        """Register an async callable that will be called with each event."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Listen-key management (sync — SDK REST calls are blocking)
    # ------------------------------------------------------------------

    def _create_listen_key(self) -> str:
        resp = self._client.rest_api.start_user_data_stream()
        return resp.data().listen_key

    def _keepalive(self) -> None:
        try:
            self._client.rest_api.keepalive_user_data_stream()
        except Exception as e:
            logger.warning(f"User data stream keepalive failed: {e}")

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, event) -> None:
        """Called synchronously by the SDK for each incoming WS message."""
        for cb in self._callbacks:
            try:
                asyncio.get_event_loop().create_task(cb(event))
            except Exception as e:
                logger.error(f"User data stream callback error: {e}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect, stream events, reconnect on error. Runs until cancelled."""
        from trader.config import SOCKS_PROXY

        try:
            self._listen_key = await asyncio.to_thread(self._create_listen_key)
        except Exception as e:
            logger.error(f"User data stream: failed to create listen key: {e}")
            return

        logger.info("User data stream: listen key acquired")

        while True:
            connection = None
            stream = None
            keepalive_task: asyncio.Task | None = None
            try:
                ws_config = self._ConfigWS(stream_url=self._ws_url)
                ws_client = self._ws_factory(config_ws_streams=ws_config)

                if SOCKS_PROXY:
                    from aiohttp_socks import ProxyConnector
                    import aiohttp
                    connector = ProxyConnector.from_url(SOCKS_PROXY)
                    ws_client.websocket_streams.session = aiohttp.ClientSession(
                        connector=connector
                    )

                connection = await ws_client.websocket_streams.create_connection()
                stream = await connection.user_data(self._listen_key)
                stream.on("message", self._dispatch)
                logger.info("User data stream: connected and listening")

                keepalive_task = asyncio.create_task(self._keepalive_loop())

                while True:
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info("User data stream: shutting down")
                return
            except Exception as e:
                logger.warning(f"User data stream error: {e} — reconnecting in 5s")
                await asyncio.sleep(5)
                try:
                    self._listen_key = await asyncio.to_thread(self._create_listen_key)
                except Exception as ke:
                    logger.error(f"User data stream: failed to refresh listen key: {ke}")
            finally:
                if keepalive_task and not keepalive_task.done():
                    keepalive_task.cancel()
                if stream:
                    try:
                        await stream.unsubscribe()
                    except Exception:
                        pass
                if connection:
                    try:
                        await connection.close_connection(close_session=True)
                    except Exception:
                        pass

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1800)  # 30 minutes
                await asyncio.to_thread(self._keepalive)
                logger.debug("User data stream: listen key kept alive")
        except asyncio.CancelledError:
            pass
