import asyncio
import json
import logging
import websockets
from typing import Callable, Optional
from config import config

logger = logging.getLogger("pumpportal_ws")

class PumpPortalWS:
    def __init__(
        self,
        on_new_token: Callable,
        on_migration: Callable,
        on_trade: Callable
    ):
        self.ws_url = config.pumpportal_ws
        self.on_new_token = on_new_token
        self.on_migration = on_migration
        self.on_trade = on_trade
        self._running = False
        self._ws = None
    
    async def connect(self) -> None:
        self._running = True
        while self._running:
            try:
                logger.info("Connecting to PumpPortal...")
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    await ws.send(json.dumps({"method": "subscribeTokenTrade"}))
                    logger.info("PumpPortal connected!")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            if "mint" in data and "name" in data:
                                asyncio.create_task(self.on_new_token(data))
                            elif data.get("txType") == "migrate":
                                asyncio.create_task(self.on_migration(data))
                            elif "mint" in data and "txType" in data:
                                asyncio.create_task(self.on_trade(data))
                        except json.JSONDecodeError:
                            pass
                        except Exception as e:
                            logger.error(f"WS message error: {e}")
            except Exception as e:
                logger.error(f"PumpPortal connection error: {e}")
            finally:
                self._ws = None
            
            if self._running:
                logger.info("Reconnecting in 10 seconds...")
                await asyncio.sleep(10)
    
    async def close(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()