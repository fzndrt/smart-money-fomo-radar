"""
config.py - Konfigurasi Terpadu Smart Money FOMO & Insider Alpha Radar.
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
    # 1. KREDENSIAL TELEGRAM (Wajib diisi di Vercel Environment Variables)
    # =========================================================================
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # =========================================================================
    # 2. MODUL ASLI: TOP 100 SMART MONEY & JUPITER FILTER (TETAP 100% UTUH)
    # =========================================================================
    JUPITER_ONLY: bool = True                     # Token WAJIB memiliki rute swap di Jupiter (jup.ag)
    MIN_BUY_USD: float = float(os.getenv("MIN_BUY_USD", "1000.0"))               # Min pembelian ($1,000 USD)
    MAX_PRICE_DRIFT_PCT: float = float(os.getenv("MAX_PRICE_DRIFT_PCT", "25.0")) # Toleransi kenaikan harga maks +25%
    CONVERGENCE_WINDOW_SEC: int = int(os.getenv("CONVERGENCE_WINDOW_SEC", "900"))# Jendela konvergensi 15 menit

    # Ambang batas kualifikasi Smart Money Top 100
    MIN_WIN_RATE_PCT: float = 75.0                # Win-Rate >= 75% ATAU
    MIN_AVG_PEAK_GAIN_PCT: float = 500.0          # Rata-rata lonjakan puncak > +500%

    # Aturan Koin Belum Runner Puncak & Akumulasi Awal
    PRE_PEAK_ONLY: bool = True                    # Wajib koin belum runner puncak
    MAX_RUNNER_MCAP_USD: float = float(os.getenv("MAX_RUNNER_MCAP_USD", "2000000.0")) # Maks $2.0M USD
    MAX_SMART_MONEY_INFLOW: int = int(os.getenv("MAX_SMART_MONEY_INFLOW", "3"))        # Maksimal 1 - 3 whale tahap awal
    ENABLE_REBUY_ALERTS: bool = False             # Lewati DCA/Rebuy (fokus first-time entry)

    # =========================================================================
    # 3. MODUL TAMBAHAN BARU: INSIDER & EARLY ALPHA RADAR (SEPERTI @ZypherHQ)
    # =========================================================================
    ENABLE_INSIDER_RADAR: bool = True                   # Mengaktifkan deteksi insider/KOL awal
    MIN_INSIDER_BUY_USD: float = 500.0                  # Sweet spot beli $500 USD (~3 SOL)
    MIN_INSIDER_MCAP_USD: float = 25000.0               # Minimal MC $25k (hindari bot spam)
    MAX_INSIDER_MCAP_USD: float = 250000.0              # Maksimal MC $250k (Sweet spot $VEIL di $64k)
    REQUIRE_INSIDER_NEW_WALLET: bool = True             # Wajib Fresh New Wallet
    MAX_INSIDER_PREVIOUS_BUYS: int = 1                  # Maks 1 riwayat token sebelumnya
    MAX_INSIDER_SUPPLY_PCT: float = 3.5                 # Maksimal 3.5% suplai (distribusi terpecah rapi, anti dev monopoli)
    REQUIRE_INSIDER_ZERO_SELL: bool = True              # Wajib Zero Sell di fase awal akumulasi

    # On-Chain Safety Guard (Anti-Rug & Anti-Honeypot via Solana RPC)
    ENFORCE_ONCHAIN_SAFETY: bool = True           # Cek mintAuthority == null & freezeAuthority == null
    MIN_LIQUIDITY_USD: float = float(os.getenv("MIN_LIQUIDITY_USD", "8000.0"))        # Minimal likuiditas pool $8k USD
    SOLANA_RPC_URL: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

    # =========================================================================
    # 4. SISTEM & PERSISTENSI DATABASE
    # =========================================================================
    # Mode Eksklusif Telegram: HANYA Sinyal Smart Money & Insider (Mute Koin Umum/Dump Trap)
    ONLY_SMART_MONEY_AND_INSIDER_ALERTS: bool = True

    DATABASE_PATH: str = os.getenv(
        "DATABASE_PATH",
        "/tmp/smart_money_ledger.db" if os.getenv("VERCEL") else "smart_money_ledger.db"
    )

    @property
    def has_telegram(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)


config = FomoConfig()

# Expose atribut tingkat modul untuk kompatibilitas kebal galat
ONLY_SMART_MONEY_AND_INSIDER_ALERTS = config.ONLY_SMART_MONEY_AND_INSIDER_ALERTS
CONVERGENCE_WINDOW_SEC = config.CONVERGENCE_WINDOW_SEC
TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = config.TELEGRAM_CHAT_ID
JUPITER_ONLY = config.JUPITER_ONLY
MIN_BUY_USD = config.MIN_BUY_USD
MAX_PRICE_DRIFT_PCT = config.MAX_PRICE_DRIFT_PCT
DATABASE_PATH = config.DATABASE_PATH
