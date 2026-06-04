import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

@dataclass
class LaunchData:
    name: str
    symbol: str
    first_seen: float
    buy_count: int = 0
    sell_count: int = 0
    unique_wallets: set = field(default_factory=set)
    volume: float = 0.0
    holders: int = 0
    lp_locked: float = 0.0

@dataclass
class TrackedCoin:
    initial_price: float
    name: str
    symbol: str
    first_seen: float
    holders: int = 0
    lp_locked: float = 0.0

@dataclass
class SignalInfo:
    symbol: str
    price_at_signal: float
    signal_time: float
    checked: bool = False

@dataclass
class CoinInfo:
    name: str
    symbol: str
    first_seen: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

class BotState:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.launch_tracking: dict[str, LaunchData] = {}
        self.tracked_coins: dict[str, TrackedCoin] = {}
        self.alerted_coins: set[str] = set()
        self.blacklisted: set[str] = set()
        self.signal_tracking: dict[str, SignalInfo] = {}
        self.pump_coins: dict[str, CoinInfo] = {}
        self.dump_coins: dict[str, CoinInfo] = {}
        self.bot_active: bool = True
        self.current_threshold: float = 0.50
    
    async def add_launch_tracking(self, address: str, data: LaunchData) -> None:
        async with self._lock:
            self.launch_tracking[address] = data
    
    async def get_launch_tracking(self, address: str) -> Optional[LaunchData]:
        async with self._lock:
            return self.launch_tracking.get(address)
    
    async def remove_launch_tracking(self, address: str) -> None:
        async with self._lock:
            self.launch_tracking.pop(address, None)
    
    async def add_tracked_coin(self, address: str, coin: TrackedCoin) -> None:
        async with self._lock:
            self.tracked_coins[address] = coin
    
    async def get_tracked_coin(self, address: str) -> Optional[TrackedCoin]:
        async with self._lock:
            return self.tracked_coins.get(address)
    
    async def remove_tracked_coin(self, address: str) -> None:
        async with self._lock:
            self.tracked_coins.pop(address, None)
    
    async def add_alerted(self, address: str) -> None:
        async with self._lock:
            self.alerted_coins.add(address)
    
    async def is_alerted(self, address: str) -> bool:
        async with self._lock:
            return address in self.alerted_coins
    
    async def add_blacklisted(self, address: str) -> None:
        async with self._lock:
            self.blacklisted.add(address)
    
    async def is_blacklisted(self, address: str) -> bool:
        async with self._lock:
            return address in self.blacklisted
    
    async def add_signal(self, address: str, signal: SignalInfo) -> None:
        async with self._lock:
            self.signal_tracking[address] = signal
    
    async def get_signal(self, address: str) -> Optional[SignalInfo]:
        async with self._lock:
            return self.signal_tracking.get(address)
    
    async def mark_signal_checked(self, address: str) -> None:
        async with self._lock:
            if address in self.signal_tracking:
                self.signal_tracking[address].checked = True
    
    async def get_unchecked_signals(self) -> dict[str, SignalInfo]:
        async with self._lock:
            return {k: v for k, v in self.signal_tracking.items() if not v.checked}
    
    async def add_pump_coin(self, address: str, coin: CoinInfo) -> None:
        async with self._lock:
            self.pump_coins[address] = coin
    
    async def add_dump_coin(self, address: str, coin: CoinInfo) -> None:
        async with self._lock:
            self.dump_coins[address] = coin
    
    async def cleanup_old_entries(self, max_age_seconds: int = 86400) -> None:
        now = datetime.now(timezone.utc).timestamp()
        async with self._lock:
            for addr, data in list(self.launch_tracking.items()):
                if now - data.first_seen > max_age_seconds:
                    del self.launch_tracking[addr]
            for addr, coin in list(self.tracked_coins.items()):
                if now - coin.first_seen > max_age_seconds:
                    del self.tracked_coins[addr]
            for addr, sig in list(self.signal_tracking.items()):
                if now - sig.signal_time > max_age_seconds:
                    del self.signal_tracking[addr]
    
    async def get_stats(self) -> dict:
        async with self._lock:
            return {
                "launch_tracking": len(self.launch_tracking),
                "tracked_coins": len(self.tracked_coins),
                "alerted_coins": len(self.alerted_coins),
                "blacklisted": len(self.blacklisted),
                "signal_tracking": len(self.signal_tracking),
                "pump_coins": len(self.pump_coins),
                "dump_coins": len(self.dump_coins),
                "bot_active": self.bot_active,
                "current_threshold": self.current_threshold,
            }
    
    async def set_bot_active(self, active: bool) -> None:
        async with self._lock:
            self.bot_active = active
    
    async def set_threshold(self, threshold: float) -> None:
        async with self._lock:
            self.current_threshold = threshold
    
    async def get_threshold(self) -> float:
        async with self._lock:
            return self.current_threshold