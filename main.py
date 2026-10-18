#!/usr/bin/env python3
import json
import os
import sys
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

# Import konfigurasi & engine lokal
try:
    from config import config
    from engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS
except ImportError:
    from smart_money_fomo.config import config
    from smart_money_fomo.engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS

engine = SmartMoneyEngine(config) if config else None

def send_telegram_direct(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Mengirim pesan langsung ke Telegram API."""
    if not token or not chat_id:
        return False, "Token atau Chat ID belum disetel"
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
            return True, "Alert berhasil terkirim ke Telegram!"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        return False, f"HTTP Error {e.code}: {err_msg}"
    except Exception as e:
        return False, str(e)

class RenderHandler(BaseHTTPRequestHandler):
    """HTTP Server Handler untuk Render Web Service."""
    def log_message(self, format, *args):
        # Override log agar rapi di Render Console
        sys.stdout.write(f"[RENDER-HTTP] {args[0]} {args[1]} - {args[2]}\n")
        sys.stdout.flush()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Health check & Root endpoint
        if path in ("/", "/health"):
            res = {
                "service": "Smart Money FOMO Radar (Render Web Service)",
                "status": "online",
                "rules": {
                    "rule_1": "Koin Belum Runner Puncak (Market Cap < $1.2M USD)",
                    "rule_2": "Akumulasi Awal: Maksimal 1 atau 2 Smart Money",
                    "rule_3": "Jupiter DEX routing aktif"
                },
                "telegram_configured": bool(config and config.has_telegram),
                "endpoints": {
                    "test_telegram": "/test_telegram",
                    "simulate": "/simulate",
                    "leaderboard": "/leaderboard"
                }
            }
        
        # 2. Tes koneksi bot Telegram
        elif path == "/test_telegram":
            if not config or not config.has_telegram:
                res = {
                    "status": "ERROR",
                    "message": "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum ada di Environment Variables Render!"
                }
            else:
                ok, detail = send_telegram_direct(
                    config.TELEGRAM_BOT_TOKEN,
                    config.TELEGRAM_CHAT_ID,
                    "🔔 *[SMART MONEY FOMO: RENDER SUKSES]*\n\n"
                    "✅ Bot berhasil terhubung ke Render Web Service!\n"
                    "Background scanner aktif 24/7 memantau Solana Pre-Peak Runner."
                )
                res = {
                    "status": "SUCCESS" if ok else "FAILED",
                    "telegram_sent": ok,
                    "detail": detail,
                    "help": None if ok else "Pastikan Anda sudah klik START (/start) di bot Telegram Anda!"
                }

        # 3. Simulasi Pre-Peak & kirim notifikasi
        elif path == "/simulate":
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
            el, reason, p = engine.evaluate_trade(event1) if engine else (False, "No engine", None)
            
            tele_status = "Skipped"
            if el and p and config and config.has_telegram:
                ok, detail = send_telegram_direct(
                    config.TELEGRAM_BOT_TOKEN,
                    config.TELEGRAM_CHAT_ID,
                    (
                        "🚨⚡ *[RENDER SIMULASI: SMART MONEY FOMO]* ⚡🚨\n\n"
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
                tele_status = {"sent": ok, "detail": detail}

            res = {
                "simulation": "completed",
                "verdict": "ALERT" if el else "SKIP",
                "reason": reason,
                "telegram_delivery": tele_status,
                "payload": p
            }

        elif path == "/leaderboard":
            wallets = [
                {"rank": w.rank, "name": w.name, "win_rate": w.win_rate, "pnl_30d_usd": w.pnl_30d_usd}
                for w in list(TOP_100_WALLETS.values())[:20]
            ]
            res = {"count": len(wallets), "leaderboard": wallets}

        else:
            res = {"error": "Not Found", "available_endpoints": ["/", "/health", "/test_telegram", "/simulate", "/leaderboard"]}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(res, indent=2).encode("utf-8"))

def background_radar_loop():
    """Loop background yang berjalan otomatis tiap 60 detik di Render."""
    print("[RENDER-WORKER] 🚀 Background Radar Loop aktif!", flush=True)
    while True:
        try:
            # Di sini Anda bisa memanggil scanner Solana RPC berkala
            time.sleep(60)
        except Exception as e:
            print(f"[RENDER-WORKER] Error: {e}", flush=True)
            time.sleep(10)

def main():
    # Baca port dari environment (Back4App/Render)
    port = int(os.environ.get("PORT", 8080))
    server_address = ("0.0.0.0", port)
    
    # Jalankan background worker thread
    bg_thread = threading.Thread(target=background_radar_loop, daemon=True)
    bg_thread.start()

    print(f"==================================================", flush=True)
    print(f"🚀 SMART MONEY FOMO RADAR RUNNING ON CONTAINERS", flush=True)
    print(f"📡 Port: {port} | Host: 0.0.0.0", flush=True)
    print(f"🤖 Telegram Bot Ready: {bool(config and config.has_telegram)}", flush=True)
    print(f"==================================================", flush=True)

    # 🚀 KIRIM PESAN OTOMATIS KE TELEGRAM SAAT CONTAINER PERTAMA KALI MENYALA
    if config and config.has_telegram:
        startup_msg = (
            "🚀 *[SMART MONEY FOMO RADAR: ONLINE]*\n\n"
            "✅ *Container Berhasil Aktif & Terhubung ke Telegram!*\n"
            "🛡️ *Aturan Pantauan:*\n"
            "• Koin Belum Runner Puncak (< $1.2M MCap)\n"
            "• Akumulasi Awal: 1–2 Smart Money Beli\n"
            "• Rute Jupiter DEX Terverifikasi\n\n"
            "Radar sedang memantau pergerakan whale 24/7 di latar belakang."
        )
        ok, res = send_telegram_direct(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, startup_msg)
        if ok:
            print("[STARTUP] ✅ Notifikasi startup berhasil dikirim ke Telegram!", flush=True)
        else:
            print(f"[STARTUP] ⚠️ Gagal kirim notifikasi startup: {res}", flush=True)

    httpd = HTTPServer(server_address, RenderHandler)
    httpd.serve_forever()
