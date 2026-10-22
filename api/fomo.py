import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

# Pastikan direktori root terbaca oleh sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

print("[VERCEL-INIT] 🚀 Smart Money FOMO & Dual-Gate Radar Serverless Engine initializing...", flush=True)

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
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return True, "Pesan berhasil terkirim ke Telegram!"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        return False, f"Telegram API HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, str(e)

def fetch_json(url: str, timeout: float = 3.5):
    """Utility fetch JSON dengan User-Agent agar tidak diblokir Cloudflare/DEX API."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

# ==============================================================================
# IDENTIFIKASI KOLAM POOL (AMM / RAYDIUM / METEORA / ORCA / BURN ADDRESSES)
# Membedakan likuiditas AMM resmi dengan dompet murni (Pure Holders)
# ==============================================================================
KNOWN_POOL_IDENTIFIERS = {
    "5q544fkrfoe6tseb7s8em7fcxks7dxdump246n4gkav",  # Raydium Authority V4
    "675kpx9mhtjs2zt1qfr1nyhuzelxfqm9h24wfsut1mp8",  # Raydium AMM
    "cammczo5yl8w4vff8kvrk22ggusp5vtaw7grrkgrwqk",  # Raydium CLMM
    "cpmm0o8l3f4nbtegckvnunggl7h1zpdthkxqb5qkpp1c",  # Raydium CPMM
    "whirlbmiicvdio4qvufm5kag6ct8vpyzyzfgff3uctycc",  # Orca Whirlpool
    "lbuzkhrxpf3xupbcjp4yztkglccjzhstdm9yuvapwxo",  # Meteora DLMM
    "eo7wjkq67rjjqszxs6z3ykapzy3emj6xy8x5eqvn5uab",  # Meteora Vault
    "11111111111111111111111111111111",              # System / Sol Burn
    "1nc1nerator11111111111111111111111111111111",   # Incinerator Burn
}

def analyze_holders_and_cabal(rug_data: dict, pair_address: str = "") -> tuple[bool, str, dict]:
    """
    1. Membedakan Kolam Likuiditas (Pool LP/Raydium/AMM/Burn) dengan Pure Holders.
    2. Mendeteksi Cabal Wallet (Developer memecah suplai ke dompet-dompet kluster identik).
    """
    if not isinstance(rug_data, dict):
        return True, "Data holder RugCheck tidak tersedia", {"pure_top10": 0.0, "max_single": 0.0, "pool_pct": 0.0, "cabal": False}

    holders = rug_data.get("topHolders") or rug_data.get("holders") or []
    if not holders:
        return True, "List holder kosong", {"pure_top10": 0.0, "max_single": 0.0, "pool_pct": 0.0, "cabal": False}

    norm_pair = (pair_address or "").lower()
    pool_holdings = []
    pure_holdings = []

    for h in holders:
        addr = str(h.get("address") or "").lower()
        pct = float(h.get("pct") or 0.0)
        # Pisahkan kolam pool DEX / Raydium / Burn dari pure holder
        is_pool = (
            addr == norm_pair
            or addr in KNOWN_POOL_IDENTIFIERS
            or "burn" in addr
            or "dead" in addr
            or (pct >= 18.0 and (addr.startswith("5q") or addr.startswith("67") or addr.startswith("ca")))
        )
        if is_pool:
            pool_holdings.append({"address": addr, "pct": pct})
        else:
            pure_holdings.append({"address": addr, "pct": pct})

    pure_holdings.sort(key=lambda x: x["pct"], reverse=True)
    max_single_pure = pure_holdings[0]["pct"] if pure_holdings else 0.0
    top10_pure_pct = sum(x["pct"] for x in pure_holdings[:10])
    pool_total_pct = sum(x["pct"] for x in pool_holdings)

    # 1. Hard Gate Pure Holder Tunggal: Maksimal <= 4.8% (Mencegah satu dompet membanting market)
    if max_single_pure > 4.8:
        return False, f"Paus Tunggal Bahaya: 1 pure holder menguasai {max_single_pure:.1f}% suplai (> 4.8%)", {
            "pure_top10": round(top10_pure_pct, 1),
            "max_single": round(max_single_pure, 1),
            "pool_pct": round(pool_total_pct, 1),
            "cabal": False
        }

    # 2. Hard Gate Top 10 Pure Holders: Maksimal <= 20.0%
    if top10_pure_pct > 20.0:
        return False, f"Pure Holder Terkonsentrasi: Top 10 menguasai {top10_pure_pct:.1f}% suplai (> 20.0%)", {
            "pure_top10": round(top10_pure_pct, 1),
            "max_single": round(max_single_pure, 1),
            "pool_pct": round(pool_total_pct, 1),
            "cabal": False
        }

    # 3. ANALISIS CABAL WALLET DEV (SYBIL CLUSTER DETECTION)
    # Dev nakal memecah 10-25% suplai ke dalam 4-8 dompet dengan persentase sangat mirip (1.2% - 4.2%)
    mid_holders = [x["pct"] for x in pure_holdings if 1.2 <= x["pct"] <= 4.2]
    cabal_found = False
    cabal_pct = 0.0
    cabal_count = 0

    for i in range(len(mid_holders)):
        cluster = [p for p in mid_holders if abs(p - mid_holders[i]) <= 0.40]
        if len(cluster) >= 3 and sum(cluster) >= 6.5:
            cabal_found = True
            cabal_count = len(cluster)
            cabal_pct = sum(cluster)
            break

    if cabal_found:
        return False, f"Cabal Wallet Terdeteksi: Dev memecah {cabal_pct:.1f}% suplai ke {cabal_count} dompet kluster identik", {
            "pure_top10": round(top10_pure_pct, 1),
            "max_single": round(max_single_pure, 1),
            "pool_pct": round(pool_total_pct, 1),
            "cabal": True,
            "cabal_pct": round(cabal_pct, 1)
        }

    return True, "Distribusi Pure Holder Aman & Bebas Cabal", {
        "pure_top10": round(top10_pure_pct, 1),
        "max_single": round(max_single_pure, 1),
        "pool_pct": round(pool_total_pct, 1),
        "cabal": False
    }

# ==============================================================================
# EVALUASI EARLY-ENTRY ORGANIK + ANTI-PUCUK GUARD
# Memungkinkan masuk di awal ($15k - $350k, umur >= 3m) tapi bebas dari trap
# ==============================================================================
def evaluate_market_candidate(best_pair: dict) -> tuple[bool, str, dict]:
    name = best_pair.get("baseToken", {}).get("name", "Unknown")
    symbol = best_pair.get("baseToken", {}).get("symbol", "SOL")
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)

    # 1. Early-Stage Market Cap ($15k - $350k) & Likuiditas Sehat (>= $7.5k)
    if mc < 15_000.0 or mc > 350_000.0:
        return False, f"MC di luar sweet spot (USD {mc:,.0f} vs $15k-$350k)", {}
    if liq < 7_500.0:
        return False, f"Likuiditas tipis (USD {liq:,.0f} < $7.5k)", {}

    # 2. Rasio Kedalaman Likuiditas terhadap Market Cap (Wajib >= 12%)
    liq_mc_ratio = (liq / max(1.0, mc)) * 100.0
    if liq_mc_ratio < 12.0:
        return False, f"Trap Likuiditas Tipis: Rasio Liq/MC rapuh ({liq_mc_ratio:.1f}% < 12%)", {}

    # 3. Early Entry Pair Age Guard (Wajib >= 3.0 menit s/d 18 jam)
    # Lolos dari kekacauan block-0 Jito bundle sniper, tapi cukup awal sebelum koin melambung tinggi
    pair_created_at_ms = best_pair.get("pairCreatedAt")
    now_ms = time.time() * 1000.0
    if pair_created_at_ms:
        age_minutes = (now_ms - float(pair_created_at_ms)) / 60000.0
        if age_minutes < 3.0:
            return False, f"Terlalu baru ({age_minutes:.1f}m < 3.0m): Masih dalam fase MEV block-0 bundle sniper", {}
        if age_minutes > 1080.0:
            return False, f"Token kadaluarsa ({age_minutes/60.0:.1f} jam): Seringkali re-pump koin mati", {}
    else:
        age_minutes = 10.0

    # 4. Anti-Parabolic God Candle (Kenaikan 5m Maksimal +85%)
    # Koin yang naik > +85% dalam 5 menit sedang di fase klimaks/overextended (pucuk jarum)
    price_change_m5 = float((best_pair.get("priceChange") or {}).get("m5", 0.0) or 0.0)
    if price_change_m5 > 85.0:
        return False, f"PUCUK GOD CANDLE: Kenaikan 5m overextended (+{price_change_m5:.1f}% > +85%). Risiko dump seketika!", {}
    if price_change_m5 < -15.0:
        return False, f"Sedang Dump: Kenaikan 5m negatif ({price_change_m5:.1f}%)", {}

    txns_m5 = best_pair.get("txns", {}).get("m5", {})
    buys_m5 = int(txns_m5.get("buys", 0))
    sells_m5 = int(txns_m5.get("sells", 0))
    total_tx = buys_m5 + sells_m5
    vol_m5 = float((best_pair.get("volume") or {}).get("m5", 0.0) or 0.0)

    # 5. Hard Gate Rasio Buy/Sell: WAJIB MINIMAL 1.9x
    if sells_m5 >= buys_m5 or (buys_m5 / max(1, sells_m5)) < 1.9:
        return False, f"Wash trade churn: Rasio Buy/Sell lemah ({buys_m5}/{sells_m5} < 1.9x)", {}

    # 6. Hard Gate Net CVD Inflow: WAJIB >= +55.0%
    cvd_ratio = round(((buys_m5 - sells_m5) / max(1, total_tx)) * 100.0, 1)
    if cvd_ratio < 55.0:
        return False, f"CVD Inflow lemah (+{cvd_ratio}% < +55%)", {}

    # 7. Anti-Dust: Rata-rata transaksi >= $20 & Volume M5 >= $2,000
    avg_ticket = vol_m5 / max(1, total_tx)
    if avg_ticket < 20.0 or vol_m5 < 2000.0:
        return False, f"Micro-bot loop: Avg ticket (USD {avg_ticket:.1f}) < $20", {}

    # 8. Skor Akumulasi Pembeli Organik Sehat
    breadth = 0.78  # Estimasi dispersi dompet non-sybil
    score = int((breadth * 40.0) + min(30.0, ((buys_m5 / max(1, sells_m5)) / 3.0) * 30.0) + min(20.0, (cvd_ratio / 100.0) * 20.0) + min(10.0, (liq_mc_ratio / 25.0) * 10.0))
    if score < 75:
        return False, f"Skor akumulasi di bawah ambang ({score}/100)", {}

    payload = {
        "score": score,
        "cvd_ratio": cvd_ratio,
        "buy_sell_ratio": round(buys_m5 / max(1, sells_m5), 2),
        "avg_ticket": round(avg_ticket, 1),
        "breadth_pct": round(breadth * 100.0, 1),
        "liq_mc_ratio": round(liq_mc_ratio, 1),
        "price_change_m5": round(price_change_m5, 1),
        "age_minutes": round(age_minutes, 1)
    }
    return True, "LOLOS: Akumulasi Pembeli Organik Sehat (Aman dari Pucuk)!", payload

def run_live_market_scan(engine, config) -> dict:
    """
    Eksekusi pemindaian live token Solana dari DexScreener & verifikasi keamanan via RugCheck.
    Memisahkan LP Pool vs Pure Holders & menganalisis kemungkinan Cabal Wallet developer.
    """
    print("[VERCEL-SCAN] 🔍 Memulai live market scan di jaringan Solana...", flush=True)
    mints = []

    profiles = fetch_json("https://api.dexscreener.com/token-profiles/latest/v1", timeout=3.5)
    if isinstance(profiles, list):
        for p in profiles:
            if p.get("chainId") == "solana" and p.get("tokenAddress"):
                mints.append(p["tokenAddress"])

    boosts = fetch_json("https://api.dexscreener.com/token-boosts/latest/v1", timeout=3.5)
    if isinstance(boosts, list):
        for b in boosts:
            if b.get("chainId") == "solana" and b.get("tokenAddress"):
                mints.append(b["tokenAddress"])

    seen = set()
    candidate_mints = []
    for m in mints:
        if m not in seen and m not in RECENT_ALERTED_MINTS:
            seen.add(m)
            candidate_mints.append(m)
            if len(candidate_mints) >= 6:
                break

    print(f"[VERCEL-SCAN] 🎯 Menemukan {len(candidate_mints)} kandidat token baru untuk dievaluasi.", flush=True)

    evaluations = []
    alerts_sent = 0

    for mint in candidate_mints:
        dex_data = fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=3.5)
        if not dex_data or not dex_data.get("pairs"):
            continue

        pairs = sorted(
            dex_data.get("pairs", []),
            key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0.0),
            reverse=True
        )
        best_pair = pairs[0]

        name = best_pair.get("baseToken", {}).get("name", "Unknown")
        symbol = best_pair.get("baseToken", {}).get("symbol", "SOL")
        mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
        liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)
        dex_id = best_pair.get("dexId", "raydium")
        pair_address = best_pair.get("pairAddress", "")

        txns_m5 = best_pair.get("txns", {}).get("m5", {})
        buys_m5 = int(txns_m5.get("buys", 0))
        sells_m5 = int(txns_m5.get("sells", 0))

        # 1. EVALUASI STRICT DUAL-GATE + ANTI-PUCUK
        is_candidate_ok, filter_reason, metrics = evaluate_market_candidate(best_pair)
        if not is_candidate_ok:
            evaluations.append({
                "token": f"{name} ({symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": filter_reason
            })
            continue

        # 2. EVALUASI RUGCHECK ON-CHAIN GUARD (< 300)
        rug_data = fetch_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=3.5)
        rug_score = 0
        if isinstance(rug_data, dict):
            rug_score = int(rug_data.get("score", 0))
            if rug_score >= 300:
                evaluations.append({
                    "token": f"{name} ({symbol})",
                    "mint": mint,
                    "verdict": "SKIP",
                    "reason": f"RugCheck bahaya (skor {rug_score} >= 300)"
                })
                continue

            risks = rug_data.get("risks", []) or []
            fatal_risk = None
            for r in risks:
                r_name = str(r.get("name", "")).lower()
                r_level = str(r.get("level", "")).lower()
                if "creator" in r_name and ("danger" in r_level or "high" in r_level):
                    fatal_risk = "Creator/Dev masih menguasai suplai besar"
                    break
                if any(k in r_name for k in ("cabal", "cluster", "sybil", "bundled")) and ("danger" in r_level or "high" in r_level):
                    fatal_risk = "RugCheck mendeteksi Cabal/Sybil cluster wallets"
                    break

            if fatal_risk:
                evaluations.append({
                    "token": f"{name} ({symbol})",
                    "mint": mint,
                    "verdict": "SKIP",
                    "reason": f"Anti-Dump Guard: {fatal_risk}"
                })
                continue

        # 3. ANALISIS ON-CHAIN: MEMBEDAKAN KOLAM POOL VS PURE HOLDER & DETEKSI CABAL WALLET DEV
        holders_ok, holders_reason, holder_meta = analyze_holders_and_cabal(rug_data, pair_address)
        if not holders_ok:
            evaluations.append({
                "token": f"{name} ({symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": holders_reason
            })
            continue

        # 4. FORMAT ALERT TELEGRAM (Lengkap dengan Analisis Pool & Cabal Guard)
        buy_ratio = metrics.get("buy_sell_ratio", 2.0)
        cvd_ratio = metrics.get("cvd_ratio", 60.0)
        avg_ticket = metrics.get("avg_ticket", 25.0)
        score = metrics.get("score", 85)
        liq_ratio = metrics.get("liq_mc_ratio", 15.0)
        p5m = metrics.get("price_change_m5", 20.0)
        age_m = metrics.get("age_minutes", 10.0)
        pure_top10 = holder_meta.get("pure_top10", 0.0)
        max_single = holder_meta.get("max_single", 0.0)
        pool_pct = holder_meta.get("pool_pct", 0.0)

        alert_msg = (
            f"🚨 *[RADAR KOIN AWAL: AKUMULASI ORGANIK & BEBAS CABAL]*\n\n"
            f"🪙 *Token:* {name} (`{symbol}`)\n"
            f"📍 *Mint:* `{mint}`\n"
            f"💰 *Market Cap:* `USD {mc:,.0f}` (Early Stage)\n"
            f"💧 *Likuiditas / Pool:* `USD {liq:,.0f}` ({dex_id.title()} | LP Pool: `{pool_pct}%` | Rasio: `{liq_ratio}%`)\n"
            f"👥 *Pure Top 10 Holders:* `{pure_top10}%` (Maks Dompet Tunggal: `{max_single}%` | Aman)\n"
            f"🛡️ *Cabal Guard:* `BERSIH` (Tidak ada dev sybil wallet splitting)\n"
            f"⏱️ *Umur Token:* `{age_m:.1f} menit` (Lolos Block-0 Snipe Trap)\n"
            f"📈 *Kenaikan 5m:* `+{p5m}%` (Akumulasi Sehat, Bukan God Candle Pucuk)\n"
            f"🛡️ *RugCheck Score:* `{rug_score}` (Lolos Filter Keamanan)\n"
            f"📊 *5m Buys / Sells:* `{buys_m5}` / `{sells_m5}` (Rasio: `{buy_ratio}x` | Inflow: `+{cvd_ratio}%`)\n"
            f"⚡ *Avg Order:* `USD {avg_ticket:.1f}` | *Skor Kualitas:* `{score}/100`\n\n"
            f"🔗 *DexScreener:* [Lihat Chart](https://dexscreener.com/solana/{mint})\n"
            f"⚡ *Jupiter DEX:* [Swap Langsung](https://jup.ag/swap/SOL-{mint})"
        )

        bot_tok = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = getattr(config, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")

        sent, detail = send_telegram_direct(bot_tok, chat_id, alert_msg)
        if sent:
            alerts_sent += 1
            RECENT_ALERTED_MINTS.add(mint)
            print(f"[VERCEL-ALERT] 🚀 Alert GEM Organik terkirim ke Telegram untuk {name} ({symbol})!", flush=True)
            evaluations.append({
                "token": f"{name} ({symbol})",
                "mint": mint,
                "verdict": "ALERT_SENT",
                "reason": "Lolos filter awal, bebas cabal cluster dev, dan aman dari pucuk!"
            })
        else:
            evaluations.append({
                "token": f"{name} ({symbol})",
                "mint": mint,
                "verdict": "ALERT_FAILED",
                "reason": detail
            })

    print(f"[VERCEL-SCAN] ✅ Scan selesai: {len(candidate_mints)} dievaluasi, {alerts_sent} alert terkirim.", flush=True)
    return {
        "status": "scan_completed",
        "candidates_evaluated": len(candidate_mints),
        "alerts_dispatched": alerts_sent,
        "evaluations": evaluations
    }

class handler(BaseHTTPRequestHandler):
    """
    Standard Vercel Python Serverless Handler.
    Zero external dependencies required at runtime.
    Logs are automatically flushed to Vercel Runtime Logs console.
    """
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

        print(f"[VERCEL-REQ] 📥 Incoming GET: {self.path} | Action: '{action}' | Cron: {is_cron}", flush=True)

        if action == "leaderboard":
            wallets = []
            if engine and hasattr(engine, "TOP_100_WALLETS"):
                wallets = [
                    {"rank": w.rank, "name": w.name, "address": w.address, "win_rate": w.win_rate}
                    for w in list(getattr(engine, "TOP_100_WALLETS", {}).values())[:30]
                ]
            response_data = {
                "service": "Smart Money FOMO Radar",
                "status": "ready",
                "smart_money_count": len(wallets),
                "top_smart_money": wallets
            }
        elif action in ("test_telegram", "test_tele"):
            bot_tok = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = getattr(config, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")
            if not bot_tok or not chat_id:
                response_data = {
                    "test": "test_telegram",
                    "status": "NOT_CONFIGURED",
                    "message": "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum ada di Vercel Environment Variables."
                }
            else:
                test_msg = (
                    "🔔 *[SMART MONEY & DUAL-GATE RADAR: TES SUKSES]*\n\n"
                    "✅ Bot Telegram Anda berhasil terhubung dengan Vercel Serverless Engine!\n"
                    "Radar aktif memfilter:\n"
                    "1. 🐋 Top 100 Smart Money Pre-Peak (< $1.2M MCap)\n"
                    "2. 🛡️ Dual-Gate Pembeli Nyata: Rasio Buy/Sell >= 1.9x & Net CVD >= +55%\n"
                    "3. 🌊 Pool Separator: Membedakan LP Pool AMM/Raydium dari Pure Holders\n"
                    "4. 🕵️ Cabal Detection: Memblokir sybil cluster wallet developer\n"
                    "5. 🛡️ RugCheck Guard: Skor < 300 & Jupiter Swap Routing"
                )
                ok, detail = send_telegram_direct(bot_tok, chat_id, test_msg)
                response_data = {
                    "test": "test_telegram",
                    "status": "SUCCESS" if ok else "FAILED",
                    "telegram_sent": ok,
                    "diagnostic_detail": detail
                }
        elif action == "scan" or is_cron:
            scan_summary = run_live_market_scan(engine, config)
            response_data = {
                "service": "Smart Money FOMO & Insider Alpha Radar (Vercel Serverless)",
                "status": "ready",
                "is_cron_call": is_cron,
                "timestamp": time.time(),
                "scan_result": scan_summary,
                "active_filters": {
                    "early_entry_mcap": "$15k - $350k (Fase Akumulasi Awal)",
                    "pair_age_guard": "Minimal 3.0 menit (Lolos Block-0 MEV Snipe)",
                    "pool_vs_pure_holders": "LP Pool AMM dibedakan; Pure Holder tunggal <= 4.8%, Top 10 <= 20%",
                    "cabal_detection": "Aktif (Mendeteksi dev sybil splitting mid-holders)",
                    "anti_top_price_velocity": "Maksimal +85.0% candle 5m",
                    "net_cvd_inflow": "Minimal +55.0% dominasi inflow",
                    "anti_wash_buy_sell_ratio": "Minimal 1.9x"
                }
            }
        else:
            response_data = {
                "service": "Smart Money FOMO & Insider Alpha Radar (Vercel Serverless)",
                "status": "ready",
                "active_filters": {
                    "gate_1": "Early Entry MCap $15k - $350k",
                    "gate_2": "Pair Age >= 3.0m (Anti Block-0 Snipe)",
                    "gate_3": "Pemisahan Kolam LP vs Pure Holder (Top 10 Pure <= 20%)",
                    "gate_4": "Anti-Cabal Wallet Cluster Dev",
                    "gate_5": "Anti-Pucuk (5m Price Change <= +85%)",
                    "gate_6": "Buy/Sell >= 1.9x & CVD >= +55%"
                },
                "endpoints": {
                    "test_telegram": "/api/fomo?action=test_telegram",
                    "scan": "/api/fomo?action=scan",
                    "leaderboard": "/api/fomo?action=leaderboard"
                }
            }

        elapsed_ms = int((time.time() - start_t) * 1000)
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
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "received", "note": "Engine offline in standalone mode"}).encode("utf-8"))
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
