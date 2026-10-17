import os
from dataclasses import dataclass

@dataclass
class FomoConfig:
    # Telegram Credentials
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Jupiter & Trading Filters
    JUPITER_ONLY: bool = True
    MIN_BUY_USD: float = float(os.getenv("MIN_BUY_USD", "1000.0"))
    MAX_PRICE_DRIFT_PCT: float = float(os.getenv("MAX_PRICE_DRIFT_PCT", "25.0"))
    CONVERGENCE_WINDOW_SEC: int = int(os.getenv("CONVERGENCE_WINDOW_SEC", "900"))

    # Top 100 Smart Money Filter
    MIN_WIN_RATE_PCT: float = 75.0
    MIN_AVG_PEAK_GAIN_PCT: float = 500.0

    # Aturan Khusus: Koin Belum Runner Puncak (< $1.2M MCap) & Akumulasi Awal (1-2 Whale)
    PRE_PEAK_ONLY: bool = True
    MAX_RUNNER_MCAP_USD: float = float(os.getenv("MAX_RUNNER_MCAP_USD", "1200000.0"))
    MAX_SMART_MONEY_INFLOW: int = int(os.getenv("MAX_SMART_MONEY_INFLOW", "2"))

    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/tmp/smart_money_ledger.db" if os.getenv("VERCEL") else "smart_money_ledger.db")

    @property
    def has_telegram(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)

config = FomoConfig()
