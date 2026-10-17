import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

# Tambahkan root path agar config dan engine terbaca
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

print("[VERCEL-INIT] 🚀 Smart Money FOMO Serverless Engine Siap!", flush=True)

try:
    from config import config
    from engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS
    engine = SmartMoneyEngine(config)
except Exception as e:
    print(f"[VERCEL-INIT] ❌ Error loading engine: {e}", flush=True)
    engine = None

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

        print(f"[VERCEL-REQ] 📥 GET: {self.path} | Cron: {is_cron}", flush=True)

        if action == "leaderboard" and engine:
            wallets = [
                {
                    "rank": w.rank, "name": w.name, "address": w.address,
                    "win_rate": w.win_rate, "avg_peak_gain": w.avg_peak_gain,
                    "pnl_30d_usd": w.pnl_30d_usd, "tag": w.tag
                }
                for w in list(TOP_100_WALLETS.values())[:30]
            ]
            response_data = {"count": len(wallets), "leaderboard": wallets}
        elif action == "simulate" and engine:
            # Uji coba skenario Pre-Peak (< $1.2M MCap) & 1-2 Whale
            event1 = TradeEvent(
                wallet="HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4ZkP",
                token_mint="CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
                token_symbol="GOAT", amount_usd=3500.0, token_amount=150000.0,
                entry_price_usd=0.021, current_price_usd=0.021,
                market_cap_usd=85000.0, price_drift_pct=2.4,
                signature=f"sig-{int(time.time()*1000)}"
            )
            el, reason, p = engine.evaluate_trade(event1)
            print(f"[VERCEL-SIM] Evaluasi Simulasi: {reason}", flush=True)
            response_data = {
                "simulation": "completed",
                "test_case": "Whale #1 Pre-Peak Entry ($85k MC, Jupiter)",
                "verdict": "ALERT" if el else "SKIP",
                "reason": reason,
                "payload": p
            }
        else:
            response_data = {
                "service": "Smart Money FOMO Engine (Vercel Serverless)",
                "status": "ready",
                "is_cron_call": is_cron,
                "rules": {
                    "rule_1_pre_peak": "Koin Belum Runner Puncak (Market Cap < $1,200,000 USD)",
                    "rule_2_whale_count": "Akumulasi Awal: Maksimal 1 atau 2 Whale Beli",
                    "rule_3_jupiter": "Jupiter DEX routing aktif (jup.ag)"
                },
                "telegram_configured": config.has_telegram if config else False
            }

        elapsed_ms = int((time.time() - start_t) * 1000)
        print(f"[VERCEL-RES] 🟢 Finished GET in {elapsed_ms}ms (HTTP 200)", flush=True)

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

            trade = TradeEvent(
                wallet=data.get("wallet", ""),
                token_mint=data.get("token_mint", ""),
                token_symbol=data.get("token_symbol", "UNKNOWN"),
                amount_usd=float(data.get("amount_usd", 0.0)),
                token_amount=float(data.get("token_amount", 0.0)),
                entry_price_usd=float(data.get("entry_price_usd", 0.001)),
                current_price_usd=float(data.get("current_price_usd", 0.001)),
                market_cap_usd=float(data.get("market_cap_usd", 150000.0)),
                price_drift_pct=float(data.get("price_drift_pct", 0.0)),
                signature=data.get("signature", f"sig-{int(time.time()*1000)}"),
                is_buy=bool(data.get("is_buy", True))
            )
            print(f"[VERCEL-POST] 📥 Webhook trade masuk: {trade.token_symbol} (${trade.market_cap_usd:,.0f} MC)", flush=True)

            is_eligible, reason, payload = engine.evaluate_trade(trade) if engine else (False, "Engine offline", None)
            telegram_sent = False
            if is_eligible and payload and engine:
                telegram_sent = engine.send_telegram_alert(payload)

            response_data = {
                "success": True,
                "is_eligible": is_eligible,
                "reason": reason,
                "telegram_sent": telegram_sent,
                "payload": payload
            }
            status_code = 200
        except Exception as e:
            print(f"[VERCEL-ERR] ❌ Error POST: {e}", flush=True)
            response_data = {"success": False, "error": str(e)}
            status_code = 400

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(response_data, indent=2).encode("utf-8"))
