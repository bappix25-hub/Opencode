import json
import os
import logging
import time
import asyncio
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict

logger = logging.getLogger("wallet_tracker")

WALLET_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "wallet_tracking.json")
KNOWN_WALLETS_FILE = os.path.join(os.path.dirname(__file__), "data", "known_wallets.json")

# Known wallet categories
DEV_WALLET_PREFIXES = [
    'So11111111111111111111111111111111111111112',  # SOL
    'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',  # Token
    'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25ZHCpPENi',   # Associated Token
]

# Suspicious patterns to monitor
SUSPICIOUS_PATTERNS = [
    'rapid_balance_change',     # Large balance changes quickly
    'multiple_transfers',        # Multiple outgoing transfers
    'new_token_creation',       # New token deploys
    'pump_dump_pattern',        # Price pump and dump
    'insider_trading',           # Insiders manipulation
    'coordinated_wallets',      # Multiple wallets moving together
]


@dataclass
class WalletActivity:
    wallet_address: str
    timestamp: float
    activity_type: str
    amount: float
    token_address: Optional[str] = None
    transaction_hash: Optional[str] = None
    signer: Optional[str] = None


@dataclass
class WalletProfile:
    address: str
    first_seen: float
    last_activity: float
    category: str  # 'unknown', 'dev', 'whale', 'sniper', 'lp_provider'
    total_volume: float
    unique_tokens_traded: Set[str]
    net_flow: float
    suspicious_patterns: List[str]
    risk_score: float
    metadata: dict

    def __post_init__(self):
        if self.unique_tokens_traded is None:
            self.unique_tokens_traded = set()
        if self.suspicious_patterns is None:
            self.suspicious_patterns = []


class WalletTracker:
    def __init__(self):
        self.wallets: Dict[str, WalletProfile] = {}
        self.activities: List[WalletActivity] = []
        self.wallet_categories: Dict[str, str] = {}
        self.risk_patterns: Dict[str, List[str]] = defaultdict(list)
        self.last_batch_check: float = 0
        self._load()

    def _load(self):
        try:
            # Load wallet profiles
            if os.path.exists(WALLET_DATA_FILE):
                with open(WALLET_DATA_FILE, "r") as f:
                    data = json.load(f)
                    for addr, profile_data in data.get("profiles", {}).items():
                        profile_data["first_seen"] = profile_data.get("first_seen", time.time())
                        profile_data["unique_tokens_traded"] = set(profile_data.get("unique_tokens_traded", []))
                        self.wallets[addr] = WalletProfile(**profile_data)

            # Load known wallets (blacklist, special wallets, etc.)
            if os.path.exists(KNOWN_WALLETS_FILE):
                with open(KNOWN_WALLETS_FILE, "r") as f:
                    known = json.load(f)
                    self.wallet_categories.update(known.get("categories", {}))
                    for addr, category in known.get("wallet_categories", {}).items():
                        if addr in self.wallets:
                            self.wallets[addr].category = category

            logger.info(f"Wallet tracker loaded: {len(self.wallets)} wallets")
        except Exception as e:
            logger.error(f"Error loading wallet tracker: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(WALLET_DATA_FILE), exist_ok=True)
            data = {
                "profiles": {
                    addr: {
                        **asdict(profile),
                        "unique_tokens_traded": list(profile.unique_tokens_traded),
                    }
                    for addr, profile in self.wallets.items()
                },
                "saved_at": time.time(),
            }
            with open(WALLET_DATA_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving wallet tracker: {e}")

    def categorize_wallet(self, wallet_address: str) -> str:
        if wallet_address in self.wallet_categories:
            return self.wallet_categories[wallet_address]

        # Auto-categorize based on patterns
        profile = self.wallets.get(wallet_address)
        if profile:
            return profile.category

        return "unknown"

    def update_wallet_profile(self, wallet_address: str, category: str = None,
                             metadata: dict = None):
        now = time.time()
        if wallet_address not in self.wallets:
            self.wallets[wallet_address] = WalletProfile(
                address=wallet_address,
                first_seen=now,
                last_activity=now,
                category=category or "unknown",
                total_volume=0.0,
                unique_tokens_traded=set(),
                net_flow=0.0,
                suspicious_patterns=[],
                risk_score=0.0,
                metadata=metadata or {}
            )
        else:
            self.wallets[wallet_address].last_activity = now
            if category:
                self.wallets[wallet_address].category = category

        if metadata:
            self.wallets[wallet_address].metadata.update(metadata)

    def record_activity(self, activity: WalletActivity):
        self.activities.append(activity)
        profile = self.wallets.get(activity.wallet_address)

        if profile:
            profile.last_activity = activity.timestamp
            profile.total_volume += abs(activity.amount)
            if activity.token_address:
                profile.unique_tokens_traded.add(activity.token_address)
            profile.net_flow += activity.amount

            # Check for suspicious patterns
            self._analyze_suspicious_patterns(profile, activity)

        # Update category for new wallets
        if activity.wallet_address not in self.wallets:
            self._auto_categorize_wallet(activity.wallet_address)

    def _auto_categorize_wallet(self, wallet_address: str):
        profile = self.wallets.get(wallet_address)
        if not profile:
            return

        # Check if it's a known developer wallet
        if wallet_address.startswith(tuple(DEV_WALLET_PREFIXES)):
            profile.category = "dev"
            return

        # Check for whale characteristics
        if profile.total_volume > 100000:  # High volume
            profile.category = "whale"
            return

        profile.category = "unknown"

    def _analyze_suspicious_patterns(self, profile: WalletProfile, activity: WalletActivity):
        if activity.activity_type == "transfer_out":
            if abs(activity.amount) > profile.total_volume * 0.1:  # Large relative transfer
                profile.suspicious_patterns.append("rapid_balance_change")

        if activity.activity_type == "token_create":
            profile.suspicious_patterns.append("new_token_creation")

        # Check for coordinated movements
        recent_activities = [
            a for a in self.activities
            if a.wallet_address == activity.wallet_address
            and a.timestamp > activity.timestamp - 3600
            and a.activity_type == "transfer_out"
        ]

        if len(recent_activities) >= 3:
            profile.suspicious_patterns.append("multiple_transfers")

        # Update risk score based on patterns
        self._update_risk_score(profile)

    def _update_risk_score(self, profile: WalletProfile):
        risk_score = 0.0

        # Deduct points for suspicious patterns
        for pattern in profile.suspicious_patterns:
            if pattern in SUSPICIOUS_PATTERNS:
                pattern_index = SUSPICIOUS_PATTERNS.index(pattern)
                risk_score += (pattern_index + 1) * 0.1

        # Adjust based on category
        if profile.category == "dev":
            risk_score += 0.3
        elif profile.category == "whale":
            risk_score += 0.2

        # Adjust based on activity pattern
        if profile.net_flow < 0:  # Net outflow
            risk_score += 0.2

        profile.risk_score = min(risk_score, 1.0)

    def get_wallet_info(self, wallet_address: str) -> Optional[dict]:
        profile = self.wallets.get(wallet_address)
        if not profile:
            return None

        # Get recent activities
        recent_activities = [
            {
                "timestamp": a.timestamp,
                "type": a.activity_type,
                "amount": a.amount,
                "token": a.token_address,
            }
            for a in self.activities
            if a.wallet_address == wallet_address
            and a.timestamp > time.time() - 3600
        ]

        return {
            "address": profile.address,
            "category": profile.category,
            "risk_score": profile.risk_score,
            "total_volume": profile.total_volume,
            "unique_tokens_traded": len(profile.unique_tokens_traded),
            "net_flow": profile.net_flow,
            "first_seen": profile.first_seen,
            "last_activity": profile.last_activity,
            "suspicious_patterns": profile.suspicious_patterns,
            "metadata": profile.metadata,
            "recent_activities": recent_activities,
        }

    def get_high_risk_wallets(self, threshold: float = 0.7) -> List[dict]:
        high_risk = []
        for profile in self.wallets.values():
            if profile.risk_score >= threshold:
                high_risk.append({
                    "address": profile.address,
                    "category": profile.category,
                    "risk_score": profile.risk_score,
                    "patterns": profile.suspicious_patterns,
                    "total_volume": profile.total_volume,
                })
        return sorted(high_risk, key=lambda x: x["risk_score"], reverse=True)

    def get_risk_analysis_by_category(self) -> dict:
        analysis = {}
        for category in ["dev", "whale", "sniper", "lp_provider", "unknown"]:
            wallets = [w for w in self.wallets.values() if w.category == category]
            if not wallets:
                continue

            avg_risk = sum(w.risk_score for w in wallets) / len(wallets)
            avg_volume = sum(w.total_volume for w in wallets) / len(wallets)

            analysis[category] = {
                "wallet_count": len(wallets),
                "avg_risk_score": avg_risk,
                "avg_volume": avg_volume,
                "most_common_patterns": Counter(
                    pattern for w in wallets for pattern in w.suspicious_patterns
                ).most_common(5),
            }

        return analysis

    def get_top_risky_tokens_by_wallet(self) -> Dict[str, List[str]]:
        token_by_wallet = defaultdict(list)
        for activity in self.activities:
            if activity.token_address:
                token_by_wallet[activity.wallet_address].append(activity.token_address)

        return {
            wallet: tokens[:10]  # Top 10 tokens per wallet
            for wallet, tokens in token_by_wallet.items()
        }

    def save(self):
        self._save()

    def cleanup_old_data(self, max_age_days: float = 30):
        cutoff = time.time() - (max_age_days * 86400)

        # Remove old activities
        self.activities = [
            a for a in self.activities if a.timestamp >= cutoff
        ]

        # Remove inactive wallets
        inactive = [
            addr for addr, profile in self.wallets.items()
            if profile.last_activity < cutoff and profile.category != "dev"
        ]

        for addr in inactive:
            del self.wallets[addr]

        if inactive:
            logger.info(f"Cleaned up {len(inactive)} inactive wallets")

    def get_summary_stats(self) -> dict:
        return {
            "total_wallets": len(self.wallets),
            "total_activities": len(self.activities),
            "categories": {
                category: len([w for w in self.wallets.values() if w.category == category])
                for category in set(w.category for w in self.wallets.values())
            },
            "high_risk_wallets": len(self.get_high_risk_wallets(0.7)),
            "avg_risk_score": sum(w.risk_score for w in self.wallets.values()) / len(self.wallets) if self.wallets else 0,
            "last_updated": time.time(),
        }


wallet_tracker = WalletTracker()
