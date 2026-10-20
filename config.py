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
    ENABLE_INSIDER_RADAR: bool = True             # Mengaktifkan deteksi insider/KOL awal
    MIN_INSIDER_BUY_USD: float = float(os.getenv("MIN_INSIDER_BUY_USD", "500.0")) # Min $1,000 (kasus Zypher $1,400)
    MIN_INSIDER_MCAP_USD: float = float(os.getenv("MIN_INSIDER_MCAP_USD", "25000.0")) # Min MC $25k (hindari bot spam)
    MAX_INSIDER_MCAP_USD: float = float(os.getenv("MAX_INSIDER_MCAP_USD", "250000.0"))# Maks MC $250k (Sweet spot insider)

    # On-Chain Safety Guard (Anti-Rug & Anti-Honeypot via Solana RPC)
    ENFORCE_ONCHAIN_SAFETY: bool = True           # Cek mintAuthority == null & freezeAuthority == null
    MIN_LIQUIDITY_USD: float = float(os.getenv("MIN_LIQUIDITY_USD", "8000.0"))        # Minimal likuiditas pool $8k USD
    SOLANA_RPC_URL: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

    # =========================================================================
    # 4. SISTEM & PERSISTENSI DATABASE
    # =========================================================================
    DATABASE_PATH: str = os.getenv(
        "DATABASE_PATH",
        "/tmp/smart_money_ledger.db" if os.getenv("VERCEL") else "smart_money_ledger.db"
    )

    @property
    def has_telegram(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)


config = FomoConfig()
