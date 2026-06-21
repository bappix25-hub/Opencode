import logging
from typing import Optional
from config import config

logger = logging.getLogger("wallet")

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


class WalletManager:
    def __init__(self):
        self.private_key = config.solana_private_key
        self.keypair = None
        self._load_keypair()

    def _load_keypair(self):
        if not self.private_key:
            logger.warning("No SOLANA_PRIVATE_KEY set - paper trading mode only")
            return

        try:
            from solders.keypair import Keypair
            import base58

            secret = base58.b58decode(self.private_key)
            self.keypair = Keypair.from_bytes(secret)
            logger.info(f"Wallet loaded: {self.keypair.pubkey()}")
        except ImportError:
            logger.warning("solders/base58 not installed - paper trading only")
        except Exception as e:
            logger.error(f"Wallet load error: {e}")

    def is_loaded(self) -> bool:
        return self.keypair is not None

    def get_public_key(self) -> str:
        if self.keypair:
            return str(self.keypair.pubkey())
        return "PAPER_MODE"

    async def get_sol_balance(self, session) -> float:
        if not self.is_loaded():
            return 0.0

        try:
            url = f"https://mainnet.helius-rpc.com/?api-key={config.helius_api_key}"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [self.get_public_key()],
            }
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    lamports = data.get("result", {}).get("value", 0)
                    return lamports / LAMPORTS_PER_SOL
        except Exception as e:
            logger.error(f"Balance fetch error: {e}")
        return 0.0

    def sol_to_lamports(self, sol: float) -> int:
        return int(sol * LAMPORTS_PER_SOL)

    def lamports_to_sol(self, lamports: int) -> float:
        return lamports / LAMPORTS_PER_SOL
