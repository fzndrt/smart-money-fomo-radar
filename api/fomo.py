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

print("[VERCEL-INIT] 🚀 Delay-Compensated Corridor Sniper & Round-Trip Anti-Scam Engine initializing...", flush=True)

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
ALERT_LEDGER_PATH = "/tmp/corridor_alerted_ledger.json"

# ==============================================================================
# KONSTANTA PRESISI EKSEKUSI, DELAY TELEGRAM, & PROTEKSI MODAL
# ==============================================================================
SOL_MINT = "So11111111111111111111111111111111111111112"
HARD_QUOTE_MINTS = {
    SOL_MINT,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",  # WBTC (Wormhole Official)
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",  # cbBTC (Coinbase Official)
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",  # USD1
}
TEST_BUY_LAMPORTS = 100_000_000          # Simulasi ukuran nyata 0.1 SOL (~$15) Beli -> Jual
PIPELINE_REACTION_DELAY_SEC = float(os.getenv("PIPELINE_REACTION_DELAY_SEC", getattr(config, "PIPELINE_REACTION_DELAY_SEC", 22.0)))
PRIORITY_TIP_BUFFER_PCT = 1.0            # Buffer priority fee / Jito tip saat eksekusi nyata
MAX_ROUNDTRIP_LOSS_PCT = float(os.getenv("MAX_ROUNDTRIP_LOSS_PCT", getattr(config, "MAX_ROUNDTRIP_LOSS_PCT", 5.5)))
MIN_NET_PROFIT_AFTER_DELAY_PCT = float(os.getenv("MIN_NET_PROFIT_AFTER_DELAY_PCT", getattr(config, "MIN_NET_PROFIT_AFTER_DELAY_PCT", 18.0)))
BATCH_CANDIDATE_LIMIT = min(30, int(os.getenv("BATCH_CANDIDATE_LIMIT", getattr(config, "BATCH_CANDIDATE_LIMIT", 30))))
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", getattr(config, "ALERT_COOLDOWN_SEC", 2700)))  # Cooldown 45m per lorong


def _esc_md(text: str) -> str:
    """Sanitasi karakter spesial Telegram Markdown agar sendMessage tidak pernah gagal HTTP 400."""
    if not text:
        return ""
    cleaned = str(text)
    for ch in ("_", "*", "`", "[", "]"):
        cleaned = cleaned.replace(ch, "")
    return cleaned.strip()


def _load_corridor_ledger() -> dict:
    """Baca ledger cooldown alert lintas-invokasi di /tmp dengan pembersihan otomatis TTL."""
    now = time.time()
    try:
        if os.path.exists(ALERT_LEDGER_PATH):
            with open(ALERT_LEDGER_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                return {k: float(ts) for k, ts in raw.items() if (now - float(ts)) < ALERT_COOLDOWN_SEC}
    except Exception:
        pass
    return {}


def _is_corridor_alerted(mint: str, corridor_id: int) -> bool:
    """Cek apakah (mint, corridor_id) sudah pernah dikirim dalam jendela cooldown."""
    key = f"{mint}:C{corridor_id}"
    if key in RECENT_ALERTED_MINTS:
        return True
    ledger = _load_corridor_ledger()
    return key in ledger


def _mark_corridor_alerted(mint: str, corridor_id: int) -> None:
    """Simpan jejak alert per-lorong agar koin di Lorong 0 tetap bisa alert lagi jika naik ke Lorong 2."""
    key = f"{mint}:C{corridor_id}"
    RECENT_ALERTED_MINTS.add(key)
    try:
        ledger = _load_corridor_ledger()
        ledger[key] = time.time()
        with open(ALERT_LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(ledger, f)
    except Exception:
        pass


def send_telegram_direct(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Kirim pesan langsung ke Telegram Bot API dengan fallback plain-text otomatis jika Markdown ditolak."""
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
        if e.code == 400 and "parse entities" in err_msg.lower():
            fallback_payload = {
                "chat_id": chat_id,
                "text": text.replace("*", "").replace("`", ""),
                "disable_web_page_preview": False
            }
            fb_req = urllib.request.Request(
                url,
                data=json.dumps(fallback_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(fb_req, timeout=7.0):
                    return True, "Pesan terkirim ke Telegram (fallback plain-text)"
            except Exception as fb_err:
                return False, f"Telegram fallback gagal: {fb_err}"
        return False, f"Telegram API HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, str(e)


def fetch_json(url: str, timeout: float = 3.2, retries: int = 1):
    """Utility fetch JSON cepat dengan 1x retry otomatis pada gangguan transien / HTTP 429 & 5xx."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as http_err:
            if http_err.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(0.12)
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(0.12)
                continue
            return None
    return None


def _fetch_jup_single_quote(input_mint: str, output_mint: str, amount: int, timeout: float = 2.6):
    """Ambil quote swap di Jupiter V1 Lite API (real-time slot Solana) dengan fallback v6."""
    primary_url = (
        f"https://lite-api.jup.ag/swap/v1/quote"
        f"?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps=200"
    )
    data = fetch_json(primary_url, timeout=timeout)
    if isinstance(data, dict) and data.get("routePlan") is not None and data.get("outAmount"):
        return data
    fallback_url = (
        f"https://quote-api.jup.ag/v6/quote"
        f"?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps=200"
    )
    data_fb = fetch_json(fallback_url, timeout=1.8)
    if isinstance(data_fb, dict) and data_fb.get("routePlan") is not None and data_fb.get("outAmount"):
        return data_fb
    return None


def simulate_jupiter_roundtrip(mint: str, timeout: float = 2.6) -> dict:
    """
    SIMULASI PULANG-PERGI NYATA (0.1 SOL -> Token -> SOL) DI SLOT VALIDATOR DETIK INI:
    1. Membuktikan koin 100% BISA DIJUAL KEMBALI (Anti Honeypot Jual & Anti Empty-Bin DLMM).
    2. Mengukur friksi riil pulang-pergi (LP fee + price impact beli & jual + pajak Token-2022).
    3. Mengekstrak harga spot live on-chain untuk menangkap dump/pump yang belum muncul di DexScreener!
    """
    q_buy = _fetch_jup_single_quote(SOL_MINT, mint, TEST_BUY_LAMPORTS, timeout=timeout)
    if not q_buy:
        return {"ok": False, "reason": "Tidak memiliki rute beli likuid 0.1 SOL di Jupiter DEX"}

    out_tokens_raw = int(q_buy.get("outAmount") or 0)
    if out_tokens_raw <= 0:
        return {"ok": False, "reason": "Simulasi beli menghasilkan 0 token (Kolam kering)"}

    q_sell = _fetch_jup_single_quote(mint, SOL_MINT, out_tokens_raw, timeout=timeout)
    if not q_sell:
        return {"ok": False, "reason": "JEBAKAN HONEYPOT: Bisa dibeli tapi TIDAK BISA DIJUAL kembali ke SOL!"}

    sol_back_lamports = int(q_sell.get("outAmount") or 0)
    if sol_back_lamports <= 0:
        return {"ok": False, "reason": "JEBAKAN HONEYPOT: Estimasi pencairan SOL kembali = 0"}

    rt_loss_pct = max(0.0, (1.0 - (sol_back_lamports / float(TEST_BUY_LAMPORTS))) * 100.0)
    return {
        "ok": True,
        "in_lamports": TEST_BUY_LAMPORTS,
        "out_tokens_raw": out_tokens_raw,
        "sol_back_lamports": sol_back_lamports,
        "rt_loss_pct": round(rt_loss_pct, 2)
    }


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
        tp_mc = min(90_000.0, mc * 1.44)
        return True, "LORONG 0 (Early Ignition < $78k)", {
            "corridor_id": 0,
            "corridor_name": "Lorong 0: Early Ignition (Sebelum Tembok $100k)",
            "corridor_max_mc": 78_000.0,
            "min_liq": 10_500.0,
            "min_turnover_h1": 0.55,
            "mature_turnover_h1": 0.32,
            "min_cum_tx": 160,
            "min_avg_ticket": 18.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 14.0
        }

    # LORONG 1: Post-$100k Breakout ($128k - $168k) -> Front-run sebelum tembok $200k
    if 128_000.0 < mc <= 168_000.0:
        tp_mc = min(190_000.0, max(mc * 1.30, 184_000.0))
        return True, "LORONG 1 (Post-$100k Breakout)", {
            "corridor_id": 1,
            "corridor_name": "Lorong 1: Breakout Tembok $100k -> Menuju $190k",
            "corridor_max_mc": 168_000.0,
            "min_liq": 18_000.0,
            "min_turnover_h1": 0.75,
            "mature_turnover_h1": 0.22,
            "min_cum_tx": 420,
            "min_avg_ticket": 22.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 13.5
        }

    # LORONG 2 (GOLDEN CORRIDOR): Lolos Kuburan $200k ($228k - $315k) -> Ruang Kosong ke $418k
    if 228_000.0 < mc <= 315_000.0:
        tp_mc = min(418_000.0, mc * 1.46)
        return True, "LORONG 2 (Golden Wave-2 Corridor)", {
            "corridor_id": 2,
            "corridor_name": "Lorong 2: Lolos Ujian $200k -> Ruang Terbang ke $400k+",
            "corridor_max_mc": 315_000.0,
            "min_liq": 26_000.0,
            "min_turnover_h1": 0.85,
            "mature_turnover_h1": 0.14,
            "min_cum_tx": 900,
            "min_avg_ticket": 26.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 14.0
        }

    # LORONG 3 (WHALE CORRIDOR): Jebol Tembok $500k ($525k - $650k) -> Magnet $1M (Keluar di $825k)
    tp_mc = min(825_000.0, mc * 1.38)
    return True, "LORONG 3 (Whale Breakout > $525k)", {
        "corridor_id": 3,
        "corridor_name": "Lorong 3: Jebol Setengah Juta -> Front-Run $825k",
        "corridor_max_mc": 650_000.0,
        "min_liq": 48_000.0,
        "min_turnover_h1": 0.70,
        "mature_turnover_h1": 0.09,
        "min_cum_tx": 1800,
        "min_avg_ticket": 30.0,
        "target_tp_mc": tp_mc,
        "cut_loss_pct": 13.5
    }


# ==============================================================================
# MODUL 2: EVALUASI MOMENTUM, ANTI DEAD-CAT BOUNCE, & PROYEKSI DELAY 22 DETIK
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

    # 2. Data Orderflow M5, H1, dan Kumulatif H24
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

    # 3. Usia Pair: Lolos Jito Bundle Block-0 (>= 3.5m) & Batas Koin Aktif (24j, atau 36j jika organik ramai)
    pair_created_at_ms = best_pair.get("pairCreatedAt")
    now_ms = time.time() * 1000.0
    if pair_created_at_ms:
        age_minutes = (now_ms - float(pair_created_at_ms)) / 60000.0
        if age_minutes < 3.5:
            return False, f"Terlalu baru ({age_minutes:.1f}m < 3.5m): Fase rawan MEV bundle block-0", {}
        max_age_mins = 2160.0 if (cum_tx_24h >= 5000 and liq >= 30_000.0) else 1440.0
        if age_minutes > max_age_mins:
            return False, f"Koin lama ({age_minutes/60.0:.1f} jam > {max_age_mins/60.0:.0f}j): Rawan jebakan re-pump koin mati", {}
    else:
        age_minutes = 12.0

    # 4. Temperatur Candle Anti-Pucuk & Anti Dead-Cat Bounce (Adaptif untuk Steady Climber Organik)
    price_change = best_pair.get("priceChange") or {}
    p_m5 = float(price_change.get("m5", 0.0) or 0.0)
    p_h1 = float(price_change.get("h1", 0.0) or 0.0)
    p_h6 = float(price_change.get("h6", 0.0) or 0.0)

    is_proven_climber = (
        age_minutes >= 45.0
        and cum_tx_24h >= max(2500, c_cfg["min_cum_tx"] * 2)
        and p_h1 >= 15.0
        and p_h6 >= 20.0
    )
    min_p_m5 = 5.0 if is_proven_climber else 6.0

    if p_m5 < min_p_m5:
        return False, f"Momentum 5m belum menginjak gas ({p_m5:+.1f}% < +{min_p_m5:.1f}%) - Tunggu dorongan naik", {}
    if p_m5 > 45.0:
        return False, f"ANTI-PUCUK: Candle 5m sudah kepanjangan (+{p_m5:.1f}% > +45%). Rawan koreksi sesaat!", {}
    if p_h1 <= 0.0 or p_h6 < -12.0:
        return False, f"Tren makro sedang distribusi/merah (H1: {p_h1:+.1f}%, H6: {p_h6:+.1f}%)", {}

    # Anti Dead-Cat Bounce: koin >= 60 menit yang sempat ambruk lalu pantul kecil di 5m terakhir
    if age_minutes >= 60.0 and p_h1 < (p_m5 * 0.60):
        return (
            False,
            f"Jebakan Dead-Cat Bounce: Pantulan 5m (+{p_m5:.1f}%) di tengah tren 1 jam lemah (+{p_h1:.1f}%)",
            {}
        )

    # 5. PENANGKAL DEV LICIK #1: Kepadatan Transaksi Seumur Hidup (Anti Bundled Ghost Pump)
    if cum_tx_24h < c_cfg["min_cum_tx"]:
        return (
            False,
            f"Jebakan Ghost-Pump Dev: MC ${mc:,.0f} tapi total transaksi baru {cum_tx_24h} (< {c_cfg['min_cum_tx']} tx)",
            {}
        )

    # 6. Dominasi Pembeli Nyata & Kesehatan Orderflow 1 Jam (Kalibrasi MEV Arbitrase pada Pool Ramai)
    if total_m5 < 25 or sells_m5 >= buys_m5:
        return False, f"Partisipasi pembeli lemah ({buys_m5}B / {sells_m5}S)", {}

    buy_sell_ratio_m5 = buys_m5 / max(1, sells_m5)
    buy_sell_ratio_h1 = buys_h1 / max(1, sells_h1) if (buys_h1 + sells_h1) > 0 else 1.5
    if (buys_h1 + sells_h1) >= 80 and buy_sell_ratio_h1 < 1.05:
        return False, f"Distribusi H1 berat: Penjual mendominasi 1 jam terakhir (Rasio H1 {buy_sell_ratio_h1:.2f}x)", {}

    high_tx_organic_flow = (
        total_m5 >= 80
        and is_proven_climber
        and buy_sell_ratio_h1 >= 1.18
    )
    min_bs_m5 = 1.35 if high_tx_organic_flow else 1.85
    min_cvd = 15.0 if high_tx_organic_flow else 29.8
    h1_decay_floor = 0.82 if high_tx_organic_flow else 0.90

    if buy_sell_ratio_m5 < min_bs_m5:
        return False, f"Rasio Buy/Sell kurang dominan ({buy_sell_ratio_m5:.2f}x < {min_bs_m5:.2f}x)", {}

    if age_minutes >= 20.0 and buy_sell_ratio_m5 < (buy_sell_ratio_h1 * h1_decay_floor):
        return False, f"Momentum beli melambat (Rasio M5 {buy_sell_ratio_m5:.2f}x < H1 {buy_sell_ratio_h1:.2f}x)", {}

    cvd_ratio = round(((buys_m5 - sells_m5) / max(1, total_m5)) * 100.0, 1)
    if cvd_ratio < min_cvd:
        return False, f"CVD Net Inflow kurang kuat (+{cvd_ratio}% < +{min_cvd}%)", {}

    # 7. PENANGKAL DEV LICIK #2: Anti-Siphon Bot (Banyak Klik Buy Kecil vs Sedikit Sell Raksasa)
    if buy_sell_ratio_m5 >= 2.8 and vol_m5 >= 5000.0 and p_m5 < 8.5:
        return (
            False,
            f"Terdeteksi Siphon Bot Dev: Rasio klik {buy_sell_ratio_m5:.1f}x tapi harga tertahan (+{p_m5:.1f}%) -> Sell paus diam-diam!",
            {}
        )

    # 8. PENANGKAL DEV LICIK #3: Anti-Wash-Breakout Churn & Perputaran Suplai (Adaptif Usia Koin)
    vol_to_liq_m5 = vol_m5 / max(1.0, liq)
    if vol_to_liq_m5 > 2.4:
        return (
            False,
            f"Terdeteksi Wash-Trading Bot: Volume 5m (${vol_m5:,.0f}) > 2.4x isi kolam LP (${liq:,.0f}) -> Uang diputar di tempat!",
            {}
        )

    turnover_h1 = vol_h1 / max(1.0, mc)
    req_turnover_h1 = c_cfg["mature_turnover_h1"] if is_proven_climber else c_cfg["min_turnover_h1"]
    if age_minutes >= 25.0 and turnover_h1 < req_turnover_h1:
        return (
            False,
            f"Suplai bawah belum ganti tangan: Vol 1h/MC baru {turnover_h1:.2f}x (< {req_turnover_h1:.2f}x)",
            {}
        )

    # 9. Akselerasi Uang Segar (Fresh Money Surge)
    effective_h1_mins = min(60.0, max(6.0, age_minutes))
    vol_rate_m5 = vol_m5 / 5.0
    vol_rate_h1 = vol_h1 / effective_h1_mins
    accel_ratio = vol_rate_m5 / max(1.0, vol_rate_h1)
    min_accel = 0.92 if high_tx_organic_flow else 1.05
    if age_minutes >= 15.0 and accel_ratio < min_accel and (vol_m5 / max(1.0, mc)) < 0.10:
        return (
            False,
            f"Volume sedang mendingin (Akselerasi {accel_ratio:.2f}x < {min_accel:.2f}x & intensitas M5 rendah)",
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

    # 11. KOMPENSASI DELAY PENCARIAN -> TELEGRAM -> KLIK MANUSIA (~22 DETIK)
    # Hitung seberapa jauh harga sudah berlari saat user benar-benar membuka Telegram & klik Buy
    vel_pct_per_sec = max(0.0, p_m5) / 300.0
    accel_boost = min(2.2, max(1.0, accel_ratio * 0.85))
    est_delay_drift_pct = round(min(15.0, vel_pct_per_sec * PIPELINE_REACTION_DELAY_SEC * accel_boost), 2)
    prelim_projected_mc = mc * (1.0 + (est_delay_drift_pct / 100.0))

    if prelim_projected_mc > c_cfg["corridor_max_mc"]:
        return (
            False,
            f"ANTI-TELAT DELAY 22s: MC ${mc:,.0f} terlalu mepet plafon; saat masuk Tele diproyeksi sudah menyentuh Zona Merah (${prelim_projected_mc:,.0f})",
            {}
        )

    prelim_gross_pct = ((c_cfg["target_tp_mc"] - prelim_projected_mc) / prelim_projected_mc) * 100.0
    if (prelim_gross_pct - 3.5) < MIN_NET_PROFIT_AFTER_DELAY_PCT:
        return (
            False,
            f"Ruang profit bersih pasca-delay 22s sempit (+{prelim_gross_pct - 3.5:.1f}% < +{MIN_NET_PROFIT_AFTER_DELAY_PCT:.0f}%)",
            {}
        )

    organic_depth_bonus = 8.0 if high_tx_organic_flow else 0.0
    score = min(
        99,
        int(
            35.0
            + min(25.0, (buy_sell_ratio_m5 / 2.6) * 25.0)
            + min(20.0, (cvd_ratio / 65.0) * 20.0)
            + min(12.0, (accel_ratio / 1.8) * 12.0)
            + (8.0 if has_commitment else 3.0)
            + organic_depth_bonus
        )
    )
    if score < 76:
        return False, f"Skor konvergensi momentum kurang ({score}/100 < 76)", {}

    return True, "LOLOS CORRIDOR & DELAY GUARD", {
        "score": score,
        "corridor_id": c_cfg["corridor_id"],
        "corridor_name": c_cfg["corridor_name"],
        "corridor_max_mc": c_cfg["corridor_max_mc"],
        "target_tp_mc": round(c_cfg["target_tp_mc"], 0),
        "cut_loss_pct": c_cfg["cut_loss_pct"],
        "est_delay_drift_pct": est_delay_drift_pct,
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
# MODUL 3: AUDIT KONTRAK, SALDO DEV, LP ASLI (HARD SOL), & SINDIKAT CABAL TOP-20
# ==============================================================================
def analyze_holders_and_lp_lock(rug_data: dict, pair_address: str = "") -> tuple[bool, str, dict]:
    if not isinstance(rug_data, dict):
        return False, "Audit RugCheck timeout (Fail-Closed Guard)", {}

    if rug_data.get("rugged") is True:
        return False, "Token ditandai RUGGED oleh RugCheck", {}

    token_info = rug_data.get("token") or {}
    decimals = int(token_info.get("decimals") if token_info.get("decimals") is not None else 6)
    supply_raw = float(token_info.get("supply") or 0.0)

    # 1. Blokir mutlak Mint Authority & Freeze Authority aktif
    if token_info.get("mintAuthority") or rug_data.get("mintAuthority"):
        return False, "BAHAYA KONTRAK: Mint Authority masih aktif (Dev bisa cetak koin tak terbatas!)", {}
    if token_info.get("freezeAuthority") or rug_data.get("freezeAuthority"):
        return False, "BAHAYA KONTRAK: Freeze Authority masih aktif (Dompet pembeli bisa dibekukan!)", {}

    # 2. Blokir Pajak Siluman Token-2022 & Ekstensi Penyedot Saldo (Tanpa False-Positive pada field null PumpSwap!)
    transfer_fee = rug_data.get("transferFee") or {}
    if float(transfer_fee.get("pct") or 0.0) > 0.0:
        return False, f"Pajak Transfer Token-2022 terdeteksi ({transfer_fee.get('pct')}%)", {}

    tok_ext = rug_data.get("token_extensions")
    if isinstance(tok_ext, dict):
        if tok_ext.get("nonTransferable") is True:
            return False, "Ekstensi berbahaya Token-2022 aktif: NonTransferable (Honeypot tidak bisa ditransfer!)", {}
        if tok_ext.get("permanentDelegate") is not None:
            pd_val = tok_ext.get("permanentDelegate")
            if not isinstance(pd_val, dict) or pd_val.get("delegate") or pd_val.get("authority"):
                return False, "Ekstensi berbahaya Token-2022 aktif: PermanentDelegate (Dev bisa menyedot saldo!)", {}
        if tok_ext.get("transferHook") is not None:
            th_val = tok_ext.get("transferHook")
            if not isinstance(th_val, dict) or th_val.get("programId") or th_val.get("authority"):
                return False, "Ekstensi berbahaya Token-2022 aktif: TransferHook (Kontrak kustom pemblokir jual!)", {}
        if tok_ext.get("pausableConfig") is not None:
            pc_val = tok_ext.get("pausableConfig")
            if not isinstance(pc_val, dict) or pc_val.get("authority") or pc_val.get("paused"):
                return False, "Ekstensi berbahaya Token-2022 aktif: PausableConfig", {}
        tf_cfg = tok_ext.get("transferFeeConfig")
        if isinstance(tf_cfg, dict):
            newer_bps = float((tf_cfg.get("newerTransferFee") or {}).get("transferFeeBasisPoints") or 0.0)
            older_bps = float((tf_cfg.get("olderTransferFee") or {}).get("transferFeeBasisPoints") or 0.0)
            if max(newer_bps, older_bps) > 0.0:
                return False, f"Pajak Transfer Token-2022 aktif ({max(newer_bps, older_bps) / 100.0:.2f}%)", {}
    elif isinstance(tok_ext, list):
        ext_names = {
            str(x if not isinstance(x, dict) else (x.get("extension") or x.get("name") or "")).lower()
            for x in tok_ext
        }
        if ext_names & {"permanentdelegate", "transferhook", "nontransferable"}:
            return False, "Ekstensi berbahaya Token-2022 terdeteksi (PermanentDelegate / TransferHook)", {}

    # 3. Verifikasi Kepemilikan Dev Asli <= 2.5% (Syarat Koin Ditinggal / Bersih dari Dev)
    creator_bal_raw = float(rug_data.get("creatorBalance") or 0.0)
    creator_pct = (creator_bal_raw / supply_raw * 100.0) if supply_raw > 0 else 0.0
    if creator_pct > 2.5:
        return False, f"Dev masih memegang {creator_pct:.1f}% suplai (> 2.5%) - Rawan dev dump!", {}

    holders = rug_data.get("topHolders") or rug_data.get("holders") or []
    if not holders:
        return False, "Data Top Holders RugCheck belum tersedia (Fail-Closed)", {}

    markets = rug_data.get("markets") or []
    market_vaults = set()
    total_locked_usd = 0.0
    total_unlocked_usd = 0.0
    primary_lp_locked_pct = 100.0
    sol_backing_ratio = 50.0

    # 4. Analisis LP Lock, Fake Secondary Pool, & Kecukupan SOL Asli di Kolam (Anti Single-Sided LP)
    sorted_markets = sorted(
        markets,
        key=lambda m: float((m.get("lp") or {}).get("baseUSD") or 0.0) + float((m.get("lp") or {}).get("quoteUSD") or 0.0),
        reverse=True
    )
    for idx, m in enumerate(sorted_markets):
        if m.get("liquidityA"): market_vaults.add(str(m["liquidityA"]).lower())
        if m.get("liquidityB"): market_vaults.add(str(m["liquidityB"]).lower())
        if m.get("pubkey"): market_vaults.add(str(m["pubkey"]).lower())
        if m.get("mintLP"): market_vaults.add(str(m["mintLP"]).lower())

        lp = m.get("lp") or {}
        base_usd = float(lp.get("baseUSD") or 0.0)
        quote_usd = float(lp.get("quoteUSD") or 0.0)
        pool_usd = base_usd + quote_usd
        locked_pct = lp.get("lpLockedPct")
        m_type = str(m.get("marketType") or "").lower()
        lp_mint = str(lp.get("lpMint") or "")

        if idx == 0 and pool_usd > 2500.0:
            hard_usd = 0.0
            if str(lp.get("baseMint") or "") in HARD_QUOTE_MINTS:
                hard_usd = base_usd
            elif str(lp.get("quoteMint") or "") in HARD_QUOTE_MINTS:
                hard_usd = quote_usd
            if hard_usd > 0.0:
                sol_backing_ratio = round((hard_usd / pool_usd) * 100.0, 1)
                if sol_backing_ratio < 32.0:
                    return (
                        False,
                        f"Jebakan Single-Sided LP Palsu: Isi kolam hanya {sol_backing_ratio:.1f}% aset keras SOL/USDC/WBTC asli (< 32%)",
                        {}
                    )

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

    if total_unlocked_usd > 15_000.0 and total_unlocked_usd > total_locked_usd:
        return (
            False,
            f"Jebakan Fake Secondary Pool: Likuiditas tak terkunci (${total_unlocked_usd:,.0f}) > terkunci (${total_locked_usd:,.0f})",
            {}
        )

    # 5. Pemisahan Pool AMM (termasuk Vault Owner PumpSwap/Meteora) vs Pure Holders & Deteksi Sindikat Cabal
    norm_pair = (pair_address or "").lower()
    pool_holdings = []
    pure_holdings = []

    for h in holders[:25]:
        addr = str(h.get("address") or "").lower()
        owner = str(h.get("owner") or "").lower()
        pct = float(h.get("pct") or 0.0)
        is_pool = (
            addr == norm_pair
            or (owner and owner == norm_pair)
            or addr in market_vaults
            or (owner and owner in market_vaults)
            or addr in KNOWN_POOL_IDENTIFIERS
            or (owner and owner in KNOWN_POOL_IDENTIFIERS)
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

    # Deteksi Kluster Cabal Kloningan Menengah (1.20% - 4.50%):
    # Hanya blokir jika dev memecah saldo kloningan identik (>= 4 dompet selisih <= 0.015%, atau >= 3 paus >= 2.40% selisih <= 0.04%)
    # sehingga kurva Pareto ritel organik yang menurun alami (mis. 1.75%, 1.71%, 1.69%, 1.66%, 1.59%) TIDAK terkena false-positive!
    mid_holders = [x["pct"] for x in pure_holdings[:20] if 1.20 <= x["pct"] <= 4.50]
    for p_ref in mid_holders:
        cloned_mid = [p for p in mid_holders if abs(p - p_ref) <= 0.015]
        if len(cloned_mid) >= 4 and sum(cloned_mid) >= 5.2:
            return (
                False,
                f"Cabal Cluster Terdeteksi: Dev memecah {sum(cloned_mid):.1f}% suplai ke {len(cloned_mid)} dompet kloningan (~{p_ref:.2f}%)",
                {}
            )

    heavy_holders = [x["pct"] for x in pure_holdings[:15] if 2.40 <= x["pct"] <= 4.60]
    for p_ref in heavy_holders:
        heavy_cluster = [p for p in heavy_holders if abs(p - p_ref) <= 0.04]
        if len(heavy_cluster) >= 3:
            return (
                False,
                f"Heavy Splitter Cabal: {len(heavy_cluster)} paus memegang porsi kembar (~{p_ref:.2f}%, total {sum(heavy_cluster):.1f}%)",
                {}
            )

    # Deteksi Sindikat Micro-Split CEX (Dompet Kloningan Presisi di Top 20: >= 5 dompet selisih <= 0.03%)
    micro_holders = [x["pct"] for x in pure_holdings[:20] if 0.65 <= x["pct"] < 1.20]
    for p_ref in micro_holders:
        m_cluster = [p for p in micro_holders if abs(p - p_ref) <= 0.03]
        if len(m_cluster) >= 5:
            return (
                False,
                f"Micro-Split CEX Syndicate: {len(m_cluster)} dompet memegang persentase kloningan identik (~{p_ref:.2f}%)",
                {}
            )

    return True, "Distribusi Bersih & LP Aman", {
        "decimals": decimals,
        "creator_pct": round(creator_pct, 2),
        "pure_top10": round(top10_pure_pct, 1),
        "max_single": round(max_single_pure, 1),
        "pool_pct": round(pool_total_pct, 1),
        "lp_locked_pct": primary_lp_locked_pct,
        "sol_backing_ratio": sol_backing_ratio
    }


# ==============================================================================
# MODUL 4: AUDIT MENDALAM PARALEL (RUGCHECK + ROUND-TRIP JUPITER + LIVE DRIFT)
# ==============================================================================
def audit_and_dispatch_candidate(
    mint: str,
    pairs: list,
    best_pair: dict,
    metrics: dict,
    config
) -> dict:
    name = best_pair.get("baseToken", {}).get("name", "Unknown")
    symbol = best_pair.get("baseToken", {}).get("symbol", "SOL")
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)
    dex_id = best_pair.get("dexId", "raydium")
    pair_address = best_pair.get("pairAddress", "")

    txns_m5 = best_pair.get("txns", {}).get("m5", {})
    buys_m5 = int(txns_m5.get("buys", 0))
    sells_m5 = int(txns_m5.get("sells", 0))

    # Tembak paralel: RugCheck Full Report + Jupiter 0.1 SOL Round-Trip (Buy -> Sell)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as sub_exec:
        f_rug = sub_exec.submit(fetch_json, f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", 3.5)
        f_rt = sub_exec.submit(simulate_jupiter_roundtrip, mint, 2.7)
        rug_data = f_rug.result()
        rt_res = f_rt.result()

    # 1. Fail-Closed RugCheck Risk Score
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
            "reason": f"RugCheck skor risiko tinggi ({rug_score} >= 300)"
        }

    for r in (rug_data.get("risks") or []):
        r_name = str(r.get("name", "")).lower()
        r_level = str(r.get("level", "")).lower()
        if ("danger" in r_level or "high" in r_level) and any(
            k in r_name for k in ("liquidity", "creator", "cabal", "cluster", "sybil", "bundled", "dump", "freeze", "mint", "unlocked", "honeypot", "fee")
        ):
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": f"Risiko On-Chain RugCheck: {r.get('name')}"
            }

    # 2. Verifikasi Kontrak, Saldo Dev <= 2.5%, Hard-SOL Backing, & Top-20 Cabal
    holders_ok, holders_reason, holder_meta = analyze_holders_and_lp_lock(rug_data, pair_address)
    if not holders_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": holders_reason
        }

    # 3. Verifikasi Simulasi Jual-Beli Pulang-Pergi 0.1 SOL (Anti Honeypot & Pajak Siluman)
    if not rt_res.get("ok"):
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": rt_res.get("reason", "Gagal simulasi Round-Trip Jupiter")
        }

    rt_loss_pct = float(rt_res["rt_loss_pct"])
    if rt_loss_pct > MAX_ROUNDTRIP_LOSS_PCT:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": f"Friksi pulang-pergi 0.1 SOL terlalu mahal ({rt_loss_pct:.2f}% > {MAX_ROUNDTRIP_LOSS_PCT}%) - Kolam timpang/licin!"
        }

    # 4. DETEKSI LAG DEXSCREENER VS HARGA SPOT REAL-TIME SLOT SOLANA DETIK INI!
    # Hanya bandingkan pool WSOL jika likuiditasnya representatif (>= $8k & >= 25% pool utama) agar tidak tertipu pool debu
    wsol_pair = next(
        (
            p for p in pairs
            if (p.get("quoteToken") or {}).get("address") == SOL_MINT
            and float(p.get("priceNative") or 0.0) > 0.0
            and float((p.get("liquidity") or {}).get("usd") or 0.0) >= max(8000.0, liq * 0.25)
        ),
        None
    )
    live_drift_pct = 0.0
    if wsol_pair:
        dex_price_native = float(wsol_pair["priceNative"])
        dec = int(holder_meta.get("decimals", 6))
        tokens_ui = rt_res["out_tokens_raw"] / float(10 ** dec)
        if tokens_ui > 0:
            jup_exec_pn = (TEST_BUY_LAMPORTS / 1_000_000_000.0) / tokens_ui
            # Koreksi setengah spread pulang-pergi untuk mendapatkan mid-price on-chain akurat
            jup_mid_pn = jup_exec_pn * (1.0 - (min(MAX_ROUNDTRIP_LOSS_PCT, rt_loss_pct) / 200.0))
            live_drift_pct = round(((jup_mid_pn - dex_price_native) / dex_price_native) * 100.0, 2)

            if live_drift_pct < -6.0:
                return {
                    "token": f"{name} (${symbol})",
                    "mint": mint,
                    "verdict": "SKIP",
                    "reason": f"TERDETEKSI LIVE DUMP ON-CHAIN ({live_drift_pct:+.1f}% di bawah DexScreener) — Bandar baru saja banting harga!"
                }
            if live_drift_pct > 12.0:
                return {
                    "token": f"{name} (${symbol})",
                    "mint": mint,
                    "verdict": "SKIP",
                    "reason": f"HARGA KABUR DULUAN ON-CHAIN (+{live_drift_pct:.1f}% di atas DexScreener) — Terlambat masuk, rawan pucuk!"
                }

    # 5. KALKULASI FINAL MC REAL-TIME + KOMPENSASI DELAY 22 DETIK + GARANSI PROFIT BERSIH
    live_mc = mc * (1.0 + (live_drift_pct / 100.0))
    projected_entry_mc = live_mc * (1.0 + (metrics["est_delay_drift_pct"] / 100.0))
    tp_mc = float(metrics["target_tp_mc"])
    corridor_max_mc = float(metrics["corridor_max_mc"])

    if live_mc < 25_000.0 or projected_entry_mc > corridor_max_mc:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": f"Proyeksi MC saat klik di Telegram (${projected_entry_mc:,.0f}) sudah keluar batas aman lorong (${corridor_max_mc:,.0f})"
        }

    total_friction_pct = round(rt_loss_pct + PRIORITY_TIP_BUFFER_PCT, 2)
    gross_from_projected_pct = ((tp_mc - projected_entry_mc) / projected_entry_mc) * 100.0
    net_from_projected_pct = round(gross_from_projected_pct - total_friction_pct, 1)

    if net_from_projected_pct < MIN_NET_PROFIT_AFTER_DELAY_PCT:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": f"Estimasi untung bersih pasca-delay ({net_from_projected_pct:+.1f}%) di bawah standar aman +{MIN_NET_PROFIT_AFTER_DELAY_PCT:.0f}%"
        }

    # Batas Harga/MC Maksimal Boleh Klik Beli (agar kalau user buka HP telat, tidak terjebak beli di pucuk)
    max_mc_for_min_profit = tp_mc / (1.0 + ((MIN_NET_PROFIT_AFTER_DELAY_PCT + total_friction_pct) / 100.0))
    max_safe_entry_mc = round(min(corridor_max_mc, max_mc_for_min_profit), 0)
    cut_loss = metrics["cut_loss_pct"]

    safe_name = _esc_md(name)
    safe_symbol = _esc_md(symbol)

    alert_msg = (
        f"🎯 *[CORRIDOR SNIPER V2: DELAY-PROOF & ROUND-TRIP VERIFIED]*\n\n"
        f"🪙 *Token:* {safe_name} (`${safe_symbol}`)\n"
        f"📍 *Mint:* `{mint}`\n"
        f"🛤️ *Posisi:* `{metrics['corridor_name']}`\n\n"
        f"💰 *MC Live On-Chain:* `${live_mc:,.0f}` *(Drift vs DEX: {live_drift_pct:+.1f}%)*\n"
        f"⏱️ *Est. MC Saat Anda Klik (~{int(PIPELINE_REACTION_DELAY_SEC)}d):* `${projected_entry_mc:,.0f}`\n"
        f"🚧 *BATAS MAKSIMAL BOLEH BELI:* `${max_safe_entry_mc:,.0f}` *(Lewat ini = JANGAN BELI!)*\n"
        f"🎯 *Target Take-Profit Aman:* `${tp_mc:,.0f}` *(Est. Untung Bersih +{net_from_projected_pct}%)*\n"
        f"🛑 *Hard Stop-Loss:* `-{cut_loss}%` *(Wajib pasang otomatis)*\n\n"
        f"🔄 *Uji Jual-Beli 0.1 SOL:* `BISA DIJUAL` (Friksi Riil `{rt_loss_pct}%` + Tip `{PRIORITY_TIP_BUFFER_PCT}%`)\n"
        f"⚡ *Nafas Baru:* 5m `+{metrics['price_change_m5']}%` | Akselerasi `{metrics['accel_ratio']}x` | Turnover 1h `{metrics['turnover_h1']}x`\n"
        f"📊 *Orderflow M5:* `{buys_m5} Buy` vs `{sells_m5} Sell` (`{metrics['buy_sell_ratio']}x` | CVD `+{metrics['cvd_ratio']}%`)\n"
        f"💧 *Likuiditas:* `${liq:,.0f}` ({_esc_md(dex_id.title())} | SOL Asli `{holder_meta['sol_backing_ratio']}%` | Lock `{holder_meta['lp_locked_pct']}%`)\n"
        f"👥 *Pure Top 10:* `{holder_meta['pure_top10']}%` | *Saldo Dev:* `{holder_meta['creator_pct']}%`\n\n"
        f"🔗 *DexScreener:* [Lihat Chart](https://dexscreener.com/solana/{mint})\n"
        f"⚡ *Jupiter Swap:* [Eksekusi Cepat](https://jup.ag/swap/SOL-{mint})"
    )

    bot_tok = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")

    sent, detail = send_telegram_direct(bot_tok, chat_id, alert_msg)
    if sent:
        _mark_corridor_alerted(mint, int(metrics.get("corridor_id", 0)))
        print(f"[VERCEL-ALERT] 🚀 Sinyal V2 Delay-Proof terkirim untuk {safe_name} (${safe_symbol})!", flush=True)
        return {
            "token": f"{safe_name} (${safe_symbol})",
            "mint": mint,
            "verdict": "ALERT_SENT",
            "corridor": metrics["corridor_name"],
            "live_mc": round(live_mc, 0),
            "projected_entry_mc": round(projected_entry_mc, 0),
            "max_safe_entry_mc": max_safe_entry_mc,
            "target_tp_mc": tp_mc,
            "roundtrip_loss_pct": rt_loss_pct,
            "est_net_gain_pct": net_from_projected_pct,
            "reason": (
                f"Lolos V2 ({metrics['corridor_name']}) | Max Entry ${max_safe_entry_mc:,.0f} -> "
                f"TP ${tp_mc:,.0f} (+{net_from_projected_pct}% bersih setelah delay {int(PIPELINE_REACTION_DELAY_SEC)}s & fee riil)"
            )
        }
    return {
        "token": f"{safe_name} (${safe_symbol})",
        "mint": mint,
        "verdict": "ALERT_FAILED",
        "corridor": metrics["corridor_name"],
        "max_safe_entry_mc": max_safe_entry_mc,
        "target_tp_mc": tp_mc,
        "est_net_gain_pct": net_from_projected_pct,
        "reason": detail
    }


def evaluate_single_mint(mint: str, config, boost_amount: int = 0) -> dict:
    """Helper evaluasi 1 token mandiri (kompatibel penuh untuk pemanggilan satuan)."""
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

    ok, filter_reason, metrics = evaluate_market_candidate(
        best_pair, pair_count=active_pools_count, boost_amount=boost_amount
    )
    if not ok:
        return {"token": f"{name} (${symbol})", "mint": mint, "verdict": "SKIP", "reason": filter_reason}

    return audit_and_dispatch_candidate(mint, pairs, best_pair, metrics, config)


def run_live_market_scan(engine, config) -> dict:
    """
    FAST-TIME BATCH MULTI-FEED SCANNER (25 KOIN SEKALI TEMBAK < 1.5 DETIK):
    1. Tarik paralel 3 radar DexScreener (Boosts Latest + Boosts Top + Profiles Latest).
    2. Ambil data pair seluruh 25 kandidat sekaligus dalam 1 Batch HTTP Request!
    3. Saring 25 koin di RAM (< 1ms) dengan Corridor + Anti-Pucuk + Proyeksi Delay 22s.
    4. Hanya kandidat elit yang lolos disimulasikan paralel ke RugCheck & Round-Trip Jupiter!
    """
    t0 = time.time()
    print("[VERCEL-SCAN] ⚡ Memulai Fast-Batch 25-Mint Delay-Compensated Scan...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as feed_exec:
        f_b_latest = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-boosts/latest/v1", 2.8)
        f_b_top = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-boosts/top/v1", 2.8)
        f_prof = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-profiles/latest/v1", 2.8)
        f_organic = feed_exec.submit(fetch_json, "https://api.dexscreener.com/latest/dex/search?q=pumpswap", 2.8)
        boosts_latest = f_b_latest.result()
        boosts_top = f_b_top.result()
        profiles = f_prof.result()
        organic_search = f_organic.result()

    ordered_mints = []
    boost_map = {}

    # Prioritaskan koin organik PumpSwap yang sedang aktif bergerak di rentang Lorong ($25k-$650k) meskipun tanpa iklan Boost!
    if isinstance(organic_search, dict):
        for op in (organic_search.get("pairs") or []):
            if op.get("chainId") == "solana":
                op_mc = float(op.get("marketCap") or op.get("fdv") or 0.0)
                op_vol5 = float((op.get("volume") or {}).get("m5") or 0.0)
                op_addr = (op.get("baseToken") or {}).get("address")
                if op_addr and 25_000.0 <= op_mc <= 650_000.0 and op_vol5 >= 1500.0:
                    ordered_mints.append(op_addr)

    for feed in (boosts_latest, boosts_top):
        if isinstance(feed, list):
            for b in feed:
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
        if m not in seen:
            seen.add(m)
            candidate_mints.append(m)
            if len(candidate_mints) >= BATCH_CANDIDATE_LIMIT:
                break

    if not candidate_mints:
        return {
            "status": "scan_completed",
            "scan_duration_ms": int((time.time() - t0) * 1000),
            "candidates_evaluated": 0,
            "alerts_dispatched": 0,
            "evaluations": []
        }

    # SINGLE-SHOT BATCH REQUEST: Ambil seluruh 25 token hanya dalam 1 kali HTTP call (~250ms)!
    batch_url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(candidate_mints)}"
    batch_dex = fetch_json(batch_url, timeout=3.2)
    all_pairs = (batch_dex or {}).get("pairs") or []

    pairs_by_mint = {}
    for p in all_pairs:
        if p.get("chainId") != "solana":
            continue
        base_addr = (p.get("baseToken") or {}).get("address")
        if base_addr:
            pairs_by_mint.setdefault(base_addr, []).append(p)

    evaluations = []
    stage2_jobs = []
    active_ledger = _load_corridor_ledger()

    # TAHAP 1 (In-Memory ~0.2ms): Saring seluruh 25 kandidat dengan Corridor & Proyeksi Delay 22s
    for m in candidate_mints:
        m_pairs = pairs_by_mint.get(m)
        if not m_pairs:
            evaluations.append({"token": m[:8], "mint": m, "verdict": "SKIP", "reason": "Data pair DexScreener kosong"})
            continue

        sorted_p = sorted(
            m_pairs,
            key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0.0),
            reverse=True
        )
        best_p = sorted_p[0]
        active_cnt = sum(1 for x in sorted_p if float((x.get("liquidity") or {}).get("usd") or 0.0) >= 4000.0)
        t_name = best_p.get("baseToken", {}).get("name", "Unknown")
        t_sym = best_p.get("baseToken", {}).get("symbol", "SOL")

        ok, reason, metrics = evaluate_market_candidate(
            best_p, pair_count=active_cnt, boost_amount=boost_map.get(m, 0)
        )
        if not ok:
            evaluations.append({
                "token": f"{t_name} (${t_sym})",
                "mint": m,
                "verdict": "SKIP",
                "reason": reason
            })
        else:
            c_key = f"{m}:C{metrics['corridor_id']}"
            if c_key in RECENT_ALERTED_MINTS or c_key in active_ledger:
                evaluations.append({
                    "token": f"{t_name} (${t_sym})",
                    "mint": m,
                    "verdict": "SKIP",
                    "reason": f"Cooldown aktif untuk Lorong {metrics['corridor_id']} (anti duplikasi sinyal)"
                })
            else:
                stage2_jobs.append((m, sorted_p, best_p, metrics))

    # TAHAP 2 (Paralel Eksklusif): Hanya kandidat yang lolos Lorong Aman diaudit ke RugCheck & Jupiter Round-Trip
    alerts_sent = 0
    if stage2_jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(stage2_jobs))) as executor:
            future_map = {
                executor.submit(audit_and_dispatch_candidate, m, s_pairs, b_pair, met, config): m
                for (m, s_pairs, b_pair, met) in stage2_jobs
            }
            for future in concurrent.futures.as_completed(future_map):
                m = future_map[future]
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
                        "reason": f"Evaluasi audit gagal: {exc}"
                    })

    elapsed_ms = int((time.time() - t0) * 1000)
    print(
        f"[VERCEL-SCAN] ✅ Batch Scan 25-Mint selesai dalam {elapsed_ms}ms: "
        f"{len(candidate_mints)} disaring ({len(stage2_jobs)} masuk audit dalam), {alerts_sent} alert.",
        flush=True
    )
    return {
        "status": "scan_completed",
        "scan_duration_ms": elapsed_ms,
        "candidates_evaluated": len(candidate_mints),
        "deep_audited_count": len(stage2_jobs),
        "alerts_dispatched": alerts_sent,
        "evaluations": evaluations
    }


class handler(BaseHTTPRequestHandler):
    """Vercel Python Serverless Handler — V2 Delay-Compensated Corridor & Round-Trip Shield."""

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

        # Validasi opsional CRON_SECRET jika diset di Vercel Environment Variables
        cron_secret = (getattr(config, "CRON_SECRET", "") or os.getenv("CRON_SECRET", "")).strip()
        if is_cron and cron_secret:
            auth_header = (self.headers.get("Authorization") or self.headers.get("authorization") or "").strip()
            if auth_header != f"Bearer {cron_secret}":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized cron invocation"}).encode("utf-8"))
                return

        if action == "health":
            bot_token = getattr(config, "TELEGRAM_BOT_TOKEN", None) or os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = getattr(config, "TELEGRAM_CHAT_ID", None) or os.getenv("TELEGRAM_CHAT_ID")
            ledger = _load_corridor_ledger()
            response_data = {
                "service": "Corridor Sniper V2.1 — Production Hardened",
                "status": "healthy",
                "python_version": sys.version.split()[0],
                "engine_loaded": engine is not None,
                "smart_money_whales": len(TOP_100_WALLETS) if engine and TOP_100_WALLETS else 0,
                "verified_insiders": len(INSIDER_WALLETS) if engine and INSIDER_WALLETS else 0,
                "telegram_configured": bool(bot_token and chat_id),
                "cron_secret_protected": bool(cron_secret),
                "active_corridor_cooldowns": len(ledger),
                "parameters": {
                    "batch_candidate_limit": BATCH_CANDIDATE_LIMIT,
                    "pipeline_reaction_delay_sec": PIPELINE_REACTION_DELAY_SEC,
                    "max_roundtrip_loss_pct": MAX_ROUNDTRIP_LOSS_PCT,
                    "min_net_profit_after_delay_pct": MIN_NET_PROFIT_AFTER_DELAY_PCT,
                    "alert_cooldown_sec": ALERT_COOLDOWN_SEC
                },
                "timestamp": time.time()
            }

        elif action == "leaderboard":
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
                    "🔔 *[CORRIDOR SNIPER V2: DELAY-PROOF & ROUND-TRIP AKTIF]*\n\n"
                    "✅ Mesin Batch 25-Token Berkecepatan Tinggi terhubung ke Telegram!\n\n"
                    "Perlindungan Eksekusi V2:\n"
                    "1. ⏱️ *22s Delay Projection:* Menghitung pergerakan harga selama jeda scan -> Telegram -> klik jari Anda, lengkap dengan `🚧 BATAS MAKSIMAL BOLEH BELI`.\n"
                    "2. 🔄 *0.1 SOL Round-Trip Swap:* Simulasi Beli -> Jual nyata di slot terbaru Jupiter untuk memblokir Honeypot Jual, Pajak Token-2022, & Kolam DLMM Kosong.\n"
                    "3. 📡 *Live Slot Drift Guard:* Menangkap live rug 5 detik terakhir yang belum terlihat di cache DexScreener.\n"
                    "4. 🛡️ *Anti-Cabal & Saldo Dev <= 2.5%:* Memastikan dev sudah keluar, kolam berisi uang SOL asli >= 32%, dan bebas sindikat Micro-Split."
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
                "service": "Corridor Sniper V2 — Delay-Compensated & Round-Trip Verified Radar",
                "status": "ready",
                "is_cron_call": is_cron,
                "timestamp": time.time(),
                "scan_result": scan_summary,
                "corridor_architecture": {
                    "corridor_0": "$25k - $78k -> TP <= $90k (Front-run tembok $100k)",
                    "deadzone_1_blocked": "$78k - $128k (Tembok Sniper $100k DIBLOKIR)",
                    "corridor_1": "$128k - $168k -> TP <= $190k (Front-run tembok $200k)",
                    "deadzone_2_blocked": "$168k - $228k (Kuburan Dev Receh $200k DIBLOKIR)",
                    "corridor_2_golden": "$228k - $315k -> TP <= $418k (Ruang Kosong Menuju $400k+)",
                    "deadzone_3_blocked": "$315k - $525k (Tembok $450k-$500k DIBLOKIR)",
                    "corridor_3_whale": "$525k - $650k -> TP <= $825k (Front-run tembok $1M)"
                },
                "execution_and_delay_shields": [
                    "Batch 25-Mint Single-Shot Discovery (3x cakupan pasar dalam ~0.8-1.4 detik)",
                    "22-Second Forward Delay Projection + Hard MAX ENTRY MC Guard di Telegram",
                    "Live Slot Drift Detector (Jupiter Real-Time vs DexScreener Cache Lag -6% / +12%)",
                    "True 0.1 SOL Round-Trip Swap Simulation (SOL -> Token -> SOL <= 5.5% total loss)",
                    "On-Chain Mint/Freeze/Token-2022 Tax Block + Dev Balance <= 2.5% + Hard-SOL LP >= 32%",
                    "Per-Corridor Persistent Cooldown Ledger (/tmp TTL) & Telegram Markdown Sanitizer"
                ]
            }

        else:
            response_data = {
                "service": "Corridor Sniper V2 — Delay-Compensated & Round-Trip Verified Radar",
                "status": "ready",
                "timestamp": time.time(),
                "endpoints": {
                    "health": "/api/fomo?action=health",
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


def _run_cli_bridge():
    """Bridge lokal agar Express dev server (/server.ts) dapat mengeksekusi handler Python secara langsung."""
    import io
    method = os.getenv("BRIDGE_METHOD", "GET").upper()
    path = os.getenv("BRIDGE_PATH", "/api/fomo")
    headers_raw = os.getenv("BRIDGE_HEADERS", "{}")
    body_bytes = sys.stdin.buffer.read() if method == "POST" else b""

    try:
        hdr_dict = json.loads(headers_raw)
    except Exception:
        hdr_dict = {}

    class _LocalBridgeHandler(handler):
        def __init__(self):
            self.path = path
            self.command = method
            self.headers = hdr_dict
            self.rfile = io.BytesIO(body_bytes)
            self.wfile = io.BytesIO()
            self._status_code = 200
            self._resp_headers = {}

        def send_response(self, code, message=None):
            self._status_code = code

        def send_header(self, keyword, value):
            self._resp_headers[keyword] = value

        def end_headers(self):
            pass

    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        h = _LocalBridgeHandler()
        if method == "POST":
            h.do_POST()
        elif method == "OPTIONS":
            h.do_OPTIONS()
        else:
            h.do_GET()
        out_str = h.wfile.getvalue().decode("utf-8", errors="replace")
        envelope = {"statusCode": h._status_code, "body": out_str}
    except Exception as exc:
        envelope = {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
    finally:
        sys.stdout = real_stdout

    sys.stdout.write(json.dumps(envelope))
    sys.stdout.flush()


if __name__ == "__main__":
    if "--cli-bridge" in sys.argv:
        _run_cli_bridge()
    else:
        print(json.dumps(run_live_market_scan(engine, config), indent=2))
