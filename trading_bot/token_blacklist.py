"""Token blacklist manager for previously dumped tokens."""

import json
from datetime import datetime, timedelta
from pathlib import Path


BLACKLIST_PATH = Path(__file__).parent / "data" / "blacklist.json"
SIGNALS_BACKUP_PATH = Path(__file__).parent / "data" / "signals_backup.json"
CLEANUP_DAYS = 30


def _load_json(path: Path) -> dict:
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


class TokenBlacklist:
    def __init__(self):
        self._data = _load_json(BLACKLIST_PATH)
        if "addresses" not in self._data:
            self._data["addresses"] = {}
        if "symbol_patterns" not in self._data:
            self._data["symbol_patterns"] = {}
        self._cleanup_old_entries()

    # ------------------------------------------------------------------
    def is_blacklisted(self, address: str, symbol: str) -> tuple[bool, str]:
        address = address.lower()
        symbol_lower = symbol.lower()

        entry = self._data["addresses"].get(address)
        if entry:
            return True, entry.get("reason", "address blacklisted")

        for pattern, info in self._data["symbol_patterns"].items():
            if pattern.lower() in symbol_lower:
                return True, info.get("reason", f"symbol contains '{pattern}'")

        return False, ""

    # ------------------------------------------------------------------
    def add_to_blacklist(
        self,
        address: str,
        symbol: str,
        reason: str,
        pnl_pct: float,
    ) -> None:
        address = address.lower()
        entry = {
            "symbol": symbol,
            "reason": reason,
            "pnl_pct": pnl_pct,
            "added_at": _now_iso(),
        }
        self._data["addresses"][address] = entry

        symbol_lower = symbol.lower()
        if symbol_lower not in self._data["symbol_patterns"]:
            self._data["symbol_patterns"][symbol_lower] = {
                "reason": reason,
                "added_at": _now_iso(),
                "pnl_pct": pnl_pct,
            }

        _save_json(BLACKLIST_PATH, self._data)

    # ------------------------------------------------------------------
    def get_blacklist_stats(self) -> dict:
        return {
            "total_addresses": len(self._data["addresses"]),
            "total_symbol_patterns": len(self._data["symbol_patterns"]),
            "addresses": self._data["addresses"],
            "symbol_patterns": self._data["symbol_patterns"],
        }

    # ------------------------------------------------------------------
    def learn_from_signals(self) -> int:
        added = 0
        added += self._learn_from_backup()
        added += self._learn_from_learning()
        return added

    def _learn_from_backup(self) -> int:
        if not SIGNALS_BACKUP_PATH.exists():
            return 0

        signals = _load_json(SIGNALS_BACKUP_PATH)
        added = 0

        items = signals if isinstance(signals, list) else signals.get("signals", [])
        for sig in items:
            pnl = sig.get("final_pnl_pct", 0) or sig.get("pnl_pct", 0) or sig.get("pnl", 0)
            if pnl >= -30:
                continue

            addr = sig.get("address", "").lower()
            sym = sig.get("symbol", "") or sig.get("token_symbol", "")
            if not addr or not sym:
                continue

            if addr not in self._data["addresses"]:
                reason = f"dumped {pnl:.0f}% in prior trade"
                self.add_to_blacklist(addr, sym, reason, pnl)
                added += 1

        if added:
            _save_json(BLACKLIST_PATH, self._data)

        return added

    def _learn_from_learning(self) -> int:
        learning_path = Path(__file__).parent / "data" / "learning.json"
        if not learning_path.exists():
            return 0

        data = _load_json(learning_path)
        outcomes = data.get("trade_outcomes", [])
        added = 0

        for o in outcomes:
            pnl = o.get("pnl_pct", 0)
            if pnl >= -30:
                continue

            addr = o.get("address", "").lower()
            sym = o.get("symbol", "")
            if not addr or not sym:
                continue

            if addr not in self._data["addresses"]:
                reason = f"dumped {pnl:.0f}% (learner data)"
                self.add_to_blacklist(addr, sym, reason, pnl)
                added += 1

        if added:
            _save_json(BLACKLIST_PATH, self._data)

        return added

    # ------------------------------------------------------------------
    def _cleanup_old_entries(self) -> None:
        cutoff = datetime.utcnow() - timedelta(days=CLEANUP_DAYS)
        changed = False

        for store in ("addresses", "symbol_patterns"):
            to_remove = []
            for key, info in self._data[store].items():
                ts = info.get("added_at")
                if ts:
                    try:
                        added = datetime.fromisoformat(ts)
                        if added < cutoff:
                            to_remove.append(key)
                    except (ValueError, TypeError):
                        pass
            for key in to_remove:
                del self._data[store][key]
                changed = True

        if changed:
            _save_json(BLACKLIST_PATH, self._data)
