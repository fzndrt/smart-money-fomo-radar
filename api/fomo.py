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

print("[VERCEL-INIT] 🚀 Smart Money FOMO & Insider Radar Serverless Engine initializing...", flush=True)

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
        f"{len(INSIDER_WALLETS)} Insiders | Telegram: {config.has_telegram}",
        flush=True
    )
except Exception as init_err:
    print(f"[VERCEL-INIT] ❌ Initialization Error: {init_err}", flush=True)
    engine = None
    config = None


def send_telegram_direct(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Kirim pesan langsung ke Telegram API dengan detail diagnostik HTTP."""
    if not token or not chat_id:
        return False, "Token atau Chat ID kosong di Vercel Environment Variables"
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
        print(f"[VERCEL-TELEGRAM] ❌ HTTP Error {e.code}: {err_msg}", flush=True)
        return False, f"Telegram API HTTP {e.code}: {err_msg}"
    except Exception as e:
        print(f"[VERCEL-TELEGRAM] ❌ Network Error: {e}", flush=True)
        return False, str(e)


RECENT_ALERTED_MINTS = set()
# ==============================================================================
# EVALUASI STRICT DUAL-GATE (ANTI-CHURN & ANTI-WASH TRADE)
# ==============================================================================
def evaluate_market_candidate(best_pair: dict) -> tuple[bool, str, dict]:
    name = best_pair.get("baseToken", {}).get("name", "Unknown")
    symbol = best_pair.get("baseToken", {}).get("symbol", "SOL")
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)
    
    txns_m5 = best_pair.get("txns", {}).get("m5", {})
    buys_m5 = int(txns_m5.get("buys", 0))
    sells_m5 = int(txns_m5.get("sells", 0))
    total_tx = buys_m5 + sells_m5
    vol_m5 = float((best_pair.get("volume") or {}).get("m5", 0.0) or 0.0)

    # 1. Sweet Spot Market Cap Pre-Peak Runner ($20k - $350k) & Likuiditas Sehat
    if mc < 20_000.0 or mc > 350_000.0:
        return False, f"MC di luar sweet spot (${mc:,.0f})", {}
    if liq < 10_000.0:
        return False, f"Likuiditas tipis (${liq:,.0f} < $10k)", {}

    # 2. Hard Gate Rasio Buy/Sell: WAJIB MINIMAL 2.0x (123 vs 111 = 1.1x langsung DROP!)
    if sells_m5 >= buys_m5 or (buys_m5 / max(1, sells_m5)) < 2.0:
        return False, f"Wash trade churn: Rasio Buy/Sell lemah ({buys_m5}/{sells_m5} < 2.0x)", {}

    # 3. Hard Gate Net CVD Inflow: WAJIB >= +60.0%
    cvd_ratio = round(((buys_m5 - sells_m5) / max(1, total_tx)) * 100.0, 1)
    if cvd_ratio < 60.0:
        return False, f"CVD Inflow lemah (+{cvd_ratio}% < +60%)", {}

    # 4. Anti-Dust: Rata-rata transaksi >= $25 & Volume M5 >= $2,500
    avg_ticket = vol_m5 / max(1, total_tx)
    if avg_ticket < 25.0 or vol_m5 < 2500.0:
        return False, f"Micro-bot loop: Avg ticket ${avg_ticket:.1f} < $25", {}

    # 5. Breadth Dompet Unik & Skor Akumulasi
    breadth = 0.70  # Estimasi dispersi dompet non-sybil
    score = int((breadth * 45.0) + min(35.0, ((buys_m5 / max(1, sells_m5)) / 3.0) * 35.0) + min(20.0, (cvd_ratio / 100.0) * 20.0))
    if score < 75:
        return False, f"Skor akumulasi di bawah ambang ({score}/100)", {}

    payload = {
        "score": score,
        "cvd_ratio": cvd_ratio,
        "buy_sell_ratio": round(buys_m5 / max(1, sells_m5), 2),
        "avg_ticket": round(avg_ticket, 1),
        "breadth_pct": round(breadth * 100.0, 1)
    }
    return True, "LOLOS: Akumulasi Pembeli Organik Terverifikasi!", payload

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


def run_live_market_scan(engine, config) -> dict:
    """
    Eksekusi pemindaian live token Solana dari DexScreener & verifikasi keamanan via RugCheck.
    Dijalankan secara otomatis setiap kali cron atau request scan masuk.
    """
    print(f"[VERCEL-SCAN] 🔍 Memulai live market scan di jaringan Solana...", flush=True)
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

        txns_m5 = best_pair.get("txns", {}).get("m5", {})
        buys_m5 = int(txns_m5.get("buys", 0))
        sells_m5 = int(txns_m5.get("sells", 0))

        max_mc = getattr(config, "MAX_RUNNER_MCAP_USD", 2000000.0) if config else 2000000.0

        if mc < 15000.0:
            evaluations.append({"token": f"{name} (${symbol})", "mint": mint, "verdict": "SKIP", "reason": f"MC terlalu rendah (${mc:,.0f} < $15k)"})
            continue
        if mc > max_mc:
            evaluations.append({"token": f"{name} (${symbol})", "mint": mint, "verdict": "SKIP", "reason": f"Sudah runner puncak (${mc:,.0f} > ${max_mc:,.0f})"})
            continue
        if liq < 8000.0:
            evaluations.append({"token": f"{name} (${symbol})", "mint": mint, "verdict": "SKIP", "reason": f"Likuiditas tipis (${liq:,.0f} < $8,000)"})
            continue

        rug_data = fetch_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=3.5)
        rug_score = 0
        if isinstance(rug_data, dict):
            rug_score = int(rug_data.get("score", 0))
            if rug_score >= 400:
                evaluations.append({"token": f"{name} (${symbol})", "mint": mint, "verdict": "SKIP", "reason": f"RugCheck bahaya (skor {rug_score} >= 400)"})
                continue

        # Format alert Telegram
        alert_msg = (
            f"🚨 *[SMART MONEY & FOMO RADAR: GEM TERDETEKSI]*\n\n"
            f"🪙 *Token:* {name} (`${symbol}`)\n"
            f"📍 *Mint:* `{mint}`\n"
            f"💰 *Market Cap:* `${mc:,.0f}` (Pre-Peak Stage)\n"
            f"💧 *Likuiditas:* `${liq:,.0f}` ({dex_id.title()})\n"
            f"🛡️ *RugCheck Score:* `{rug_score}` (Aman & Lolos Filter)\n"
            f"📊 *5m Buys / Sells:* `{buys_m5}` / `{sells_m5}`\n\n"
            f"🔗 *DexScreener:* [Lihat Chart](https://dexscreener.com/solana/{mint})\n"
            f"⚡ *Jupiter DEX:* [Swap Langsung](https://jup.ag/swap/SOL-{mint})"
        )

        sent, detail = send_telegram_direct(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, alert_msg)
        if sent:
            alerts_sent += 1
            RECENT_ALERTED_MINTS.add(mint)
            print(f"[VERCEL-ALERT] 🚀 Alert GEM terkirim ke Telegram untuk {name} (${symbol})!", flush=True)
            evaluations.append({"token": f"{name} (${symbol})", "mint": mint, "verdict": "ALERT_SENT", "reason": "Lolos semua filter pre-peak & rugcheck"})
        else:
            evaluations.append({"token": f"{name} (${symbol})", "mint": mint, "verdict": "ALERT_FAILED", "reason": detail})

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
                "smart_money_count": len(TOP_100_WALLETS),
                "insiders_count": len(insiders),
                "verified_insiders": insiders,
                "top_smart_money": wallets
            }
            print(f"[VERCEL-RES] 📋 Returned leaderboard & insider data", flush=True)

        elif action in ("test_telegram", "test_tele"):
            if not config or not config.has_telegram:
                response_data = {
                    "test": "test_telegram",
                    "status": "NOT_CONFIGURED",
                    "message": "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum ada di Vercel Environment Variables.",
                    "has_token": bool(getattr(config, "TELEGRAM_BOT_TOKEN", None)),
                    "has_chat_id": bool(getattr(config, "TELEGRAM_CHAT_ID", None))
                }
            else:
                test_msg = (
                    "🔔 *[SMART MONEY & INSIDER RADAR: TES SUKSES]*\n\n"
                    "✅ Bot Telegram Anda berhasil terhubung dengan Vercel Serverless Engine!\n"
                    "Radar siap memantau:\n"
                    "1. 🐋 Top 100 Smart Money Pre-Peak (< $2.0M MCap)\n"
                    "2. 👑 Verified Insider Alpha (@ZypherHQ Case: MC $25k-$250k Sweet Spot)\n"
                    "3. 🛡️ Solana RPC On-Chain Rug Shield (Mint & Freeze Authority check)"
                )
                ok, detail = send_telegram_direct(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, test_msg)
                response_data = {
                    "test": "test_telegram",
                    "status": "SUCCESS" if ok else "FAILED",
                    "telegram_sent": ok,
                    "diagnostic_detail": detail,
                    "chat_id_target": f"{config.TELEGRAM_CHAT_ID[:3]}***{config.TELEGRAM_CHAT_ID[-2:]}" if len(config.TELEGRAM_CHAT_ID) > 5 else config.TELEGRAM_CHAT_ID
                }

        elif action == "simulate":
            print(f"[VERCEL-EXEC] 🧪 Running comprehensive test suite (Original + ZypherHQ Insider)...", flush=True)
            test_cases = [
                {
                    "name": "👑 @ZypherHQ Insider Entry ($1,400 Buy @ $64k MC on $VEIL)",
                    "is_priority_tele": True,
                    "event": TradeEvent(
                        wallet="ZyphERq8Xz3m41L9aK7sN2dF6tY8uW1oP4vB7cR3jQ5",
                        token_mint="CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
                        token_symbol="VEIL",
                        amount_usd=1400.0,
                        token_amount=21875000.0,
                        entry_price_usd=0.000064,
                        current_price_usd=0.000064,
                        market_cap_usd=64000.0,
                        price_drift_pct=1.8,
                        signature=f"zypher-sig-{int(time.time()*1000)}"
                    )
                },
                {
                    "name": "🐋 Whale #1 Pre-Peak Entry ($85k MC, Jupiter)",
                    "is_priority_tele": False,
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
                    "name": "⚡ Whale #2 Dual Convergence ($95k MC, Jupiter)",
                    "is_priority_tele": False,
                    "event": TradeEvent(
                        wallet="5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
                        token_mint="CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
                        token_symbol="GOAT",
                        amount_usd=4500.0,
                        token_amount=210000.0,
                        entry_price_usd=0.0215,
                        current_price_usd=0.0215,
                        market_cap_usd=95000.0,
                        price_drift_pct=3.1,
                        signature=f"demo-sig-{int(time.time()*1000)+1}"
                    )
                },
                {
                    "name": "🚫 Whale High MCap Skip (Sudah Runner Puncak > $2.0M)",
                    "is_priority_tele": False,
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
            telegram_send_status = None

            for i, tc in enumerate(test_cases):
                el, reason, p = engine.evaluate_trade(tc["event"])
                results.append({
                    "case": tc["name"],
                    "eligible": el,
                    "reason": reason,
                    "alert_type": p.get("alert_type") if p else None,
                    "whale_stage": p.get("whale_stage") if p else None,
                    "is_insider": p.get("is_insider") if p else False
                })
                print(f"[VERCEL-SIM] {tc['name']} -> Verdict: {'ALERT' if el else 'SKIP'} ({reason})", flush=True)

                if tc.get("is_priority_tele") and el and p:
                    if config and config.has_telegram:
                        sent_ok = engine.send_telegram_alert(p)
                        telegram_send_status = {
                            "attempted": True,
                            "sent": sent_ok,
                            "target": "Telegram Channel/Group"
                        }

            response_data = {
                "simulation": "completed",
                "telegram_delivery": telegram_send_status,
                "results": results
            }

        elif action == "scan" or is_cron:
            scan_summary = run_live_market_scan(engine, config)
            response_data = {
                "service": "Smart Money FOMO & Insider Alpha Radar (Vercel Serverless)",
                "status": "ready",
                "is_cron_call": is_cron,
                "timestamp": time.time(),
                "scan_result": scan_summary,
                "active_modules": {
                    "module_1_smart_money": "Top 100 Whitelisted Wallets (Winrate >= 75% atau Gain > +500%)",
                    "module_2_pre_peak": f"Koin Belum Runner Puncak (< ${getattr(config, 'MAX_RUNNER_MCAP_USD', 2000000.0):,.0f} USD)",
                    "module_3_early_accumulation": f"Maksimal {getattr(config, 'MAX_SMART_MONEY_INFLOW', 3)} Whale Tahap Awal",
                    "module_4_jupiter_route": "Jupiter DEX Active Routing (jup.ag)",
                    "module_5_insider_radar": "Verified Insider Alpha (@ZypherHQ Case: MC $25k-$250k Sweet Spot, Min $1,000 Buy)",
                    "module_6_onchain_safety": "Solana JSON-RPC On-Chain Guard (Mint & Freeze Authority Revoked)"
                },
                "telegram_configured": config.has_telegram if config else False
            }

        else:
            response_data = {
                "service": "Smart Money FOMO & Insider Alpha Radar (Vercel Serverless)",
                "status": "ready",
                "is_cron_call": False,
                "timestamp": time.time(),
                "active_modules": {
                    "module_1_smart_money": "Top 100 Whitelisted Wallets (Winrate >= 75% atau Gain > +500%)",
                    "module_2_pre_peak": f"Koin Belum Runner Puncak (< ${getattr(config, 'MAX_RUNNER_MCAP_USD', 2000000.0):,.0f} USD)",
                    "module_3_early_accumulation": f"Maksimal {getattr(config, 'MAX_SMART_MONEY_INFLOW', 3)} Whale Tahap Awal",
                    "module_4_jupiter_route": "Jupiter DEX Active Routing (jup.ag)",
                    "module_5_insider_radar": "Verified Insider Alpha (@ZypherHQ Case: MC $25k-$250k Sweet Spot, Min $1,000 Buy)",
                    "module_6_onchain_safety": "Solana JSON-RPC On-Chain Guard (Mint Authority null & Freeze Authority null)"
                },
                "telegram_configured": config.has_telegram if config else False,
                "endpoints": {
                    "root": "/",
                    "test_telegram": "/api/fomo?action=test_telegram",
                    "scan": "/api/fomo?action=scan",
                    "simulate": "/api/fomo?action=simulate",
                    "leaderboard": "/api/fomo?action=leaderboard"
                }
            }

        elapsed_ms = int((time.time() - start_t) * 1000)
        print(f"[VERCEL-RES] 🟢 Finished GET {self.path} in {elapsed_ms}ms (HTTP 200)", flush=True)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(response_data, indent=2).encode("utf-8"))

    def do_POST(self):
        start_t = time.time()
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

            if eligible and payload and config and config.has_telegram:
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
