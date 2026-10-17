import os
from dataclasses import dataclass

@dataclass
class FomoConfig:
    # Telegram Credentials
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Jupiter & Trading Filters
    JUPITER_ONLY: bool = True                     # Token WAJIB memiliki rute swap aktif di Jupiter (jup.ag)
    MIN_BUY_USD: float = float(os.getenv("MIN_BUY_USD", "1000.0"))               # Min pembelian pertama ($1,000 USD)
    MAX_PRICE_DRIFT_PCT: float = float(os.getenv("MAX_PRICE_DRIFT_PCT", "25.0")) # Toleransi kenaikan harga maks +25%
    CONVERGENCE_WINDOW_SEC: int = int(os.getenv("CONVERGENCE_WINDOW_SEC", "900"))# Jendela konvergensi 15 menit

    # Top 100 Smart Money Filter
    MIN_WIN_RATE_PCT: float = 75.0                # Win-Rate >= 75% ATAU
    MIN_AVG_PEAK_GAIN_PCT: float = 500.0          # Rata-rata kenaikan > +500%

    # Aturan Khusus "Koin Belum Runner Puncak" & "1-2 Smart Money Beli"
    PRE_PEAK_ONLY: bool = True                    # Wajib koin belum runner puncak
    MAX_RUNNER_MCAP_USD: float = float(os.getenv("MAX_RUNNER_MCAP_USD", "1200000.0")) # Market cap maks $1.2M USD
    MAX_SMART_MONEY_INFLOW: int = int(os.getenv("MAX_SMART_MONEY_INFLOW", "2"))        # Maksimal whale tahap awal (1 atau 2 whale)

    # General Options
    ENABLE_EXIT_ALERTS: bool = True               # Alert saat smart money take-profit/menjual
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/tmp/smart_money_ledger.db" if os.getenv("VERCEL") else "smart_money_ledger.db")

config = FomoConfig()
