#!/usr/bin/env python3
"""
Comprehensive Backtest Trainer for Solana Meme Coin Bot
Fetches 200+ tokens, simulates launch behavior, trains bot continuously.
"""

import asyncio
import json
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import sys
import os

sys.path.insert(0, '/root/Opencode')

from dex_client import DexScreenerClient
from learner import (
    load_data, save_data, extract_launch_features, record_signal_result,
    match_pump_patterns, match_dump_patterns, enhanced_auto_learn,
    calculate_optimal_tp_sl, get_performance_report, _update_hourly_stats,
    record_launch, PUMP_THRESHOLD, DUMP_THRESHOLD
)
from honeypot_detector import HoneypotDetector
from bot_state import BotState
import aiohttp


class BacktestTrainer:
    def __init__(self):
        self.session = None
        self.dex = None
        self.honeypot = None
        self.state = BotState()
        self.tokens_processed = 0
        self.tokens_learned = 0
        self.session_start = datetime.now(timezone.utc)
    
    async def init_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self.dex = DexScreenerClient(self.session)
            self.honeypot = HoneypotDetector(self.session)
        
    async def fetch_token_universe(self, min_liquidity: float = 100, max_tokens: int = 300) -> List[Dict]:
        """Fetch tokens from DexScreener with sufficient data for backtesting."""
        await self.init_session()
        print(f"🔍 Fetching token universe (max {max_tokens}, min liq ${min_liquidity})...")
        
        all_tokens = []
        seen_addresses = set()
        
        # Method 1: Top pairs
        try:
            pairs = await self.dex.fetch_top_pairs(limit=max_tokens * 3)
            if pairs:
                for pair in pairs:
                    if len(all_tokens) >= max_tokens:
                        break
                    token = self._process_pair(pair, min_liquidity, seen_addresses)
                    if token:
                        all_tokens.append(token)
        except Exception as e:
            print(f"  ⚠️ Error fetching top pairs: {e}")
        
        # Method 2: Boosted pairs
        if len(all_tokens) < max_tokens:
            try:
                pairs = await self.dex.fetch_boosted_pairs()
                if pairs:
                    for pair in pairs:
                        if len(all_tokens) >= max_tokens:
                            break
                        token = self._process_pair(pair, min_liquidity, seen_addresses)
                        if token:
                            all_tokens.append(token)
            except Exception as e:
                print(f"  ⚠️ Error fetching boosted pairs: {e}")
        
        # Method 3: New Solana pairs
        if len(all_tokens) < max_tokens:
            try:
                pairs = await self.dex.fetch_new_solana_pairs()
                if pairs:
                    for pair in pairs:
                        if len(all_tokens) >= max_tokens:
                            break
                        token = self._process_pair(pair, min_liquidity, seen_addresses)
                        if token:
                            all_tokens.append(token)
            except Exception as e:
                print(f"  ⚠️ Error fetching new pairs: {e}")
                
        print(f"  ✅ Found {len(all_tokens)} tokens")
        return all_tokens
    
    def _process_pair(self, pair: Dict, min_liquidity: float, seen_addresses: set) -> Optional[Dict]:
        """Process a pair and return token dict if valid."""
        liq = float((pair.get('liquidity') or {}).get('usd', 0) or 0)
        fdv = float(pair.get('fdv', 0) or 0)
        volume_24h = float(pair.get('volume', {}).get('h24', 0) or 0)
        price_change_24h = float(pair.get('priceChange', {}).get('h24', 0) or 0)
        created_at = pair.get('pairCreatedAt')
        
        if liq < min_liquidity:
            return None
        if fdv < 500:
            return None
        if not created_at:
            return None
            
        base_token = pair.get('baseToken', {})
        address = base_token.get('address', '')
        symbol = base_token.get('symbol', 'UNKNOWN')
        
        if not address or len(address) < 32:
            return None
        if address in seen_addresses:
            return None
            
        seen_addresses.add(address)
        
        return {
            'address': address,
            'symbol': symbol,
            'name': base_token.get('name', symbol),
            'liquidity': liq,
            'fdv': fdv,
            'volume_24h': volume_24h,
            'price_change_24h': price_change_24h,
            'created_at': created_at,
            'pair_data': pair
        }
    
    def simulate_launch_behavior(self, token: Dict) -> Dict:
        """Simulate realistic launch behavior based on token characteristics."""
        liq = token['liquidity']
        fdv = token['fdv']
        volume = token['volume_24h']
        price_change = token['price_change_24h']
        age_hours = (datetime.now(timezone.utc).timestamp() * 1000 - token['created_at']) / 3_600_000
        
        # Base probability of being a pump
        pump_prob = 0.3
        
        # Higher liquidity = more stable, less likely to rug
        if liq > 50000:
            pump_prob += 0.15
        elif liq > 10000:
            pump_prob += 0.10
        elif liq > 5000:
            pump_prob += 0.05
            
        # Higher volume = more interest
        if volume > 100000:
            pump_prob += 0.15
        elif volume > 50000:
            pump_prob += 0.10
        elif volume > 10000:
            pump_prob += 0.05
            
        # Positive price change = momentum
        if price_change > 50:
            pump_prob += 0.20
        elif price_change > 20:
            pump_prob += 0.15
        elif price_change > 0:
            pump_prob += 0.10
        elif price_change < -50:
            pump_prob -= 0.20
        elif price_change < -20:
            pump_prob -= 0.10
            
        # Age factor - newer tokens more volatile
        if age_hours < 24:
            pump_prob += 0.10
        elif age_hours < 168:  # 1 week
            pump_prob += 0.05
            
        pump_prob = max(0.05, min(0.95, pump_prob))
        
        is_pump = random.random() < pump_prob
        
        if is_pump:
            # Pump: 2x to 50x ATH
            ath_multiplier = random.uniform(2.0, 50.0)
            if random.random() < 0.3:  # 30% strong pumps
                ath_multiplier = random.uniform(10.0, 100.0)
            current_multiplier = random.uniform(0.1, ath_multiplier * 0.8)
            min_price_multiplier = random.uniform(0.1, 0.95)
            verdict = "STRONG_PUMP" if ath_multiplier >= 5.0 else "PUMP"
        else:
            # Dump: -90% to -50%
            ath_multiplier = random.uniform(1.0, 2.5)
            current_multiplier = random.uniform(0.05, 0.5)
            min_price_multiplier = random.uniform(0.02, 0.4)
            verdict = "DUMP"
            
        signal_age = random.uniform(300, 21600)  # 5 min to 6 hours
        
        return {
            'is_pump': is_pump,
            'verdict': verdict,
            'ath_multiplier': round(ath_multiplier, 2),
            'current_multiplier': round(current_multiplier, 2),
            'min_price_multiplier': round(min_price_multiplier, 2),
            'signal_age': round(signal_age, 0),
            'pump_probability': round(pump_prob, 2)
        }
    
    def generate_realistic_features(self, token: Dict, behavior: Dict) -> Dict:
        """Generate realistic launch features based on token and behavior."""
        liq = token['liquidity']
        fdv = token['fdv']
        volume = token['volume_24h']
        is_pump = behavior['is_pump']
        
        if is_pump:
            # Pump characteristics
            buy_count = random.randint(50, 500)
            sell_count = random.randint(10, buy_count // 2)
            unique_wallets = random.randint(30, 200)
            holders = random.randint(50, 500)
            buy_sell_ratio = random.uniform(1.5, 5.0)
            snipers_30s = random.randint(3, 20)
            liq_pct = random.uniform(5, 30)
            lp_locked = random.randint(80, 100)
            lp_providers = random.randint(2, 5)
            social_score = random.uniform(0.3, 0.9)
            volume_velocity = random.uniform(1000, 50000)
            buy_sell_momentum = random.uniform(1.5, 3.0)
            volume_spike_ratio = random.uniform(2.0, 10.0)
            buy_spike_ratio = random.uniform(2.0, 5.0)
            insider_count = random.randint(1, 5)
        else:
            # Dump characteristics
            buy_count = random.randint(5, 50)
            sell_count = random.randint(buy_count, buy_count * 3)
            unique_wallets = random.randint(3, 20)
            holders = random.randint(2, 20)
            buy_sell_ratio = random.uniform(0.2, 1.0)
            snipers_30s = random.randint(0, 3)
            liq_pct = random.uniform(0.5, 5)
            lp_locked = random.randint(0, 50)
            lp_providers = random.randint(1, 2)
            social_score = random.uniform(0.0, 0.3)
            volume_velocity = random.uniform(10, 1000)
            buy_sell_momentum = random.uniform(0.1, 0.8)
            volume_spike_ratio = random.uniform(0.1, 1.5)
            buy_spike_ratio = random.uniform(0.1, 1.0)
            insider_count = random.randint(0, 1)
            
        features = {
            'unique_wallets': unique_wallets,
            'buy_count': buy_count,
            'sell_count': sell_count,
            'buy_sell_ratio': round(buy_sell_ratio, 2),
            'holders': holders,
            'snipers_30s': snipers_30s,
            'liq_pct': round(liq_pct, 1),
            'lp_locked': lp_locked,
            'lp_providers_count': lp_providers,
            'social_score': round(social_score, 2),
            'volume_velocity': round(volume_velocity, 1),
            'buy_sell_momentum': round(buy_sell_momentum, 2),
            'volume_spike_ratio': round(volume_spike_ratio, 2),
            'buy_spike_ratio': round(buy_spike_ratio, 2),
            'insider_count': insider_count,
            'liquidity': liq,
            'mcap': fdv,
            'launch_time': token['created_at'] / 1000,
            'launch_weekday': datetime.fromtimestamp(token['created_at'] / 1000, tz=timezone.utc).weekday(),
            'launch_hour': datetime.fromtimestamp(token['created_at'] / 1000, tz=timezone.utc).hour,
            'launch_month': datetime.fromtimestamp(token['created_at'] / 1000, tz=timezone.utc).month,
            'is_weekend': 1 if datetime.fromtimestamp(token['created_at'] / 1000, tz=timezone.utc).weekday() >= 5 else 0,
            'launch_session': (datetime.fromtimestamp(token['created_at'] / 1000, tz=timezone.utc).hour // 8) % 3,
        }
        
        return features
    
    async def train_on_token(self, token: Dict) -> bool:
        """Train bot on a single token's simulated behavior."""
        try:
            behavior = self.simulate_launch_behavior(token)
            features = self.generate_realistic_features(token, behavior)
            
            # Record launch
            record_launch(
                address=token['address'],
                symbol=token['symbol'],
                features=features
            )
            
            # Record signal result (simulate that bot would have signaled)
            # Only record if features would have passed initial filters
            if (features['holders'] >= 3 and 
                features['unique_wallets'] >= 5 and 
                features['buy_sell_ratio'] >= 1.0 and
                features['liquidity'] >= 500):
                
                record_signal_result(
                    address=token['address'],
                    symbol=token['symbol'],
                    ath_multiplier=behavior['ath_multiplier'],
                    current_multiplier=behavior['current_multiplier'],
                    signal_age=behavior['signal_age'],
                    min_price=behavior['min_price_multiplier']
                )
                
                # Also add to pump/dump patterns
                data = load_data()
                if behavior['verdict'] in ("PUMP", "STRONG_PUMP"):
                    pump_patterns = data.setdefault("pump_patterns", [])
                    existing = next((p for p in pump_patterns if p.get("address") == token['address']), None)
                    if not existing:
                        features_copy = features.copy()
                        features_copy["ath_multiplier"] = behavior['ath_multiplier']
                        features_copy["outcome"] = behavior['verdict']
                        features_copy["signal_age"] = behavior['signal_age']
                        pump_patterns.append({
                            "address": token['address'],
                            "symbol": token['symbol'],
                            "features": features_copy,
                            "outcome": behavior['verdict'],
                            "ath_multiplier": behavior['ath_multiplier'],
                            "signal_age": behavior['signal_age'],
                            "learned_at": datetime.now(timezone.utc).isoformat(),
                        })
                        data["model"]["total_pumps"] = data["model"].get("total_pumps", 0) + 1
                else:
                    dump_patterns = data.setdefault("dump_patterns", [])
                    existing = next((p for p in dump_patterns if p.get("address") == token['address']), None)
                    if not existing:
                        features_copy = features.copy()
                        features_copy["ath_multiplier"] = behavior['ath_multiplier']
                        features_copy["outcome"] = behavior['verdict']
                        features_copy["signal_age"] = behavior['signal_age']
                        dump_patterns.append({
                            "address": token['address'],
                            "symbol": token['symbol'],
                            "features": features_copy,
                            "outcome": behavior['verdict'],
                            "ath_multiplier": behavior['ath_multiplier'],
                            "signal_age": behavior['signal_age'],
                            "learned_at": datetime.now(timezone.utc).isoformat(),
                        })
                        data["model"]["total_dumps"] = data["model"].get("total_dumps", 0) + 1
                        
                save_data(data)
                self.tokens_learned += 1
                
            self.tokens_processed += 1
            return True
            
        except Exception as e:
            print(f"  ⚠️ Error training on {token['symbol']}: {e}")
            return False
    
    async def run_training_cycle(self, num_tokens: int = 200):
        """Run a full training cycle."""
        print(f"\n{'='*60}")
        print(f"🚀 BACKTEST TRAINING CYCLE STARTED")
        print(f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*60}")
        
        tokens = await self.fetch_token_universe(max_tokens=num_tokens, min_liquidity=0)
        
        if not tokens:
            print("❌ No tokens fetched")
            return
            
        print(f"\n📚 Training on {len(tokens)} tokens...")
        
        for i, token in enumerate(tokens, 1):
            await self.train_on_token(token)
            if i % 20 == 0:
                print(f"  Progress: {i}/{len(tokens)} tokens processed, {self.tokens_learned} learned")
                
        # Run enhanced auto-learn
        print(f"\n🧠 Running enhanced auto-learn...")
        enhanced_auto_learn()
        
        # Calculate optimal TP/SL
        print(f"\n📊 Calculating optimal TP/SL...")
        perf = get_performance_report()
        
        print(f"\n{'='*60}")
        print(f"✅ TRAINING CYCLE COMPLETE")
        print(f"📈 Tokens processed: {self.tokens_processed}")
        print(f"🎯 Tokens learned from: {self.tokens_learned}")
        print(f"📊 Performance: {perf['total']} signals, {perf['win_rate']:.1f}% win rate")
        print(f"🎯 Optimal TP: {perf['optimal_tp']}%, SL: {perf['optimal_sl']}%")
        print(f"💰 Expected PnL: {perf['expected_pnl']:.1f}%")
        print(f"{'='*60}\n")
        
        return perf
    
    async def run_continuous(self, cycle_interval_hours: int = 1, max_tokens: int = 200):
        """Run continuous 24/7 training."""
        print(f"\n🔄 STARTING CONTINUOUS 24/7 TRAINING")
        print(f"   Cycle interval: {cycle_interval_hours} hour(s)")
        print(f"   Tokens per cycle: {max_tokens}")
        print(f"   Press Ctrl+C to stop\n")
        
        cycle = 0
        while True:
            cycle += 1
            print(f"\n{'#'*60}")
            print(f"# CYCLE #{cycle} - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print(f"{'#'*60}")
            
            try:
                await self.run_training_cycle(max_tokens)
            except Exception as e:
                print(f"❌ Cycle error: {e}")
                
            # Wait for next cycle
            if cycle_interval_hours > 0:
                print(f"⏳ Waiting {cycle_interval_hours} hour(s) until next cycle...")
                await asyncio.sleep(cycle_interval_hours * 3600)
    
    def _process_pair(self, pair: Dict, min_liquidity: float, seen_addresses: set) -> Optional[Dict]:
        """Process a pair and return token dict if valid."""
        liq = float((pair.get('liquidity') or {}).get('usd', 0) or 0)
        fdv = float(pair.get('fdv', 0) or 0)
        volume_24h = float(pair.get('volume', {}).get('h24', 0) or 0)
        price_change_24h = float(pair.get('priceChange', {}).get('h24', 0) or 0)
        created_at = pair.get('pairCreatedAt')
        
        if liq < min_liquidity:
            return None
        if fdv < 100:  # Lower threshold
            return None
        if not created_at:
            return None
            
        base_token = pair.get('baseToken', {})
        address = base_token.get('address', '')
        symbol = base_token.get('symbol', 'UNKNOWN')
        
        if not address or len(address) < 32:
            return None
        if address in seen_addresses:
            return None
            
        seen_addresses.add(address)
        
        return {
            'address': address,
            'symbol': symbol,
            'name': base_token.get('name', symbol),
            'liquidity': liq,
            'fdv': fdv,
            'volume_24h': volume_24h,
            'price_change_24h': price_change_24h,
            'created_at': created_at,
            'pair_data': pair
        }


async def main():
    trainer = BacktestTrainer()
    
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--once':
            await trainer.run_training_cycle(200)
        else:
            await trainer.run_continuous(cycle_interval_hours=1, max_tokens=200)
    finally:
        if trainer.session:
            await trainer.session.close()


if __name__ == "__main__":
    asyncio.run(main())