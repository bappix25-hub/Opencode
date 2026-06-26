import asyncio
import aiohttp
import logging
import time
import subprocess
from typing import Optional

logger = logging.getLogger("health")


class HealthChecker:
    def __init__(self):
        self.last_api_check = 0.0
        self.last_internet_check = 0.0
        self.api_failures = 0
        self.max_api_failures = 20
        self.consecutive_internet_fails = 0
        self.max_internet_fails = 10
        self.is_healthy = True
        self.last_reconnect_attempt = 0.0
        self.reconnect_cooldown = 600
        self.last_failed_apis = []

    async def check_internet(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://frontend-api-v3.pump.fun/coins?limit=1",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    ok = resp.status == 200
                    self.last_internet_check = time.time()
                    if ok:
                        self.consecutive_internet_fails = 0
                    return ok
        except Exception:
            self.consecutive_internet_fails += 1
            logger.warning(f"Internet check failed ({self.consecutive_internet_fails}/{self.max_internet_fails})")
            return False
        return False

    async def check_api(self, session: aiohttp.ClientSession) -> dict:
        results = {}
        
        check_session = None
        try:
            check_session = aiohttp.ClientSession(
                headers={"Accept": "application/json"},
                connector=aiohttp.TCPConnector(limit=2, force_close=True)
            )
            
            for api_name, url in [
                ("pumpfun", "https://frontend-api-v3.pump.fun/coins?limit=1"),
                ("gecko", "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1"),
            ]:
                for attempt in range(2):
                    try:
                        async with check_session.get(
                            url,
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            if resp.status == 200:
                                results[api_name] = True
                                break
                            elif resp.status == 429:
                                logger.debug(f"{api_name} rate limited (attempt {attempt+1})")
                                await asyncio.sleep(2)
                            else:
                                results[api_name] = False
                                break
                    except asyncio.TimeoutError:
                        logger.debug(f"{api_name} timeout (attempt {attempt+1})")
                    except Exception as e:
                        logger.debug(f"{api_name} check error: {e}")
                        break
                else:
                    results[api_name] = False
        finally:
            if check_session:
                await check_session.close()

        self.last_api_check = time.time()
        
        failed = [k for k, v in results.items() if not v]
        self.last_failed_apis = failed
        if failed:
            self.api_failures += 1
            logger.warning(f"API check failed: {failed} (total: {self.api_failures}/{self.max_api_failures})")
        else:
            if self.api_failures > 0:
                logger.info(f"API check passed, resetting failure count (was {self.api_failures})")
            self.api_failures = 0

        return results

    def should_reconnect(self) -> bool:
        if self.api_failures >= self.max_api_failures:
            return True
        if self.consecutive_internet_fails >= self.max_internet_fails:
            return True
        if self.api_failures >= 10 and (time.time() - self.last_reconnect_attempt) > self.reconnect_cooldown:
            return True
        return False

    async def attempt_reconnect(self) -> bool:
        self.last_reconnect_attempt = time.time()
        logger.info("Attempting reconnect...")
        
        internet_ok = await self.check_internet()
        if internet_ok:
            self.api_failures = 0
            self.consecutive_internet_fails = 0
            self.is_healthy = True
            logger.info("Reconnect successful")
            return True
        
        logger.warning("Reconnect failed, internet still down")
        return False

    def get_status(self) -> str:
        if not self.is_healthy:
            return "🔴 Unhealthy"
        if self.api_failures > 0:
            return f"🟡 Degraded ({self.api_failures} failures)"
        return "🟢 Healthy"

    def format_status(self) -> str:
        status = self.get_status()
        failed_str = ", ".join(self.last_failed_apis) if self.last_failed_apis else "None"
        return (
            f"❤️ <b>Health Status</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n"
            f"API Failures: {self.api_failures}/{self.max_api_failures}\n"
            f"Internet Fails: {self.consecutive_internet_fails}/{self.max_internet_fails}\n"
            f"Last Failed: {failed_str}\n"
            f"Last API Check: {self._time_ago(self.last_api_check)}\n"
            f"Last Internet Check: {self._time_ago(self.last_internet_check)}\n"
            f"━━━━━━━━━━━━━━━━"
        )

    def _time_ago(self, timestamp: float) -> str:
        if timestamp == 0:
            return "Never"
        diff = time.time() - timestamp
        if diff < 60:
            return f"{diff:.0f}s ago"
        elif diff < 3600:
            return f"{diff / 60:.1f}m ago"
        else:
            return f"{diff / 3600:.1f}h ago"


def check_internet_sync() -> bool:
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "3", "8.8.8.8"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False
