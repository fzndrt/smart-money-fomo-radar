"""
config.py - Konfigurasi Terpadu Smart Money FOMO, Insider Alpha & Corridor Sniper V2.
Kompatibel dengan Vercel Serverless Functions, Docker/VPS, dan Local Environment.
"""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class FomoConfig:
    # =========================================================================
    # 1. KREDENSIAL TELEGRAM & KEAMANAN CRON (Vercel Environment Variables)
    # =========================================================================
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    CRON_SECRET: str = os.getenv("CRON_SECRET", "").strip()
    DEBUG: bool = os.getenv("DEBUG", "false").strip().lower() in ("true", "1", "yes")

    # =========================================================================
    # 2. MODUL INTI: TOP 100 SMART MONEY & JUPITER V1 LITE ROUTING
    # =========================================================================
    JUPITER_ONLY: bool = True
    MIN_BUY_USD: float = float(os.getenv("MIN_BUY_USD", "1000.0"))
    MAX_PRICE_DRIFT_PCT: float = float(os.getenv("MAX_PRICE_DRIFT_PCT", "25.0"))
    CONVERGENCE_WINDOW_SEC: int = int(os.getenv("CONVERGENCE_WINDOW_SEC", "900"))

    MIN_WIN_RATE_PCT: float = 75.0
    MIN_AVG_PEAK_GAIN_PCT: float = 500.0

    PRE_PEAK_ONLY: bool = True
    MAX_RUNNER_MCAP_USD: float = float(os.getenv("MAX_RUNNER_MCAP_USD", "2000000.0"))
    MAX_SMART_MONEY_INFLOW: int = int(os.getenv("MAX_SMART_MONEY_INFLOW", "3"))
    ENABLE_REBUY_ALERTS: bool = False

    # =========================================================================
    # 3. MODUL INSIDER & EARLY ALPHA RADAR (@ZypherHQ Profile)
    # =========================================================================
    ENABLE_INSIDER_RADAR: bool = True
    MIN_INSIDER_BUY_USD: float = float(os.getenv("MIN_INSIDER_BUY_USD", "500.0"))
    MIN_INSIDER_MCAP_USD: float = float(os.getenv("MIN_INSIDER_MCAP_USD", "25000.0"))
    MAX_INSIDER_MCAP_USD: float = float(os.getenv("MAX_INSIDER_MCAP_USD", "250000.0"))
    REQUIRE_INSIDER_NEW_WALLET: bool = True
    MAX_INSIDER_PREVIOUS_BUYS: int = 1
    MAX_INSIDER_SUPPLY_PCT: float = 3.5
    REQUIRE_INSIDER_ZERO_SELL: bool = True

    # =========================================================================
    # 4. MODUL CORRIDOR SNIPER V2 (DELAY PROJECTION & ROUND-TRIP EXECUTION)
    # =========================================================================
    PIPELINE_REACTION_DELAY_SEC: float = float(os.getenv("PIPELINE_REACTION_DELAY_SEC", "22.0"))
    MAX_ROUNDTRIP_LOSS_PCT: float = float(os.getenv("MAX_ROUNDTRIP_LOSS_PCT", "5.5"))
    MIN_NET_PROFIT_AFTER_DELAY_PCT: float = float(os.getenv("MIN_NET_PROFIT_AFTER_DELAY_PCT", "18.0"))
    BATCH_CANDIDATE_LIMIT: int = int(os.getenv("BATCH_CANDIDATE_LIMIT", "50"))
    ALERT_COOLDOWN_SEC: int = int(os.getenv("ALERT_COOLDOWN_SEC", "2700"))  # 45 menit per lorong

    # On-Chain Safety Guard via Solana RPC
    ENFORCE_ONCHAIN_SAFETY: bool = True
    MIN_LIQUIDITY_USD: float = float(os.getenv("MIN_LIQUIDITY_USD", "8000.0"))
    SOLANA_RPC_URL: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

    # =========================================================================
    # 5. SISTEM & PERSISTENSI DATABASE
    # =========================================================================
    ONLY_SMART_MONEY_AND_INSIDER_ALERTS: bool = True

    DATABASE_PATH: str = os.getenv(
        "DATABASE_PATH",
        "/tmp/smart_money_ledger.db" if os.getenv("VERCEL") else "smart_money_ledger.db"
    )

    @property
    def has_telegram(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)


config = FomoConfig()

# Expose atribut tingkat modul untuk kompatibilitas penuh lintas runner
ONLY_SMART_MONEY_AND_INSIDER_ALERTS = config.ONLY_SMART_MONEY_AND_INSIDER_ALERTS
CONVERGENCE_WINDOW_SEC = config.CONVERGENCE_WINDOW_SEC
TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = config.TELEGRAM_CHAT_ID
CRON_SECRET = config.CRON_SECRET
DEBUG = config.DEBUG
JUPITER_ONLY = config.JUPITER_ONLY
MIN_BUY_USD = config.MIN_BUY_USD
MAX_PRICE_DRIFT_PCT = config.MAX_PRICE_DRIFT_PCT
DATABASE_PATH = config.DATABASE_PATH
