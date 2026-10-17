import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

# Import root modules
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

print("[VERCEL-INIT] 🚀 Smart Money FOMO Serverless Engine initializing...", flush=True)

try:
    try:
        from smart_money_fomo.config import config
        from smart_money_fomo.engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS
    except ImportError:
        from config import config
        from engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS

    engine = SmartMoneyEngine(config)
    print(f"[VERCEL-INIT] ✅ Engine loaded with {len(TOP_100_WALLETS)} Top 100 Whales | Telegram: {config.has_telegram}", flush=True)
except Exception as init_err:
    print(f"[VERCEL-INIT] ❌ Initialization Error: {init_err}", flush=True)
    engine = None
    config = None

def send_telegram_direct(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Kirim pesan langsung ke Telegram API dengan detail diagnostik lengkap."""
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID masih kosong di Environment Variables Vercel"
    
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
            return True, "Pesan alert berhasil terkirim ke Telegram!"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(f"[VERCEL-TELEGRAM] ❌ HTTP Error {e.code}: {err_msg}", flush=True)
        return False, f"Telegram API HTTP {e.code}: {err_msg}"
    except Exception as e:
        print(f"[VERCEL-TELEGRAM] ❌ Network Error: {e}", flush=True)
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

        print(f"[VERCEL-REQ] 📥 GET: {self.path} | Action: '{action}' | Cron: {is_cron}", flush=True)

        if action in ("test_telegram", "test_tele"):
            # Diagnostik langsung untuk mengecek token Telegram
            if not config or not config.has_telegram:
                response_data = {
                    "test": "test_telegram",
                    "status": "NOT_CONFIGURED",
                    "message": "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum aktif di Vercel. Pastikan sudah input di Settings > Environment Variables lalu klik Redeploy!",
                    "has_token": bool(getattr(config, "TELEGRAM_BOT_TOKEN", None)),
                    "has_chat_id": bool(getattr(config, "TELEGRAM_CHAT_ID", None))
                }
            else:
                test_msg = (
                    "🔔 *[SMART MONEY FOMO RADAR: KONEKSI BERHASIL]*\n\n"
                    "✅ Bot Telegram Anda sudah terhubung aktif ke Vercel Serverless!\n"
                    "Radar siap memantau koin Solana Pre-Peak (< $1.2M MCap) & Akumulasi Awal (1-2 Smart Money)."
                )
                ok, detail = send_telegram_direct(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, test_msg)
                response_data = {
                    "test": "test_telegram",
                    "status": "SUCCESS" if ok else "FAILED",
                    "telegram_sent": ok,
                    "diagnostic_detail": detail,
                    "help": None if ok else "Jika error 400/403: Buka bot Anda di Telegram dan klik START (/start) terlebih dahulu!"
                }

        elif action == "simulate":
            # Buat data dummy pre-peak (< $1.2M MCap & Whale #1)
            event1 = TradeEvent(
                wallet="HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4ZkP",
                token_mint="CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
                token_symbol="GOAT",
                amount_usd=3500.0,
                token_amount=150000.0,
                entry_price_usd=0.021,
                current_price_usd=0.021,
                market_cap_usd=85000.0,
                price_drift_pct=2.4,
                signature=f"sig-{int(time.time()*1000)}"
            )
            el, reason, p = engine.evaluate_trade(event1)
            print(f"[VERCEL-SIM] Evaluasi: {reason}", flush=True)

            telegram_status = {"sent": False, "detail": "Telegram belum disetel atau bot belum di-start"}
            if el and p and config and config.has_telegram:
                ok, detail = send_telegram_direct(
                    config.TELEGRAM_BOT_TOKEN,
                    config.TELEGRAM_CHAT_ID,
                    (
                        "🚨⚡ *[SIMULASI: SMART MONEY FOMO RADAR]* ⚡🚨\n\n"
                        f"🪙 **Token:** ${p['token_symbol']}\n"
                        f"📍 **Address:** `{p['token_mint']}`\n"
                        f"📊 **Market Cap:** ${p['market_cap_usd']:,.0f} USD *(Pre-Peak Runner)*\n"
                        f"🌱 **Tahap:** Whale Ke-{p['whale_stage']} Mulai Akumulasi!\n\n"
                        f"👤 **Smart Money:** {p['wallet_name']} (Rank #{p['wallet_rank']})\n"
                        f"🏆 **Win-Rate:** {p['win_rate']}%\n"
                        f"💵 **Nominal:** ${p['amount_usd']:,.2f} USD\n\n"
                        f"🪐 [Beli di Jupiter]({p['jupiter_swap_url']})\n"
                        f"📊 [Chart DexScreener]({p['dexscreener_url']})"
                    )
                )
                telegram_status = {"sent": ok, "detail": detail}

            response_data = {
                "simulation": "completed",
                "verdict": "ALERT" if el else "SKIP",
                "reason": reason,
                "telegram_delivery": telegram_status,
                "payload": p
            }

        else:
            response_data = {
                "service": "Smart Money FOMO Engine",
                "status": "ready",
                "telegram_configured": config.has_telegram if config else False,
                "endpoints": {
                    "test_telegram": "/api/fomo?action=test_telegram",
                    "simulate_alert": "/api/fomo?action=simulate"
                }
            }

        elapsed_ms = int((time.time() - start_t) * 1000)
        print(f"[VERCEL-RES] 🟢 Finished in {elapsed_ms}ms (HTTP 200)", flush=True)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(response_data, indent=2).encode("utf-8"))
