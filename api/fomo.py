import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

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
    """Kirim pesan langsung ke Telegram API dengan detail diagnostik."""
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
        return False, f"Telegram API HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, str(e)


class handler(BaseHTTPRequestHandler):
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
        is_cron = query.get("cron", ["false"])[0].lower() in ("true", "1")

        if action == "leaderboard":
            wallets = [
                {
                    "rank": w.rank, "name": w.name, "address": w.address,
                    "win_rate": w.win_rate, "avg_peak_gain": w.avg_peak_gain,
                    "pnl_30d_usd": w.pnl_30d_usd, "tag": w.tag
                }
                for w in list(TOP_100_WALLETS.values())[:30]
            ]
            insiders = [
                {
                    "handle": iw.handle, "alias": iw.alias, "address": iw.address,
                    "category": iw.category, "known_hits": iw.known_hits,
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

        elif action in ("test_telegram", "test_tele"):
            if not config or not config.has_telegram:
                response_data = {
                    "test": "test_telegram",
                    "status": "NOT_CONFIGURED",
                    "message": "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum ada di Vercel Environment Variables."
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
                    "diagnostic_detail": detail
                }

        elif action == "simulate":
            test_cases = [
                {
                    # KASUS NYATA: @ZypherHQ Beli $1.400 di MC $64k pada $VEIL
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

                if tc.get("is_priority_tele") and el and p:
                    if config and config.has_telegram:
                        sent_ok = engine.send_telegram_alert(p)
                        telegram_send_status = {"attempted": True, "sent": sent_ok}
                    else:
                        telegram_send_status = {"attempted": False, "sent": False, "detail": "Telegram belum terkonfigurasi"}

            response_data = {
                "simulation": "completed",
                "telegram_delivery": telegram_send_status,
                "results": results
            }
        else:
            response_data = {
                "service": "Smart Money FOMO & Insider Alpha Radar (Vercel Serverless)",
                "status": "ready",
                "is_cron_call": is_cron,
                "timestamp": time.time(),
                "active_modules": {
                    "module_1_smart_money": "Top 100 Whitelisted Wallets (Winrate >= 75% atau Gain > +500%)",
                    "module_2_pre_peak": f"Koin Belum Runner Puncak (< ${config.MAX_RUNNER_MCAP_USD:,.0f} USD)",
                    "module_3_early_accumulation": f"Maksimal {config.MAX_SMART_MONEY_INFLOW} Whale Tahap Awal",
                    "module_4_jupiter_route": "Jupiter DEX Active Routing (jup.ag)",
                    "module_5_insider_radar": "Verified Insider Alpha (@ZypherHQ Case: MC $25k-$250k Sweet Spot, Min $1,000 Buy)",
                    "module_6_onchain_safety": "Solana JSON-RPC On-Chain Guard (Mint & Freeze Authority Revoked)"
                },
                "telegram_configured": config.has_telegram if config else False,
                "endpoints": {
                    "root": "/",
                    "test_telegram": "/api/fomo?action=test_telegram",
                    "simulate": "/api/fomo?action=simulate",
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
