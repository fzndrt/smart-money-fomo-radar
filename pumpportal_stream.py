"""
pumpportal_stream.py - Sub-Second Early Detection Engine (Solana / Pump.fun)
Dilengkapi pemrosesan non-blocking tanpa blind time (Zero Blind Time Streamer).
"""
import json
import asyncio
import logging
from typing import Callable

logger = logging.getLogger("pumpportal_stream")
PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"

class PumpPortalStreamer:
    def __init__(self, on_token_trade_callback: Callable):
        self.callback = on_token_trade_callback
        self.is_running = False

    async def start(self):
        try:
            import websockets
        except ImportError:
            logger.warning("Modul 'websockets' belum terpasang. Jalankan: pip install websockets")
            return

        self.is_running = True
        logger.info("[PumpAlpha] Menghubungkan ke WebSocket PumpPortal...")

        while self.is_running:
            try:
                async with websockets.connect(
                    PUMPPORTAL_WS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=10 * 1024 * 1024
                ) as ws:
                    logger.info("[PumpAlpha] ✅ TERHUBUNG KE REAL-TIME STREAM SOLANA / PUMP.FUN!")

                    # 1. Berlangganan feed token baru diluncurkan
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    # 2. Berlangganan transaksi live di bonding curve
                    await ws.send(json.dumps({"method": "subscribeTokenTrade"}))

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            if self.callback:
                                # ZERO BLIND TIME: Jalankan callback sebagai background task
                                # agar pembaca WebSocket tidak pernah tertahan oleh audit RPC token
                                if asyncio.iscoroutinefunction(self.callback):
                                    asyncio.create_task(self.callback(data))
                                else:
                                    self.callback(data)
                        except Exception as parse_err:
                            logger.debug(f"[PumpAlpha] Parse Error: {parse_err}")

            except Exception as e:
                logger.warning(f"[PumpAlpha] Koneksi WebSocket terputus: {e}. Reconnect dalam 3 detik...")
                await asyncio.sleep(3)

    def stop(self):
        self.is_running = False
