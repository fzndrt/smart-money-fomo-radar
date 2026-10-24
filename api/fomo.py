import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import concurrent.futures
from http.server import BaseHTTPRequestHandler

# Pastikan direktori root terbaca oleh sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

print("[VERCEL-INIT] 🚀 Smart Money & Corridor-Surfing Anti-Scam Engine initializing...", flush=True)

try:
    try:
        from config import config
        from engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS, INSIDER_WALLETS
    except ImportError:
        from smart_money_fomo.config import config
        from smart_money_fomo.engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS, INSIDER_WALLETS

    engine = SmartMoneyEngine(config)
    print(
        f"[VERCEL-INIT] ✅ Engine loaded: {len(TOP_100_WALLETS)} Whales | "
        f"{len(INSIDER_WALLETS)} Insiders | Telegram: {getattr(config, 'has_telegram', False)}",
        flush=True
    )
except Exception as init_err:
    print(f"[VERCEL-INIT] ⚠️ Root import notice ({init_err}), continuing in standalone mode.", flush=True)
    engine = None
    config = None

RECENT_ALERTED_MINTS = set()

# Estimasi friksi transaksi pulang-pergi di Solana DEX (swap fee + slippage + priority tip)
ESTIMATED_ROUNDTRIP_FEE_PCT = 4.5


def send_telegram_direct(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Kirim pesan langsung ke Telegram Bot API."""
    if not token or not chat_id:
        return False, "Token atau Chat ID belum diset di Vercel Environment Variables"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=7.0) as resp:
            return True, "Pesan berhasil terkirim ke Telegram!"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        return False, f"Telegram API HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, str(e)


def fetch_json(url: str, timeout: float = 3.2):
    """Utility fetch JSON cepat dengan User-Agent agar tidak diblokir Cloudflare/DEX API."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def fetch_jupiter_quote(mint: str, timeout: float = 3.0):
    """Cek rute swap & price impact nyata di Jupiter V1 Lite API (dengan fallback legacy)."""
    primary_url = (
        f"https://lite-api.jup.ag/swap/v1/quote"
        f"?inputMint=So11111111111111111111111111111111111111112"
        f"&outputMint={mint}&amount=10000000&slippageBps=200"
    )
    data = fetch_json(primary_url, timeout=timeout)
    if isinstance(data, dict) and data.get("routePlan") is not None:
        return data
    fallback_url = (
        f"https://quote-api.jup.ag/v6/quote"
        f"?inputMint=So11111111111111111111111111111111111111112"
        f"&outputMint={mint}&amount=10000000&slippageBps=200"
    )
    return fetch_json(fallback_url, timeout=2.0)


# ==============================================================================
# IDENTIFIKASI KOLAM POOL (AMM / RAYDIUM / METEORA / ORCA / BURN ADDRESSES)
# ==============================================================================
KNOWN_POOL_IDENTIFIERS = {
    "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1",
    "5q544fkrfoe6tseb7s8em7fcxks7dxdump246n4gkav",
    "675kpx9mhtjs2zt1qfr1nyhuzelxfqm9h24wfsut1mp8",
    "675kqpx985w3h236f568mhnf91f3v9g519w5g1395561",
    "cammczo5yl8w4vff8kvrk22ggusp5vtaw7grrkgrwqk",
    "cpmm0o8l3f4nbtegckvnunggl7h1zpdthkxqb5qkpp1c",
    "whirlbmiicvdio4qvufm5kag6ct8vpyzyzfgff3uctycc",
    "lbuzkhrxpf3xupbcjp4yztkglccjzhstdm9yuvapwxo",
    "eo7wjkq67rjjqszxs6z3ykapzy3emj6xy8x5eqvn5uab",
    "teqzcblr8dxhgntzru6jbj8gnzub6bxdrfh8h8a1wrc",
    "11111111111111111111111111111111",
    "1nc1nerator11111111111111111111111111111111",
    "deaddeaddeaddeaddeaddeaddeaddeaddeaddeaddead"
}


# ==============================================================================
# MODUL 1: PETA LORONG TERBANG AMAN (CORRIDOR SURFING) VS ZONA MERAH PLAFON
# Memblokir area antrean jual bandar ($100k, $200k, $450k-$500k, $800k+)
# ==============================================================================
def classify_entry_corridor(mc: float) -> tuple[bool, str, dict]:
    if mc < 25_000.0:
        return False, f"MC terlalu dini/tipis (${mc:,.0f} < $25k) - Rawan rug instan", {}

    # ZONA MERAH 1: Tembok Take-Profit Sniper Kelulusan ($78k - $128k)
    if 78_000.0 < mc <= 128_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $100K (${mc:,.0f}): Rawan bantingan sniper kelulusan. Tunggu tembus > $128k!",
            {}
        )

    # ZONA MERAH 2: Tembok Kematian Dev Modal Kecil ($168k - $228k)
    if 168_000.0 < mc <= 228_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $200K (${mc:,.0f}): Area favorit dev receh dump. Tunggu konfirmasi > $228k!",
            {}
        )

    # ZONA MERAH 3: Mendekati / Di Dalam Tembok Psikologis $420k - $525k
    if 315_000.0 < mc <= 525_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $450K-$500K (${mc:,.0f}): Ruang naik sempit, rawan front-run jual early whale!",
            {}
        )

    # ZONA MERAH 4: Area Pucuk / Distribusi Bandar Menuju $1M (> $650k)
    if mc > 650_000.0:
        return (
            False,
            f"ZONA PUCUK DISTRIBUSI (${mc:,.0f} > $650k): Terlalu dekat tembok jual $800k-$1M!",
            {}
        )

    # LORONG 0: Early Ignition ($25k - $78k) -> Front-run sebelum tembok $100k
    if 25_000.0 <= mc <= 78_000.0:
        tp_mc = min(88_000.0, mc * 1.42)
        return True, "LORONG 0 (Early Ignition < $78k)", {
            "corridor_id": 0,
            "corridor_name": "Lorong 0: Early Ignition (Sebelum Tembok $100k)",
            "min_liq": 10_500.0,
            "min_turnover_h1": 0.65,
            "min_cum_tx": 160,
            "min_avg_ticket": 20.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 15.0
        }

    # LORONG 1: Post-$100k Breakout ($128k - $168k) -> Front-run sebelum tembok $200k
    if 128_000.0 < mc <= 168_000.0:
        tp_mc = min(190_000.0, max(mc * 1.28, 182_000.0))
        return True, "LORONG 1 (Post-$100k Breakout)", {
            "corridor_id": 1,
            "corridor_name": "Lorong 1: Breakout Tembok $100k -> Menuju $190k",
            "min_liq": 18_000.0,
            "min_turnover_h1": 0.95,
            "min_cum_tx": 420,
            "min_avg_ticket": 25.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 14.0
        }

    # LORONG 2 (GOLDEN CORRIDOR): Lolos Kuburan $200k ($228k - $315k) -> Ruang Kosong ke $410k
    if 228_000.0 < mc <= 315_000.0:
        tp_mc = min(415_000.0, mc * 1.46)
        return True, "LORONG 2 (Golden Wave-2 Corridor)", {
            "corridor_id": 2,
            "corridor_name": "Lorong 2: Lolos Ujian $200k -> Ruang Terbang ke $400k+",
            "min_liq": 26_000.0,
            "min_turnover_h1": 1.30,
            "min_cum_tx": 900,
            "min_avg_ticket": 32.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 15.0
        }

    # LORONG 3 (WHALE CORRIDOR): Jebol Tembok $500k ($525k - $650k) -> Magnet $1M (Keluar di $800k)
    tp_mc = min(825_000.0, mc * 1.38)
    return True, "LORONG 3 (Whale Breakout > $525k)", {
        "corridor_id": 3,
        "corridor_name": "Lorong 3: Jebol Setengah Juta -> Front-Run $800k",
        "min_liq": 48_000.0,
        "min_turnover_h1": 1.20,
        "min_cum_tx": 1800,
        "min_avg_ticket": 48.0,
        "target_tp_mc": tp_mc,
        "cut_loss_pct": 14.0
    }


# ==============================================================================
# MODUL 2: EVALUASI MOMENTUM NAFAS BARU & PENANGKAL BOT MANIPULASI DEV
# ==============================================================================
def evaluate_market_candidate(best_pair: dict, pair_count: int = 1, boost_amount: int = 0) -> tuple[bool, str, dict]:
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)

    # 1. Cek Posisi Lorong Aman vs Zona Merah Plafon
    corridor_ok, corridor_msg, c_cfg = classify_entry_corridor(mc)
    if not corridor_ok:
        return False, corridor_msg, {}

    min_liq = c_cfg["min_liq"]
    if liq < min_liq:
        return False, f"Tangki likuiditas kurang untuk {c_cfg['corridor_name']} (${liq:,.0f} < ${min_liq:,.0f})", {}

    liq_mc_ratio = (liq / max(1.0, mc)) * 100.0
    min_liq_ratio = 8.5 if c_cfg["corridor_id"] == 3 else 11.5
    if liq_mc_ratio < min_liq_ratio:
        return False, f"Kolam LP terlalu tipis terhadap MC ({liq_mc_ratio:.1f}% < {min_liq_ratio}%)", {}

    # 2. Usia Pair: Lolos Jito Bundle Block-0 (>= 3.5m) & Bukan Zombie Tua (<= 16 jam)
    pair_created_at_ms = best_pair.get("pairCreatedAt")
    now_ms = time.time() * 1000.0
    if pair_created_at_ms:
        age_minutes = (now_ms - float(pair_created_at_ms)) / 60000.0
        if age_minutes < 3.5:
            return False, f"Terlalu baru ({age_minutes:.1f}m < 3.5m): Fase rawan MEV bundle block-0", {}
        if age_minutes > 960.0:
            return False, f"Koin lama ({age_minutes/60.0:.1f} jam > 16j): Rawan jebakan re-pump koin mati", {}
    else:
        age_minutes = 12.0

    # 3. Temperatur Candle Anti-Pucuk (+6.0% <= M5 <= +45.0%) & Tren H1 Hijau
    price_change = best_pair.get("priceChange") or {}
    p_m5 = float(price_change.get("m5", 0.0) or 0.0)
    p_h1 = float(price_change.get("h1", 0.0) or 0.0)
    p_h6 = float(price_change.get("h6", 0.0) or 0.0)

    if p_m5 < 6.0:
        return False, f"Momentum 5m belum menginjak gas ({p_m5:+.1f}% < +6.0%) - Tunggu dorongan naik", {}
    if p_m5 > 45.0:
        return False, f"ANTI-PUCUK: Candle 5m sudah kepanjangan (+{p_m5:.1f}% > +45%). Rawan koreksi sesaat!", {}
    if p_h1 <= 0.0 or p_h6 < -12.0:
        return False, f"Tren makro sedang distribusi/merah (H1: {p_h1:+.1f}%, H6: {p_h6:+.1f}%)", {}

    # 4. Data Orderflow M5, H1, dan Kumulatif H24
    txns = best_pair.get("txns") or {}
    m5_tx = txns.get("m5") or {}
    h1_tx = txns.get("h1") or {}
    h24_tx = txns.get("h24") or {}

    buys_m5, sells_m5 = int(m5_tx.get("buys", 0)), int(m5_tx.get("sells", 0))
    buys_h1, sells_h1 = int(h1_tx.get("buys", 0)), int(h1_tx.get("sells", 0))
    cum_tx_24h = int(h24_tx.get("buys", 0)) + int(h24_tx.get("sells", 0))
    total_m5 = buys_m5 + sells_m5

    volumes = best_pair.get("volume") or {}
    vol_m5 = float(volumes.get("m5", 0.0) or 0.0)
    vol_h1 = float(volumes.get("h1", 0.0) or 0.0)

    # 5. PENANGKAL DEV LICIK #1: Kepadatan Transaksi Seumur Hidup (Anti Bundled Ghost Pump)
    if cum_tx_24h < c_cfg["min_cum_tx"]:
        return (
            False,
            f"Jebakan Ghost-Pump Dev: MC ${mc:,.0f} tapi total transaksi baru {cum_tx_24h} (< {c_cfg['min_cum_tx']} tx)",
            {}
        )

    # 6. Dominasi Pembeli Nyata & Seller Exhaustion (M5 vs H1)
    if total_m5 < 25 or sells_m5 >= buys_m5:
        return False, f"Partisipasi pembeli lemah ({buys_m5}B / {sells_m5}S)", {}

    buy_sell_ratio_m5 = buys_m5 / max(1, sells_m5)
    buy_sell_ratio_h1 = buys_h1 / max(1, sells_h1) if (buys_h1 + sells_h1) > 0 else 1.5
    if buy_sell_ratio_m5 < 2.0:
        return False, f"Rasio Buy/Sell kurang dominan ({buy_sell_ratio_m5:.2f}x < 2.0x)", {}

    if age_minutes >= 20.0 and buy_sell_ratio_m5 < (buy_sell_ratio_h1 * 0.90):
        return False, f"Momentum beli melambat (Rasio M5 {buy_sell_ratio_m5:.2f}x < H1 {buy_sell_ratio_h1:.2f}x)", {}

    # Net Order Imbalance (CVD): saat Buy = 2.0x Sell (66.7% Buy Share), CVD Delta = +33.3%
    cvd_ratio = round(((buys_m5 - sells_m5) / max(1, total_m5)) * 100.0, 1)
    if cvd_ratio < 33.3:
        return False, f"CVD Net Inflow kurang kuat (+{cvd_ratio}% < +33.3%)", {}

    # 7. PENANGKAL DEV LICIK #2: Anti-Siphon Bot (Banyak Klik Buy Kecil vs Sedikit Sell Raksasa)
    if buy_sell_ratio_m5 >= 2.8 and vol_m5 >= 5000.0 and p_m5 < 8.5:
        return (
            False,
            f"Terdeteksi Siphon Bot Dev: Rasio klik {buy_sell_ratio_m5:.1f}x tapi harga tertahan (+{p_m5:.1f}%) -> Sell paus diam-diam!",
            {}
        )

    # 8. PENANGKAL DEV LICIK #3: Anti-Wash-Breakout Churn & Perputaran Suplai (Cost-Basis Reset)
    vol_to_liq_m5 = vol_m5 / max(1.0, liq)
    if vol_to_liq_m5 > 2.4:
        return (
            False,
            f"Terdeteksi Wash-Trading Bot: Volume 5m (${vol_m5:,.0f}) > 2.4x isi kolam LP (${liq:,.0f}) -> Uang diputar di tempat!",
            {}
        )

    turnover_h1 = vol_h1 / max(1.0, mc)
    if age_minutes >= 25.0 and turnover_h1 < c_cfg["min_turnover_h1"]:
        return (
            False,
            f"Suplai bawah belum ganti tangan: Vol 1h/MC baru {turnover_h1:.2f}x (< {c_cfg['min_turnover_h1']}x)",
            {}
        )

    # 9. Akselerasi Uang Segar (Fresh Money Surge: laju M5 naik ATAU intensitas Vol 5m >= 10% MC)
    effective_h1_mins = min(60.0, max(6.0, age_minutes))
    vol_rate_m5 = vol_m5 / 5.0
    vol_rate_h1 = vol_h1 / effective_h1_mins
    accel_ratio = vol_rate_m5 / max(1.0, vol_rate_h1)
    if age_minutes >= 15.0 and accel_ratio < 1.05 and (vol_m5 / max(1.0, mc)) < 0.10:
        return (
            False,
            f"Volume sedang mendingin (Akselerasi {accel_ratio:.2f}x < 1.05x & intensitas M5 rendah)",
            {}
        )

    # 10. Ukuran Tiket & Komitmen Tangki Bahan Bakar
    avg_ticket = vol_m5 / max(1, total_m5)
    has_commitment = (boost_amount >= 50) or (pair_count >= 2) or bool((best_pair.get("info") or {}).get("socials"))
    effective_min_ticket = c_cfg["min_avg_ticket"] * (0.85 if has_commitment else 1.0)
    if vol_m5 < 2500.0 or avg_ticket < effective_min_ticket:
        return (
            False,
            f"Order rata-rata terlalu receh/micro-bot (${avg_ticket:.1f} < ${effective_min_ticket:.1f})",
            {}
        )

    gross_upside_pct = ((c_cfg["target_tp_mc"] - mc) / mc) * 100.0
    net_upside_pct = max(12.0, round(gross_upside_pct - ESTIMATED_ROUNDTRIP_FEE_PCT, 1))

    score = min(
        99,
        int(
            35.0
            + min(25.0, (buy_sell_ratio_m5 / 3.0) * 25.0)
            + min(20.0, (cvd_ratio / 80.0) * 20.0)
            + min(12.0, (accel_ratio / 2.0) * 12.0)
            + (8.0 if has_commitment else 3.0)
        )
    )
    if score < 76:
        return False, f"Skor konvergensi momentum kurang ({score}/100 < 76)", {}

    return True, "LOLOS CORRIDOR & ANTI-SPOOFING", {
        "score": score,
        "corridor_id": c_cfg["corridor_id"],
        "corridor_name": c_cfg["corridor_name"],
        "target_tp_mc": round(c_cfg["target_tp_mc"], 0),
        "gross_upside_pct": round(gross_upside_pct, 1),
        "net_upside_pct": net_upside_pct,
        "cut_loss_pct": c_cfg["cut_loss_pct"],
        "cvd_ratio": cvd_ratio,
        "buy_sell_ratio": round(buy_sell_ratio_m5, 2),
        "avg_ticket": round(avg_ticket, 1),
        "liq_mc_ratio": round(liq_mc_ratio, 1),
        "turnover_h1": round(turnover_h1, 2),
        "accel_ratio": round(accel_ratio, 2),
        "price_change_m5": round(p_m5, 1),
        "age_minutes": round(age_minutes, 1),
        "cum_tx_24h": cum_tx_24h,
        "boost_amount": boost_amount
    }


# ==============================================================================
# MODUL 3: PENANGKAL CABAL TOP-20 MICRO-SPLIT & FAKE UNLOCKED POOL
# ==============================================================================
def analyze_holders_and_lp_lock(rug_data: dict, pair_address: str = "") -> tuple[bool, str, dict]:
    if not isinstance(rug_data, dict):
        return False, "Audit RugCheck timeout (Fail-Closed Guard)", {}

    holders = rug_data.get("topHolders") or rug_data.get("holders") or []
    if not holders:
        return False, "Data Top Holders RugCheck belum tersedia (Fail-Closed)", {}

    markets = rug_data.get("markets") or []
    market_vaults = set()
    total_locked_usd = 0.0
    total_unlocked_usd = 0.0
    primary_lp_locked_pct = 100.0

    sorted_markets = sorted(
        markets,
        key=lambda m: float((m.get("lp") or {}).get("baseUSD") or 0.0) + float((m.get("lp") or {}).get("quoteUSD") or 0.0),
        reverse=True
    )
    for idx, m in enumerate(sorted_markets):
        if m.get("liquidityA"): market_vaults.add(str(m["liquidityA"]).lower())
        if m.get("liquidityB"): market_vaults.add(str(m["liquidityB"]).lower())
        if m.get("pubkey"): market_vaults.add(str(m["pubkey"]).lower())

        lp = m.get("lp") or {}
        pool_usd = float(lp.get("baseUSD") or 0.0) + float(lp.get("quoteUSD") or 0.0)
        locked_pct = lp.get("lpLockedPct")
        m_type = str(m.get("marketType") or "").lower()
        lp_mint = str(lp.get("lpMint") or "")

        if locked_pct is not None and pool_usd > 1000.0:
            pct_val = float(locked_pct)
            if 0.0 < pct_val <= 1.0 and float(lp.get("lpLockedUSD") or 0.0) > pool_usd * 0.5:
                pct_val *= 100.0
            if idx == 0:
                primary_lp_locked_pct = round(pct_val, 1)
                if ("raydium" in m_type or "pump" in m_type) and lp_mint != "11111111111111111111111111111111":
                    if pct_val < 85.0:
                        return False, f"Bahaya Rug LP: Pool utama hanya terkunci {pct_val:.1f}% (< 85%)", {}

            locked_u = float(lp.get("lpLockedUSD") or (pool_usd * (pct_val / 100.0)))
            total_locked_usd += locked_u
            total_unlocked_usd += max(0.0, pool_usd - locked_u)

    # Penangkal Dev Licik #4: Pool Kedua Palsu Tidak Dikunci
    if total_unlocked_usd > 15_000.0 and total_unlocked_usd > total_locked_usd:
        return (
            False,
            f"Jebakan Fake Secondary Pool: Likuiditas tak terkunci (${total_unlocked_usd:,.0f}) > terkunci (${total_locked_usd:,.0f})",
            {}
        )

    norm_pair = (pair_address or "").lower()
    pool_holdings = []
    pure_holdings = []

    for h in holders[:25]:
        addr = str(h.get("address") or "").lower()
        pct = float(h.get("pct") or 0.0)
        is_pool = (
            addr == norm_pair
            or addr in market_vaults
            or addr in KNOWN_POOL_IDENTIFIERS
            or "burn" in addr
            or "dead" in addr
            or (pct >= 18.0 and (addr.startswith("5q") or addr.startswith("67") or addr.startswith("ca")))
        )
        if is_pool:
            pool_holdings.append({"address": addr, "pct": pct})
        else:
            pure_holdings.append({"address": addr, "pct": pct, "insider": bool(h.get("insider"))})

    pure_holdings.sort(key=lambda x: x["pct"], reverse=True)
    max_single_pure = pure_holdings[0]["pct"] if pure_holdings else 0.0
    top10_pure_pct = sum(x["pct"] for x in pure_holdings[:10])
    pool_total_pct = sum(x["pct"] for x in pool_holdings)
    insider_count = sum(1 for x in pure_holdings[:15] if x.get("insider"))

    if insider_count >= 2:
        return False, f"Terdeteksi {insider_count} dompet Insider/Bundler aktif di Top 15", {}

    if max_single_pure > 4.6:
        return False, f"Paus Tunggal Bahaya: 1 pure holder menguasai {max_single_pure:.1f}% (> 4.6%)", {}

    if top10_pure_pct > 19.5:
        return False, f"Top 10 Pure Holders terlalu pekat ({top10_pure_pct:.1f}% > 19.5%)", {}

    # Deteksi Kluster Cabal Menengah (1.2% - 4.2%)
    mid_holders = [x["pct"] for x in pure_holdings if 1.2 <= x["pct"] <= 4.2]
    for p_ref in mid_holders:
        cluster = [p for p in mid_holders if abs(p - p_ref) <= 0.38]
        if len(cluster) >= 3 and sum(cluster) >= 6.2:
            return (
                False,
                f"Cabal Cluster Terdeteksi: Dev memecah {sum(cluster):.1f}% suplai ke {len(cluster)} dompet seragam",
                {}
            )

    # Deteksi Sindikat Micro-Split CEX (0.65% - 1.20% di Top 20)
    micro_holders = [x["pct"] for x in pure_holdings[:20] if 0.65 <= x["pct"] < 1.20]
    for p_ref in micro_holders:
        m_cluster = [p for p in micro_holders if abs(p - p_ref) <= 0.14]
        if len(m_cluster) >= 5:
            return (
                False,
                f"Micro-Split CEX Syndicate: {len(m_cluster)} dompet memegang persentase identik (~{p_ref:.2f}%)",
                {}
            )

    return True, "Distribusi Bersih & LP Aman", {
        "pure_top10": round(top10_pure_pct, 1),
        "max_single": round(max_single_pure, 1),
        "pool_pct": round(pool_total_pct, 1),
        "lp_locked_pct": primary_lp_locked_pct
    }


# ==============================================================================
# EKSEKUTOR PARALEL PER TOKEN (DEXSCREENER -> CORRIDOR -> PARALLEL AUDIT & JUP)
# ==============================================================================
def evaluate_single_mint(mint: str, config, boost_amount: int = 0) -> dict:
    dex_data = fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=3.0)
    if not dex_data or not dex_data.get("pairs"):
        return {"mint": mint, "token": mint[:8], "verdict": "SKIP", "reason": "Data pair DexScreener kosong"}

    sol_pairs = [p for p in dex_data.get("pairs", []) if p.get("chainId") == "solana"]
    if not sol_pairs:
        return {"mint": mint, "token": mint[:8], "verdict": "SKIP", "reason": "Bukan pair Solana"}

    pairs = sorted(
        sol_pairs,
        key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0.0),
        reverse=True
    )
    best_pair = pairs[0]
    active_pools_count = sum(1 for p in pairs if float((p.get("liquidity") or {}).get("usd") or 0.0) >= 4000.0)

    name = best_pair.get("baseToken", {}).get("name", "Unknown")
    symbol = best_pair.get("baseToken", {}).get("symbol", "SOL")
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)
    dex_id = best_pair.get("dexId", "raydium")
    pair_address = best_pair.get("pairAddress", "")

    txns_m5 = best_pair.get("txns", {}).get("m5", {})
    buys_m5 = int(txns_m5.get("buys", 0))
    sells_m5 = int(txns_m5.get("sells", 0))

    # TAHAP 1 (0.01ms In-Memory): Saring Lorong Aman, Anti-Pucuk & Anti-Bot Manipulasi
    is_candidate_ok, filter_reason, metrics = evaluate_market_candidate(
        best_pair, pair_count=active_pools_count, boost_amount=boost_amount
    )
    if not is_candidate_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": filter_reason
        }

    # TAHAP 2 (Paralel Cepat): Tembak RugCheck Report & Jupiter V1 Quote secara bersamaan!
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as sub_exec:
        f_rug = sub_exec.submit(fetch_json, f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", 3.5)
        f_jup = sub_exec.submit(fetch_jupiter_quote, mint, 3.0)
        rug_data = f_rug.result()
        jup_quote = f_jup.result()

    if not isinstance(rug_data, dict):
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": "Audit RugCheck timeout / belum siap (Fail-Closed: Lindungi modal)"
        }

    rug_score = int(rug_data.get("score", 0))
    if rug_score >= 300:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": f"RugCheck skor tinggi ({rug_score} >= 300)"
        }

    for r in (rug_data.get("risks") or []):
        r_name = str(r.get("name", "")).lower()
        r_level = str(r.get("level", "")).lower()
        if ("danger" in r_level or "high" in r_level) and any(
            k in r_name for k in ("liquidity", "creator", "cabal", "cluster", "sybil", "bundled", "dump", "freeze", "mint", "unlocked")
        ):
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": f"Risiko On-Chain RugCheck: {r.get('name')}"
            }

    # TAHAP 3: Verifikasi Top-20 Micro-Split Cabal & Penguncian LP
    holders_ok, holders_reason, holder_meta = analyze_holders_and_lp_lock(rug_data, pair_address)
    if not holders_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": holders_reason
        }

    # TAHAP 4: Verifikasi Rute Likuid & Price Impact di Jupiter V1
    if not isinstance(jup_quote, dict) or not jup_quote.get("routePlan"):
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": "Tidak memiliki rute swap likuid di Jupiter DEX (Anti-Trap)"
        }

    jup_impact = abs(float(jup_quote.get("priceImpactPct") or 0.0)) * 100.0 if abs(float(jup_quote.get("priceImpactPct") or 0.0)) < 1.0 else abs(float(jup_quote.get("priceImpactPct") or 0.0))
    if jup_impact > 4.5:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": f"Price impact Jupiter terlalu tinggi ({jup_impact:.2f}% > 4.5%)"
        }

    # TAHAP 5: LOLOS SEMUA LAPISAN -> KIRIM SINYAL LENGKAP TARGET TP & CUT-LOSS
    tp_mc = metrics["target_tp_mc"]
    net_gain = metrics["net_upside_pct"]
    cut_loss = metrics["cut_loss_pct"]

    alert_msg = (
        f"🎯 *[CORRIDOR SNIPER: MASUK LORONG AMAN & ANTI-PUCUK]*\n\n"
        f"🪙 *Token:* {name} (`${symbol}`)\n"
        f"📍 *Mint:* `{mint}`\n"
        f"🛤️ *Posisi:* `{metrics['corridor_name']}`\n"
        f"💰 *Entry Market Cap:* `${mc:,.0f}`\n"
        f"🎯 *Target Take-Profit Aman:* `${tp_mc:,.0f}` *(Est. Bersih +{net_gain}% setelah fee)*\n"
        f"🛑 *Disiplin Auto Cut-Loss:* `-{cut_loss}%` *(Wajib pasang di bot swap)*\n\n"
        f"⚡ *Mesin Nafas Baru:* 5m `+{metrics['price_change_m5']}%` | Akselerasi Vol `{metrics['accel_ratio']}x` | Turnover 1h `{metrics['turnover_h1']}x`\n"
        f"📊 *Orderflow M5:* `{buys_m5} Buy` vs `{sells_m5} Sell` (`{metrics['buy_sell_ratio']}x` | CVD `+{metrics['cvd_ratio']}%`)\n"
        f"💧 *Likuiditas:* `${liq:,.0f}` ({dex_id.title()} | LP Locked `{holder_meta['lp_locked_pct']}%`)\n"
        f"👥 *Pure Top 10:* `{holder_meta['pure_top10']}%` (Max Tunggal `{holder_meta['max_single']}%`)\n"
        f"🛡️ *Anti-Cheat Shield:* `LOLOS` (Bebas Siphon Bot, Wash-Churn, & Micro-Split Cabal)\n\n"
        f"🔗 *DexScreener:* [Lihat Chart](https://dexscreener.com/solana/{mint})\n"
        f"⚡ *Jupiter Swap:* [Eksekusi Cepat](https://jup.ag/swap/SOL-{mint})"
    )

    bot_tok = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")

    sent, detail = send_telegram_direct(bot_tok, chat_id, alert_msg)
    if sent:
        RECENT_ALERTED_MINTS.add(mint)
        print(f"[VERCEL-ALERT] 🚀 Sinyal {metrics['corridor_name']} terkirim untuk {name} (${symbol})!", flush=True)
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "ALERT_SENT",
            "corridor": metrics["corridor_name"],
            "entry_mc": mc,
            "target_tp_mc": tp_mc,
            "est_net_gain_pct": net_gain,
            "reason": f"Lolos {metrics['corridor_name']} -> Target TP ${tp_mc:,.0f} (+{net_gain}% net)"
        }
    return {
        "token": f"{name} (${symbol})",
        "mint": mint,
        "verdict": "ALERT_FAILED",
        "corridor": metrics["corridor_name"],
        "reason": detail
    }


def run_live_market_scan(engine, config) -> dict:
    """Pemindaian multi-thread berkecepatan tinggi (Discovery Paralel + Evaluasi Paralel)."""
    t0 = time.time()
    print("[VERCEL-SCAN] ⚡ Memulai Fast-Time Parallel Corridor Scan di Solana...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as feed_exec:
        f_prof = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-profiles/latest/v1", 3.0)
        f_boost = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-boosts/latest/v1", 3.0)
        profiles = f_prof.result()
        boosts = f_boost.result()

    ordered_mints = []
    boost_map = {}

    if isinstance(boosts, list):
        for b in boosts:
            if b.get("chainId") == "solana" and b.get("tokenAddress"):
                addr = b["tokenAddress"]
                amt = int(b.get("totalAmount") or b.get("amount") or 0)
                boost_map[addr] = max(boost_map.get(addr, 0), amt)
                ordered_mints.append(addr)

    if isinstance(profiles, list):
        for p in profiles:
            if p.get("chainId") == "solana" and p.get("tokenAddress"):
                ordered_mints.append(p["tokenAddress"])

    seen = set()
    candidate_mints = []
    for m in ordered_mints:
        if m not in seen and m not in RECENT_ALERTED_MINTS:
            seen.add(m)
            candidate_mints.append(m)
            if len(candidate_mints) >= 8:
                break

    evaluations = []
    alerts_sent = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_to_mint = {
            executor.submit(evaluate_single_mint, m, config, boost_map.get(m, 0)): m
            for m in candidate_mints
        }
        for future in concurrent.futures.as_completed(future_to_mint):
            m = future_to_mint[future]
            try:
                res = future.result()
                evaluations.append(res)
                if res.get("verdict") == "ALERT_SENT":
                    alerts_sent += 1
            except Exception as exc:
                evaluations.append({
                    "token": m[:8],
                    "mint": m,
                    "verdict": "ERROR",
                    "reason": f"Evaluasi gagal: {exc}"
                })

    elapsed_ms = int((time.time() - t0) * 1000)
    print(f"[VERCEL-SCAN] ✅ Scan paralel selesai dalam {elapsed_ms}ms: {len(candidate_mints)} dievaluasi, {alerts_sent} alert.", flush=True)
    return {
        "status": "scan_completed",
        "scan_duration_ms": elapsed_ms,
        "candidates_evaluated": len(candidate_mints),
        "alerts_dispatched": alerts_sent,
        "evaluations": evaluations
    }


class handler(BaseHTTPRequestHandler):
    """Vercel Python Serverless Handler — Fast-Time Corridor Surfing & Anti-Scam Engine."""

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        start_t = time.time()
        parsed_url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_url.query)
        action = query.get("action", [""])[0]

        is_cron = (
            query.get("cron", ["false"])[0].lower() in ("true", "1")
            or action == "cron"
            or bool(self.headers.get("x-vercel-cron-schedule"))
            or "vercel-cron" in self.headers.get("user-agent", "").lower()
        )

        if action == "leaderboard":
            wallets = []
            if engine and TOP_100_WALLETS:
                wallets = [
                    {
                        "rank": w.rank,
                        "name": w.name,
                        "address": w.address,
                        "win_rate": w.win_rate,
                        "avg_peak_gain": w.avg_peak_gain,
                        "pnl_30d_usd": w.pnl_30d_usd,
                        "tag": w.tag
                    }
                    for w in list(TOP_100_WALLETS.values())[:30]
                ]
            insiders = []
            if engine and INSIDER_WALLETS:
                insiders = [
                    {
                        "handle": iw.handle,
                        "alias": iw.alias,
                        "address": iw.address,
                        "category": iw.category,
                        "known_hits": iw.known_hits,
                        "typical_entry_mc": iw.typical_entry_mc
                    }
                    for iw in INSIDER_WALLETS.values()
                ]
            response_data = {
                "smart_money_count": len(TOP_100_WALLETS) if TOP_100_WALLETS else 0,
                "insiders_count": len(insiders),
                "verified_insiders": insiders,
                "top_smart_money": wallets
            }

        elif action in ("test_telegram", "test_tele"):
            bot_token = getattr(config, "TELEGRAM_BOT_TOKEN", None) or os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = getattr(config, "TELEGRAM_CHAT_ID", None) or os.getenv("TELEGRAM_CHAT_ID")
            if not bot_token or not chat_id:
                response_data = {
                    "test": "test_telegram",
                    "status": "NOT_CONFIGURED",
                    "message": "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum ada di Vercel Environment Variables.",
                    "has_token": bool(bot_token),
                    "has_chat_id": bool(chat_id)
                }
            else:
                test_msg = (
                    "🔔 *[CORRIDOR SNIPER & ANTI-SCAM RADAR: AKTIF]*\n\n"
                    "✅ Mesin Fast-Time Parallel terhubung ke Telegram!\n\n"
                    "Fitur Perlindungan Modal Aktif:\n"
                    "1. 🛤️ *Corridor Surfing:* Blokir Tembok Kematian ($100k, $200k, $500k) & masuk di Lorong Udara Kosong lengkap dengan Target TP otomatis.\n"
                    "2. 🚀 *Anti-Pucuk Impulse:* Hanya masuk di awal dorongan (`+6%` s/d `+45%` M5) dengan Akselerasi Volume.\n"
                    "3. 🛡️ *Anti-Dev Curang:* Penangkal Siphon Bot, Wash-Breakout Churn, Micro-Split Top-20 CEX Cabal, & Fake Unlocked LP.\n"
                    "4. ⚡ *Jupiter V1 Live Quote:* Verifikasi rute swap & price impact rendah."
                )
                ok, detail = send_telegram_direct(bot_token, chat_id, test_msg)
                response_data = {
                    "test": "test_telegram",
                    "status": "SUCCESS" if ok else "FAILED",
                    "telegram_sent": ok,
                    "diagnostic_detail": detail
                }

        elif action == "simulate":
            test_cases = [
                {
                    "name": "🐋 Whale #1 Pre-Peak Entry ($85k MC, Jupiter)",
                    "event": TradeEvent(
                        wallet="HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4ZkP",
                        token_mint="CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
                        token_symbol="GOAT",
                        amount_usd=3200.0,
                        token_amount=150000.0,
                        entry_price_usd=0.021,
                        current_price_usd=0.021,
                        market_cap_usd=85000.0,
                        price_drift_pct=2.4,
                        signature=f"demo-sig-{int(time.time()*1000)}"
                    )
                },
                {
                    "name": "🚫 Whale High MCap Skip (> $2.0M Runner Puncak)",
                    "event": TradeEvent(
                        wallet="HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4ZkP",
                        token_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                        token_symbol="USDC",
                        amount_usd=12000.0,
                        token_amount=12000.0,
                        entry_price_usd=1.0,
                        current_price_usd=1.0,
                        market_cap_usd=18500000.0,
                        price_drift_pct=1.2,
                        signature=f"demo-sig-{int(time.time()*1000)+3}"
                    )
                }
            ]
            results = []
            for tc in test_cases:
                el, reason, p = engine.evaluate_trade(tc["event"]) if engine else (False, "Engine not initialized", None)
                results.append({
                    "case": tc["name"],
                    "eligible": el,
                    "reason": reason,
                    "alert_type": p.get("alert_type") if p else None
                })
            response_data = {"simulation": "completed", "results": results}

        elif action == "scan" or is_cron:
            scan_summary = run_live_market_scan(engine, config)
            response_data = {
                "service": "Corridor Sniper & Smart Money Anti-Scam Radar",
                "status": "ready",
                "is_cron_call": is_cron,
                "timestamp": time.time(),
                "scan_result": scan_summary,
                "corridor_architecture": {
                    "corridor_0": "$25k - $78k -> TP <= $88k (Front-run tembok $100k)",
                    "deadzone_1_blocked": "$78k - $128k (Tembok Sniper $100k DIBLOKIR)",
                    "corridor_1": "$128k - $168k -> TP <= $190k (Front-run tembok $200k)",
                    "deadzone_2_blocked": "$168k - $228k (Kuburan Dev Receh $200k DIBLOKIR)",
                    "corridor_2_golden": "$228k - $315k -> TP <= $415k (Ruang Kosong Menuju $400k+)",
                    "deadzone_3_blocked": "$315k - $525k (Tembok $450k-$500k DIBLOKIR)",
                    "corridor_3_whale": "$525k - $650k -> TP <= $825k (Front-run tembok $1M)"
                },
                "anti_dev_cheat_shields": [
                    "Anti-Siphon Divergence (Deteksi 300 Buy kecil vs Sell paus tersembunyi)",
                    "Anti-Wash Churn (Blokir Volume 5m > 2.4x LP & Ghost-Pump minim transaksi)",
                    "Top-20 Micro-Split CEX Syndicate Guard (0.65%-4.2% uniform slice)",
                    "LP Lock Integrity & Fake Secondary Pool Detector + Jupiter V1 Price Impact <= 4.5%"
                ]
            }

        else:
            response_data = {
                "service": "Corridor Sniper & Smart Money Anti-Scam Radar",
                "status": "ready",
                "timestamp": time.time(),
                "endpoints": {
                    "scan": "/api/fomo?action=scan",
                    "test_telegram": "/api/fomo?action=test_telegram",
                    "simulate": "/api/fomo?action=simulate",
                    "leaderboard": "/api/fomo?action=leaderboard"
                }
            }

        elapsed_ms = int((time.time() - start_t) * 1000)
        print(f"[VERCEL-RES] 🟢 Finished GET {self.path} in {elapsed_ms}ms", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(response_data, indent=2).encode("utf-8"))

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_length)
            data = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception as err:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Invalid JSON payload: {err}"}).encode("utf-8"))
            return

        if not engine:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Engine initialization failure"}).encode("utf-8"))
            return

        try:
            event = TradeEvent(
                wallet=data.get("wallet", ""),
                token_mint=data.get("token_mint", ""),
                token_symbol=data.get("token_symbol", "UNKNOWN"),
                amount_usd=float(data.get("amount_usd", 0.0)),
                token_amount=float(data.get("token_amount", 0.0)),
                entry_price_usd=float(data.get("entry_price_usd", 0.0)),
                current_price_usd=float(data.get("current_price_usd", 0.0)),
                market_cap_usd=float(data.get("market_cap_usd", 0.0)),
                price_drift_pct=float(data.get("price_drift_pct", 0.0)),
                signature=data.get("signature", f"sig-{int(time.time()*1000)}"),
                timestamp=data.get("timestamp", time.time())
            )
            eligible, reason, payload = engine.evaluate_trade(event)
            alert_sent = False
            if eligible and payload and config and getattr(config, "has_telegram", False):
                alert_sent = engine.send_telegram_alert(payload)

            res = {
                "verdict": "ALERT" if eligible else "SKIP",
                "reason": reason,
                "telegram_sent": alert_sent,
                "payload": payload
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(res, indent=2).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Evaluation error: {str(e)}"}).encode("utf-8"))
