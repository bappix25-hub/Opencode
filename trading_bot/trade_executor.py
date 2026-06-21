import asyncio
import aiohttp
import logging
from typing import Optional
from config import config
from wallet_manager import WalletManager

logger = logging.getLogger("executor")

JUPITER_QUOTE = "https://api.jup.ag/swap/v2/quote"
JUPITER_SWAP = "https://api.jup.ag/swap/v2/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"


class TradeExecutor:
    def __init__(self, session: aiohttp.ClientSession, wallet: WalletManager):
        self.session = session
        self.wallet = wallet

    async def get_quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = None) -> Optional[dict]:
        if slippage_bps is None:
            slippage_bps = config.max_slippage_bps

        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": slippage_bps,
            }
            async with self.session.get(
                JUPITER_QUOTE, params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and data.get("outAmount"):
                        return {
                            "in_amount": int(data.get("inAmount", 0)),
                            "out_amount": int(data.get("outAmount", 0)),
                            "price_impact_pct": float(data.get("priceImpactPct", 0)),
                            "route": data.get("routePlan", []),
                        }
        except Exception as e:
            logger.error(f"Quote error: {e}")
        return None

    async def buy_token(self, token_address: str, sol_amount: float) -> Optional[dict]:
        if not self.wallet.is_loaded():
            logger.warning("Paper mode - simulated buy")
            return {
                "type": "paper_buy",
                "token_address": token_address,
                "sol_amount": sol_amount,
                "simulated": True,
            }

        lamports = self.wallet.sol_to_lamports(sol_amount)
        quote = await self.get_quote(SOL_MINT, token_address, lamports)
        if not quote:
            logger.error("Failed to get buy quote")
            return None

        if abs(quote["price_impact_pct"]) > 10:
            logger.warning(f"High price impact: {quote['price_impact_pct']:.2f}%")
            return None

        logger.info(
            f"BUY QUOTE: {sol_amount:.4f} SOL -> {quote['out_amount']} tokens "
            f"(impact: {quote['price_impact_pct']:.2f}%)"
        )

        try:
            swap_payload = {
                "quoteResponse": quote,
                "userPublicKey": self.wallet.get_public_key(),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            }
            async with self.session.post(
                JUPITER_SWAP, json=swap_payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    swap_data = await resp.json()
                    swap_tx = swap_data.get("swapTransaction")
                    if swap_tx:
                        logger.info(f"Swap transaction ready for {token_address}")
                        return {
                            "type": "buy",
                            "token_address": token_address,
                            "sol_amount": sol_amount,
                            "token_amount": quote["out_amount"],
                            "price_impact": quote["price_impact_pct"],
                            "swap_transaction": swap_tx,
                            "simulated": False,
                        }
                else:
                    error_text = await resp.text()
                    logger.error(f"Swap error {resp.status}: {error_text}")
        except Exception as e:
            logger.error(f"Swap execution error: {e}")

        return None

    async def sell_token(self, token_address: str, token_amount: int) -> Optional[dict]:
        if not self.wallet.is_loaded():
            logger.warning("Paper mode - simulated sell")
            return {
                "type": "paper_sell",
                "token_address": token_address,
                "token_amount": token_amount,
                "simulated": True,
            }

        quote = await self.get_quote(token_address, SOL_MINT, token_amount)
        if not quote:
            logger.error("Failed to get sell quote")
            return None

        logger.info(
            f"SELL QUOTE: {token_amount} tokens -> {quote['out_amount'] / 1e9:.4f} SOL "
            f"(impact: {quote['price_impact_pct']:.2f}%)"
        )

        try:
            swap_payload = {
                "quoteResponse": quote,
                "userPublicKey": self.wallet.get_public_key(),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            }
            async with self.session.post(
                JUPITER_SWAP, json=swap_payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    swap_data = await resp.json()
                    swap_tx = swap_data.get("swapTransaction")
                    if swap_tx:
                        return {
                            "type": "sell",
                            "token_address": token_address,
                            "token_amount": token_amount,
                            "sol_amount": quote["out_amount"] / 1e9,
                            "price_impact": quote["price_impact_pct"],
                            "swap_transaction": swap_tx,
                            "simulated": False,
                        }
                else:
                    error_text = await resp.text()
                    logger.error(f"Sell swap error {resp.status}: {error_text}")
        except Exception as e:
            logger.error(f"Sell execution error: {e}")

        return None
