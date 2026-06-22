import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

@dataclass
class LaunchData:
    name: str
    symbol: str
    first_seen: float
    launch_time: float
    buy_count: int = 0
    sell_count: int = 0
    unique_wallets: set = field(default_factory=set)
    volume: float = 0.0
    holders: int = 0
    lp_locked: float = 0.0
    deployer_wallet: str = ""
    tx_history: list = field(default_factory=list)
    pre_signal_sent: bool = False
    migration_time: float = 0.0
    migration_price: float = 0.0
    initial_price: float = 0.0
    buy_timestamps: list = field(default_factory=list)
    buy_velocity: float = 0.0
    curve_fill_pct: float = 0.0
    eval_done: dict = field(default_factory=dict)
    ath_price: float = 0.0
    trailing_sl_triggered: bool = False

@dataclass
class TrackedCoin:
    initial_price: float
    name: str
    symbol: str
    first_seen: float
    launch_time: float
    holders: int = 0
    lp_locked: float = 0.0
    deployer_wallet: str = ""
    initial_holders: int = 0
    ath_price: float = 0.0
    migration_time: float = 0.0

@dataclass
class SignalInfo:
    symbol: str
    price_at_signal: float
    signal_time: float
    checked: bool = False
    launch_time: float = 0.0
    is_pre_migration: bool = False
    migration_time: float = 0.0
    is_pre_migration_known: bool = False
    eval_done: dict = field(default_factory=dict)
    ath_price: float = 0.0
    min_price: float = 0.0
    signal_age: float = 0.0  # age in seconds at signal time
    source: str = ""

@dataclass
class PendingSignal:
    """Signal candidate awaiting confirmation before sending."""
    symbol: str
    address: str
    name: str
    match_score: float
    match_reason: str
    price_at_match: float
    mcap: float
    liquidity: float
    holders: int
    unique_wallets: int
    buy_count: int
    sell_count: int
    buy_sell_ratio: float
    lp_locked: float
    age_seconds: float
    pending_since: float
    last_check_price: float = 0.0
    check_count: int = 0
    price_stable: bool = False
    min_price: float = 0.0
    stage2_done: bool = False
    source: str = ""

@dataclass
class CoinInfo:
    name: str
    symbol: str
    first_seen: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

@dataclass
class LPSnapshot:
    address: str
    symbol: str
    timestamp: float
    liquidity_usd: float
    lp_providers: int
    deployer_has_lp: bool = False
    price_at_snapshot: float = 0.0

class BotState:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.launch_tracking: dict[str, LaunchData] = {}
        self.tracked_coins: dict[str, TrackedCoin] = {}
        self.alerted_coins: set[str] = set()
        self.blacklisted: set[str] = set()
        self.blocked_deployers: set[str] = set()
        self.honeypot_addresses: set[str] = set()
        self.signal_tracking: dict[str, SignalInfo] = {}
        self.pump_coins: dict[str, CoinInfo] = {}
        self.dump_coins: dict[str, CoinInfo] = {}
        self.pending_signals: dict[str, PendingSignal] = {}
        self.lp_snapshots: dict[str, LPSnapshot] = {}
        self.bot_active: bool = True
        self.current_threshold: float = 0.50
    
    async def update_launch_tx(self, address: str, tx_type: str, wallet: str = "", amount: float = 0.0) -> None:
        async with self._lock:
            data = self.launch_tracking.get(address)
            if not data:
                return
            data.tx_history.append({"type": tx_type, "wallet": wallet, "amount": amount, "time": datetime.now(timezone.utc).timestamp()})
            if len(data.tx_history) > 50:
                data.tx_history = data.tx_history[-50:]
            if tx_type == "buy":
                data.buy_count += 1
                if wallet:
                    data.unique_wallets.add(wallet)
                now = datetime.now(timezone.utc).timestamp()
                data.buy_timestamps.append(now)
                if len(data.buy_timestamps) > 50:
                    data.buy_timestamps = data.buy_timestamps[-50:]
                if len(data.buy_timestamps) >= 2:
                    age = now - data.launch_time
                    if age > 0:
                        data.buy_velocity = len(data.buy_timestamps) / max(age / 60.0, 1.0)
                if wallet and wallet not in data.unique_wallets:
                    if data.holders == 0:
                        data.holders = len(data.unique_wallets) + 1
                data.volume += amount
            elif tx_type == "sell":
                data.sell_count += 1

    async def get_deployer_tokens(self, deployer: str) -> list:
        if not deployer:
            return []
        async with self._lock:
            return [addr for addr, data in self.launch_tracking.items() if data.deployer_wallet == deployer]

    async def add_launch_tracking(self, address: str, data: LaunchData) -> None:
        async with self._lock:
            self.launch_tracking[address] = data
    
    async def get_launch_tracking(self, address: str) -> Optional[LaunchData]:
        async with self._lock:
            return self.launch_tracking.get(address)

    async def save_migration_launch_pattern(self, address: str, launch_pattern: dict) -> None:
        """Save launch pattern at migration time for post-migration learning."""
        async with self._lock:
            if not hasattr(self, '_migration_launch_patterns'):
                self._migration_launch_patterns = {}
            self._migration_launch_patterns[address] = launch_pattern

    async def get_migration_launch_pattern(self, address: str) -> Optional[dict]:
        """Get saved launch pattern for post-migration learning."""
        async with self._lock:
            return getattr(self, '_migration_launch_patterns', {}).get(address)

    async def get_all_tracked(self) -> dict:
        async with self._lock:
            return dict(self.launch_tracking)

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

    async def add_blocked_deployer(self, deployer: str) -> None:
        if not deployer:
            return
        async with self._lock:
            self.blocked_deployers.add(deployer)

    async def is_deployer_blocked(self, deployer: str) -> bool:
        if not deployer:
            return False
        async with self._lock:
            return deployer in self.blocked_deployers

    async def mark_honeypot(self, address: str) -> None:
        async with self._lock:
            self.honeypot_addresses.add(address)
            self.blacklisted.add(address)

    async def is_honeypot(self, address: str) -> bool:
        async with self._lock:
            return address in self.honeypot_addresses
    
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
            self.alerted_coins.add(address)
    
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
                "blocked_deployers": len(self.blocked_deployers),
                "honeypot_addresses": len(self.honeypot_addresses),
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

    async def save_lp_snapshot(self, address: str, symbol: str, liquidity_usd: float,
                                lp_providers: int, deployer_has_lp: bool = False,
                                price: float = 0.0) -> None:
        async with self._lock:
            self.lp_snapshots[address] = LPSnapshot(
                address=address,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc).timestamp(),
                liquidity_usd=liquidity_usd,
                lp_providers=lp_providers,
                deployer_has_lp=deployer_has_lp,
                price_at_snapshot=price,
            )

    async def get_lp_snapshot(self, address: str) -> Optional[LPSnapshot]:
        async with self._lock:
            return self.lp_snapshots.get(address)

    async def get_all_lp_snapshots(self) -> dict[str, LPSnapshot]:
        async with self._lock:
            return dict(self.lp_snapshots)

    async def remove_lp_snapshot(self, address: str) -> None:
        async with self._lock:
            self.lp_snapshots.pop(address, None)