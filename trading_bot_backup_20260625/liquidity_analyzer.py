import logging
import time
from typing import Tuple

logger = logging.getLogger("liquidity")


class LiquidityAnalyzer:
    def analyze(self, metrics: dict) -> Tuple[float, list, list, dict]:
        risk_score = 0.0
        warnings = []
        good_signs = []
        details = {}

        liquidity = metrics.get("liquidity", 0)
        fdv = metrics.get("fdv", 0)
        vol_5m = metrics.get("volume_5m", 0)
        holder_count = metrics.get("holder_count", 0)

        txns = metrics.get("transactions", {})
        m5 = txns.get("m5", {})
        h1 = txns.get("h1", {})

        buyers_5m = m5.get("buyers", 0)
        sellers_5m = m5.get("sellers", 0)
        buyers_1h = h1.get("buyers", 0)
        sellers_1h = h1.get("sellers", 0)

        unique_5m = buyers_5m + sellers_5m
        unique_1h = buyers_1h + sellers_1h

        details["buyers_5m"] = buyers_5m
        details["sellers_5m"] = sellers_5m
        details["unique_5m"] = unique_5m
        details["unique_1h"] = unique_1h

        # === CRITICAL: Unique wallets = LP provider proxy ===
        # If only 1-2 wallets trading = likely single LP provider = HIGH RISK
        if unique_5m == 0:
            risk_score += 0.3
            warnings.append("No trading activity (5m)")
        elif unique_5m == 1:
            risk_score += 0.5
            warnings.append("1 wallet only (single LP provider?)")
        elif unique_5m == 2:
            risk_score += 0.3
            warnings.append("Only 2 wallets (possible single LP)")
        elif unique_5m >= 8:
            risk_score -= 0.15
            good_signs.append(f"{unique_5m} wallets active")
        elif unique_5m >= 5:
            risk_score -= 0.05

        # Also check 1h data for more confidence
        if unique_1h > 0 and unique_1h <= 2:
            risk_score += 0.2
            warnings.append(f"Only {unique_1h} unique wallets in 1h")
        elif unique_1h >= 15:
            risk_score -= 0.1
            good_signs.append(f"{unique_1h} wallets in 1h")

        # === CRITICAL: Liquidity absolute minimum ===
        if liquidity < 10:
            risk_score += 0.7
            warnings.append(f"CRITICAL: LP ${liquidity:.2f} (near zero!)")
        elif liquidity < 100:
            risk_score += 0.5
            warnings.append(f"CRITICAL: LP ${liquidity:.0f} (no real LP)")
        elif liquidity < 500:
            risk_score += 0.3
            warnings.append(f"Very low LP ${liquidity:.0f}")
        elif liquidity < 1000:
            risk_score += 0.15
        elif liquidity < 3000:
            risk_score += 0.05
            warnings.append(f"Low LP ${liquidity:.0f}")
        elif liquidity >= 10000:
            risk_score -= 0.15
            good_signs.append(f"Good LP ${liquidity:.0f}")
        elif liquidity >= 5000:
            risk_score -= 0.05

        # === CRITICAL: MC/LP ratio ===
        if liquidity > 0 and fdv > 0:
            fdv_liq_ratio = fdv / liquidity
            details["fdv_liq_ratio"] = round(fdv_liq_ratio, 1)
            if fdv_liq_ratio > 1000:
                risk_score += 0.6
                warnings.append(f"SCAM: MC ${fdv:.0f} but LP ${liquidity:.0f} ({fdv_liq_ratio:.0f}x!)")
            elif fdv_liq_ratio > 500:
                risk_score += 0.5
                warnings.append(f"FAKE MC: {fdv_liq_ratio:.0f}x MC/LP ratio")
            elif fdv_liq_ratio > 100:
                risk_score += 0.3
                warnings.append(f"High MC/LP ratio {fdv_liq_ratio:.0f}x")
            elif fdv_liq_ratio < 3:
                risk_score -= 0.15
                good_signs.append(f"Healthy MC/LP {fdv_liq_ratio:.1f}x")

        # === Holder count (if available) ===
        if holder_count > 0:
            details["holder_count"] = holder_count
            if holder_count <= 3:
                risk_score += 0.5
                warnings.append(f"SCAM: Only {holder_count} holders!")
            elif holder_count <= 10:
                risk_score += 0.2
                warnings.append(f"Very few holders ({holder_count})")
            elif holder_count >= 100:
                risk_score -= 0.15
                good_signs.append(f"Good holder count ({holder_count})")

        # === GMGN data (if available) ===
        lp_count = metrics.get("lp_count", 0)
        top_10_pct = metrics.get("top_10_pct", 0)
        bundler_pct = metrics.get("bundler_pct", 0)
        lock_pct = metrics.get("lock_pct", 0)

        if lp_count > 0:
            details["lp_count"] = lp_count
            if lp_count == 1:
                risk_score += 0.6
                warnings.append(f"CRITICAL: Single LP provider (LP Count = 1)")
            elif lp_count <= 2:
                risk_score += 0.3
                warnings.append(f"Low LP count ({lp_count})")
            elif lp_count >= 5:
                risk_score -= 0.1
                good_signs.append(f"Good LP count ({lp_count})")

        if top_10_pct > 0:
            details["top_10_pct"] = top_10_pct
            if top_10_pct > 0.9:
                risk_score += 0.4
                warnings.append(f"SCAM: Top 10 hold {top_10_pct*100:.1f}%!")
            elif top_10_pct > 0.7:
                risk_score += 0.2
                warnings.append(f"High concentration: Top 10 hold {top_10_pct*100:.1f}%")

        if bundler_pct > 0:
            details["bundler_pct"] = bundler_pct
            if bundler_pct > 0.3:
                risk_score += 0.3
                warnings.append(f"HIGH BUNDLER: {bundler_pct*100:.1f}% bundled!")

        if lock_pct > 0:
            details["lock_pct"] = lock_pct
            if lock_pct > 0.5:
                risk_score -= 0.1
                good_signs.append(f"LP locked {lock_pct*100:.0f}%")

        # === Sell pressure check ===
        if sellers_5m > buyers_5m * 2 and sellers_5m >= 3:
            risk_score += 0.2
            warnings.append(f"Sell pressure ({sellers_5m}S/{buyers_5m}B)")
        elif buyers_5m > sellers_5m * 2 and buyers_5m >= 3:
            good_signs.append(f"Buy pressure ({buyers_5m}B/{sellers_5m}S)")

        # === Volume check ===
        if vol_5m > 0 and liquidity > 0:
            vol_liq_ratio = vol_5m / liquidity
            details["vol_liq_ratio_5m"] = round(vol_liq_ratio, 2)
            if vol_liq_ratio > 10:
                risk_score += 0.15
                warnings.append(f"High Vol/Liq {vol_liq_ratio:.1f}x")

        if vol_5m == 0 and liquidity > 0:
            risk_score += 0.1
            warnings.append("No trading volume")

        # === Combined heuristic: Low wallets + low liquidity = definitely single LP ===
        if unique_5m <= 2 and liquidity < 5000:
            risk_score += 0.3
            warnings.append("Single LP provider (low wallets + low liq)")

        risk_score = max(min(risk_score, 1.0), -0.4)
        details["risk_score"] = round(risk_score, 2)
        details["good_signs"] = good_signs

        return risk_score, warnings, good_signs, details

    def is_safe(self, metrics: dict) -> Tuple[bool, str]:
        risk_score, warnings, good_signs, details = self.analyze(metrics)

        if risk_score > 0.4:
            return False, f"SCAM ({risk_score:.2f}): {', '.join(warnings[:2])}"

        liquidity = metrics.get("liquidity", 0)
        fdv = metrics.get("fdv", 0)
        lp_count = metrics.get("lp_count", 0)

        if lp_count == 1:
            return False, "Single LP provider (LP Count = 1)"

        if liquidity < 500:
            return False, f"No real liquidity ${liquidity:.2f}"

        if fdv > 0 and liquidity > 0:
            if fdv / liquidity > 100:
                return False, f"MC {fdv/liquidity:.0f}x higher than LP"

        return True, ""


liquidity_analyzer = LiquidityAnalyzer()
