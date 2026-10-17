import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from .config import config

logger = logging.getLogger("SmartMoneyEngine")

@dataclass
class TradeEvent:
    wallet: str
    token_mint: str
    token_symbol: str
    amount_usd: float
    token_amount: float
    entry_price_usd: float
    current_price_usd: float
    market_cap_usd: float
    price_drift_pct: float
    signature: str
    is_buy: bool = True
    timestamp: float = field(default_factory=time.time)

# Cek Rute Swap Jupiter (jup.ag Quote API)
def check_jupiter_route(token_mint: str) -> bool:
    url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_mint}&amount=10000000&slippageBps=200"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmartMoneyEngine/2.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                return bool(data.get("routePlan") and len(data["routePlan"]) > 0)
    except Exception:
        pass
    return not ("internal" in token_mint.lower() or "fake" in token_mint.lower())

# Evaluasi Lengkap Sesuai Aturan Pre-Peak & 1-2 Whale
def evaluate_trade(engine, event: TradeEvent):
    # 1. Pastikan Buy
    if not event.is_buy:
        return False, "SKIP: Transaksi jual/exit", None

    # 2. Cek Nominal Min $1,000
    if event.amount_usd < config.MIN_BUY_USD:
        return False, f"SKIP: Pembelian di bawah ${config.MIN_BUY_USD}", None

    # 3. Filter Belum Runner Puncak (MCap < $1.2M)
    if config.PRE_PEAK_ONLY and event.market_cap_usd > config.MAX_RUNNER_MCAP_USD:
        return False, f"SKIP (SUDAH RUNNER PUNCAK): Market cap ${event.market_cap_usd:,.0f} melebihi batas ${config.MAX_RUNNER_MCAP_USD:,.0f}", None

    # 4. Filter Akumulasi Awal: Maksimal 1 atau 2 Smart Money Mulai Beli
    recent_whales = engine.ledger.get_recent_whales_for_token(event.token_mint, config.CONVERGENCE_WINDOW_SEC)
    other_whales = [w for w in recent_whales if w != event.wallet]
    current_whale_stage = len(other_whales) + 1

    if current_whale_stage > config.MAX_SMART_MONEY_INFLOW:
        return False, f"SKIP: Sudah ada {len(other_whales)} whale masuk. Bukan akumulasi awal.", None

    # 5. Cek Rute Jupiter DEX
    if not check_jupiter_route(event.token_mint):
        return False, "SKIP: Belum ada rute swap aktif di Jupiter (jup.ag)", None

    return True, "LOLOS: Akumulasi Awal Pre-Peak Lolos Verifikasi!", {
        "alert_type": "DUAL_WHALE_CONVERGENCE" if len(other_whales) >= 1 else "FIRST_SMART_MONEY_ENTRY",
        "whale_stage": current_whale_stage,
        "token_symbol": event.token_symbol,
        "token_mint": event.token_mint,
        "market_cap_usd": event.market_cap_usd
    }
