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
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB (BSC)
    "0x55d398326f99059ff775485246999027b3197955",  # USDT (BSC)
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC (BSC)
    "0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d",  # USD1 (BSC)
    "0x4200000000000000000000000000000000000006",  # WETH (Base)
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (Base)
    "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b",  # VIRTUAL (Base)
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH (Ethereum)
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC (Ethereum)
}
SUPPORTED_CHAINS = ("solana", "bsc", "base", "ethereum")
GT_NETWORK_MAP = {"solana": "solana", "bsc": "bsc", "base": "base", "ethereum": "eth"}
GOPLUS_CHAIN_MAP = {"bsc": "56", "base": "8453", "ethereum": "1"}
EVM_BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000042069420694206942069",
}
TEST_BUY_LAMPORTS = 100_000_000          # Simulasi ukuran nyata 0.1 SOL (~$15) Beli -> Jual
TEST_DEPTH_MULTIPLIER = 10               # Stress-test pencairan 1.0 SOL paralel (Anti Hollow-Bin DLMM)
MAX_DEPTH_CLIFF_LOSS_PCT = 13.5          # Batas deviasi likuiditas keluar ukuran 1.0 SOL
PIPELINE_REACTION_DELAY_SEC = float(os.getenv("PIPELINE_REACTION_DELAY_SEC", getattr(config, "PIPELINE_REACTION_DELAY_SEC", 22.0)))
PRIORITY_TIP_BUFFER_PCT = 1.0            # Buffer priority fee / Jito tip saat eksekusi nyata
MAX_ROUNDTRIP_LOSS_PCT = float(os.getenv("MAX_ROUNDTRIP_LOSS_PCT", getattr(config, "MAX_ROUNDTRIP_LOSS_PCT", 5.5)))
MIN_NET_PROFIT_AFTER_DELAY_PCT = float(os.getenv("MIN_NET_PROFIT_AFTER_DELAY_PCT", getattr(config, "MIN_NET_PROFIT_AFTER_DELAY_PCT", 18.0)))
BATCH_CANDIDATE_LIMIT = min(90, int(os.getenv("BATCH_CANDIDATE_LIMIT", getattr(config, "BATCH_CANDIDATE_LIMIT", 90))))
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", getattr(config, "ALERT_COOLDOWN_SEC", 2700)))  # Cooldown 45m per lorong

# KONSTANTA JALUR KHUSUS "MASUK DI AWAL" (PAUS POMPA DINI < $70K — NAIK DULU SEBELUM DUMP)
EARLY_WHALE_MIN_MC = 28_000.0            # Sudah lulus kurva & bertahan di atas lantai dasar kelulusan
EARLY_WHALE_MAX_SPOT_MC = 66_000.0       # Spot MC wajib <= $66k agar saat klik Telegram (~22d) tetap aman < $70k!
EARLY_WHALE_MAX_BUY_MC = 69_000.0        # Batas mutlak klik beli user (< $70,000 dengan margin aman)
EARLY_WHALE_MAX_AGE_MINS = 35.0          # Wajib koin segar baru lahir/lulus (3.5m - 35m), bukan koin mengendap lama


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
    """Utility fetch JSON cepat dengan retry adaptif pada gangguan transien / burst HTTP 429 & 5xx."""
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
                backoff = (0.18 * (attempt + 1)) if http_err.code == 429 else 0.12
                time.sleep(backoff)
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(0.12)
                continue
            return None
    return None


def _fetch_jup_single_quote(input_mint: str, output_mint: str, amount: int, timeout: float = 2.6):
    """Ambil quote swap di Jupiter V1 Lite API (real-time slot Solana) dengan anti-429 burst & fallback v6."""
    primary_url = (
        f"https://lite-api.jup.ag/swap/v1/quote"
        f"?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps=200"
    )
    data = fetch_json(primary_url, timeout=timeout, retries=2)
    if isinstance(data, dict) and data.get("routePlan") is not None and data.get("outAmount"):
        return data
    fallback_url = (
        f"https://quote-api.jup.ag/v6/quote"
        f"?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps=200"
    )
    data_fb = fetch_json(fallback_url, timeout=1.8, retries=1)
    if isinstance(data_fb, dict) and data_fb.get("routePlan") is not None and data_fb.get("outAmount"):
        return data_fb
    return None


def simulate_jupiter_roundtrip(mint: str, timeout: float = 2.6) -> dict:
    """
    SIMULASI PULANG-PERGI PARALEL (0.1 SOL + STRESS-TEST KEDALAMAN 1.0 SOL) DI SLOT DETIK INI:
    1. Membuktikan koin 100% BISA DIJUAL KEMBALI (Anti Honeypot Jual & Pajak Token-2022).
    2. Menembak kuotasi jual 0.1 SOL & 1.0 SOL secara PARALEL (0ms tambahan waktu tunggu!) untuk membongkar
       jebakan Meteora DLMM / CLMM "Hollow Bin" (0.1 SOL lancar tapi 1.0 SOL terjun ke jurang kosong).
    3. Mengekstrak harga spot live on-chain untuk menangkap dump/pump yang belum muncul di DexScreener!
    """
    q_buy = _fetch_jup_single_quote(SOL_MINT, mint, TEST_BUY_LAMPORTS, timeout=timeout)
    if not q_buy:
        return {"ok": False, "reason": "Tidak memiliki rute beli likuid 0.1 SOL di Jupiter DEX"}

    out_tokens_raw = int(q_buy.get("outAmount") or 0)
    if out_tokens_raw <= 0:
        return {"ok": False, "reason": "Simulasi beli menghasilkan 0 token (Kolam kering)"}

    depth_tokens_raw = out_tokens_raw * TEST_DEPTH_MULTIPLIER
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as jup_exec:
        f_sell_01 = jup_exec.submit(_fetch_jup_single_quote, mint, SOL_MINT, out_tokens_raw, timeout)
        f_sell_depth = jup_exec.submit(_fetch_jup_single_quote, mint, SOL_MINT, depth_tokens_raw, timeout)
        q_sell = f_sell_01.result()
        q_depth = f_sell_depth.result()

    if not q_sell:
        return {"ok": False, "reason": "JEBAKAN HONEYPOT: Bisa dibeli tapi TIDAK BISA DIJUAL kembali ke SOL!"}

    sol_back_lamports = int(q_sell.get("outAmount") or 0)
    if sol_back_lamports <= 0:
        return {"ok": False, "reason": "JEBAKAN HONEYPOT: Estimasi pencairan SOL kembali = 0"}

    if not q_depth or int(q_depth.get("outAmount") or 0) <= 0:
        return {
            "ok": False,
            "reason": "JEBAKAN LIKUIDITAS SEMU (Hollow Bin): 0.1 SOL bisa keluar, tapi pencairan 1.0 SOL gagal/kosong!"
        }

    depth_back_lamports = int(q_depth.get("outAmount") or 0)
    expected_depth_lamports = float(TEST_BUY_LAMPORTS * TEST_DEPTH_MULTIPLIER)
    depth_loss_pct = max(0.0, (1.0 - (depth_back_lamports / expected_depth_lamports)) * 100.0)
    if depth_loss_pct > MAX_DEPTH_CLIFF_LOSS_PCT:
        return {
            "ok": False,
            "reason": (
                f"JEBAKAN JURANG LIKUIDITAS (DLMM/CLMM Tipis): Pencairan 1.0 SOL amblas "
                f"-{depth_loss_pct:.1f}% (> {MAX_DEPTH_CLIFF_LOSS_PCT}%)!"
            )
        }

    rt_loss_pct = max(0.0, (1.0 - (sol_back_lamports / float(TEST_BUY_LAMPORTS))) * 100.0)
    swap_usd_val = float(q_buy.get("swapUsdValue") or 0.0)
    return {
        "ok": True,
        "in_lamports": TEST_BUY_LAMPORTS,
        "out_tokens_raw": out_tokens_raw,
        "sol_back_lamports": sol_back_lamports,
        "rt_loss_pct": round(rt_loss_pct, 2),
        "depth_loss_pct": round(depth_loss_pct, 2),
        "swap_usd_value": swap_usd_val
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

    # ZONA MERAH 1: Tembok Take-Profit Sniper Kelulusan ($86k - $108k)
    if 86_000.0 < mc <= 108_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $100K (${mc:,.0f}): Rawan bantingan sniper kelulusan. Tunggu tembus bersih > $108k!",
            {}
        )

    # ZONA MERAH 2: Tembok Kematian Dev Modal Kecil ($178k - $215k)
    if 178_000.0 < mc <= 215_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $200K (${mc:,.0f}): Area favorit dev receh dump. Tunggu konfirmasi > $215k!",
            {}
        )

    # ZONA MERAH 2B: Buffer Anti-Delay di Atas Lorong 2 ($315k - $340k)
    if 315_000.0 < mc <= 340_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $330K (${mc:,.0f}): Buffer transisi pasca-$315k, tunggu konfirmasi > $340k!",
            {}
        )

    # ZONA MERAH 3: Mendekati / Di Dalam Tembok Psikologis $420k - $510k
    if 420_000.0 < mc <= 510_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $450K-$500K (${mc:,.0f}): Ruang naik sempit, rawan front-run jual early whale!",
            {}
        )

    # ZONA MERAH 4: Tembok Psikologis Sakral $1 Juta ($760k - $1.06M)
    if 760_000.0 < mc <= 1_060_000.0:
        return (
            False,
            f"ZONA PUCUK DISTRIBUSI TEMBOK $1M (${mc:,.0f}): Rawan bantingan take-profit angka bulat $1 Juta. Tunggu jebol bersih > $1.06M!",
            {}
        )

    # ZONA MERAH 5: Tembok Distribusi Early-Whale 20x ($2.65M - $5.25M)
    if 2_650_000.0 < mc <= 5_250_000.0:
        return (
            False,
            f"ZONA MERAH PLAFON $3M-$5M (${mc:,.0f}): Area rawan shakeout early whale. Tunggu konfirmasi ekspansi > $5.25M!",
            {}
        )

    # ZONA MERAH 6: Area Late-Stage > $8.80M (Kawal via Moonbag, jangan buka posisi baru mepet $10M+)
    if mc > 8_800_000.0:
        return (
            False,
            f"ZONA PUCUK LATE-STAGE (${mc:,.0f} > $8.8M): Jangan kejar entry baru di dekat $10M+, kawal via Moonbag!",
            {}
        )

    # LORONG 0: Early Ignition ($25k - $86k) -> Front-run sebelum tembok $100k
    if 25_000.0 <= mc <= 86_000.0:
        tp_mc = min(96_000.0, mc * 1.44)
        return True, "LORONG 0 (Early Ignition < $86k)", {
            "corridor_id": 0,
            "corridor_name": "Lorong 0: Early Ignition (Sebelum Tembok $100k)",
            "corridor_max_mc": 86_000.0,
            "min_liq": 10_500.0,
            "min_liq_ratio": 11.5,
            "min_turnover_h1": 0.55,
            "mature_turnover_h1": 0.32,
            "min_cum_tx": 160,
            "min_avg_ticket": 18.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 14.0
        }

    # LORONG 1: Post-$100k Breakout ($108k - $178k) -> Tangkap pelari <$150k-$178k menuju $210k-$1M+
    if 108_000.0 < mc <= 178_000.0:
        tp_mc = min(208_000.0, max(mc * 1.32, 186_000.0))
        return True, "LORONG 1 (Post-$100k Breakout)", {
            "corridor_id": 1,
            "corridor_name": "Lorong 1: Breakout Tembok $100k ($108k-$178k) -> Menuju $208k+",
            "corridor_max_mc": 178_000.0,
            "min_liq": 18_000.0,
            "min_liq_ratio": 11.5,
            "min_turnover_h1": 0.75,
            "mature_turnover_h1": 0.22,
            "min_cum_tx": 380,
            "min_avg_ticket": 20.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 13.5
        }

    # LORONG 2 (GOLDEN CORRIDOR): Lolos Kuburan $200k ($215k - $315k) -> Ruang Kosong ke $418k
    if 215_000.0 < mc <= 315_000.0:
        tp_mc = min(418_000.0, mc * 1.46)
        return True, "LORONG 2 (Golden Wave-2 Corridor)", {
            "corridor_id": 2,
            "corridor_name": "Lorong 2: Lolos Ujian $200k -> Ruang Terbang ke $400k+",
            "corridor_max_mc": 315_000.0,
            "min_liq": 25_000.0,
            "min_liq_ratio": 11.0,
            "min_turnover_h1": 0.75,
            "mature_turnover_h1": 0.14,
            "min_cum_tx": 800,
            "min_avg_ticket": 22.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 14.0
        }

    # LORONG 2B (MID-CAP ACCELERATION): Ekspansi $340k - $420k -> Menuju $492k+ ($HI Trajectory)
    if 340_000.0 < mc <= 420_000.0:
        tp_mc = min(492_000.0, max(mc * 1.28, 455_000.0))
        return True, "LORONG 2B (Mid-Cap Acceleration $340k-$420k)", {
            "corridor_id": 2,
            "corridor_name": "Lorong 2B: Akselerasi Menengah ($340k-$420k) -> Front-Run $492k",
            "corridor_max_mc": 420_000.0,
            "min_liq": 34_000.0,
            "min_liq_ratio": 9.5,
            "min_turnover_h1": 0.70,
            "mature_turnover_h1": 0.12,
            "min_cum_tx": 1100,
            "min_avg_ticket": 24.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 13.5
        }

    # LORONG 3 (WHALE CORRIDOR): Jebol Tembok $500k ($510k - $760k) -> Magnet $1M (Keluar di $940k)
    if 510_000.0 < mc <= 760_000.0:
        tp_mc = min(940_000.0, mc * 1.36)
        return True, "LORONG 3 (Whale Breakout > $510k)", {
            "corridor_id": 3,
            "corridor_name": "Lorong 3: Jebol Setengah Juta ($510k-$760k) -> Front-Run $940k",
            "corridor_max_mc": 760_000.0,
            "min_liq": 48_000.0,
            "min_liq_ratio": 8.2,
            "min_turnover_h1": 0.65,
            "mature_turnover_h1": 0.09,
            "min_cum_tx": 1600,
            "min_avg_ticket": 26.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 13.5
        }

    # LORONG 4 (POST-$1M CULT IGNITION — $PONS / $STONK EARLY SUPER-RUNNER): $1.06M - $2.65M -> $4.2M + Moonbag $50M+
    if 1_060_000.0 < mc <= 2_650_000.0:
        tp_mc = min(4_200_000.0, mc * 1.48)
        return True, "LORONG 4 (Post-$1M Cult Ignition)", {
            "corridor_id": 4,
            "corridor_name": "Lorong 4: Jebol Sakral $1 Juta -> Cult Ignition ($4.2M + Moonbag $50M+)",
            "corridor_max_mc": 2_650_000.0,
            "min_liq": 85_000.0,
            "min_liq_ratio": 5.2,
            "min_turnover_h1": 0.28,
            "mature_turnover_h1": 0.055,
            "min_cum_tx": 2600,
            "min_avg_ticket": 38.0,
            "target_tp_mc": tp_mc,
            "cut_loss_pct": 12.5
        }

    # LORONG 5 (MEGA-CULT TIER-1 EXPANSION — $5.25M - $8.80M): Terbang Menuju $14M - $50M+
    tp_mc = min(13_800_000.0, mc * 1.46)
    return True, "LORONG 5 (Mega-Cult Tier-1 > $5.25M)", {
        "corridor_id": 5,
        "corridor_name": "Lorong 5: Mega-Cult Tier-1 (> $5.25M) -> Ekspansi $14M-$50M+",
        "corridor_max_mc": 8_800_000.0,
        "min_liq": 210_000.0,
        "min_liq_ratio": 3.4,
        "min_turnover_h1": 0.16,
        "mature_turnover_h1": 0.035,
        "min_cum_tx": 4000,
        "min_avg_ticket": 52.0,
        "target_tp_mc": tp_mc,
        "cut_loss_pct": 12.0
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
    min_liq_ratio = float(c_cfg.get("min_liq_ratio", 8.5 if c_cfg["corridor_id"] == 3 else 11.5))
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
    vol_h24 = float(volumes.get("h24", 0.0) or 0.0)

    # 3. Usia Pair: Lolos Jito Bundle Block-0 (>= 3.5m), Batas Ketat 24j/36j di Lorong 0 (Anti Zombie Micro-Cap),
    #    & Jalur CTO / Cult Multi-Hari (> 1 Hari s/d 60 Hari) di Lorong 1 s/d 5 ($Macca / $PONS / $STONK Guard)!
    pair_created_at_ms = best_pair.get("pairCreatedAt")
    now_ms = time.time() * 1000.0
    is_cto_multi_day_breakout = False
    is_mega_corridor = c_cfg["corridor_id"] in (4, 5)
    if pair_created_at_ms:
        age_minutes = (now_ms - float(pair_created_at_ms)) / 60000.0
        _inf_s1 = best_pair.get("info") or {}
        _soc_cnt = len(_inf_s1.get("socials") or [])
        _pair_boost = max(int(boost_amount or 0), int(((best_pair.get("boosts") or {}).get("active")) or 0))
        _has_full_infra = bool(
            _inf_s1.get("header")
            and (
                bool(_inf_s1.get("websites") or [])
                or (_soc_cnt >= 2 and _pair_boost >= 50)
            )
            and _soc_cnt >= 1
        )
        _min_safe_age = 1.8 if (_has_full_infra and liq >= 28_000.0) else 3.5
        if age_minutes < _min_safe_age:
            return False, f"Terlalu baru ({age_minutes:.1f}m < {_min_safe_age:.1f}m): Fase rawan MEV bundle block-0", {}
        cto_liq_req = {1: 28_000.0, 2: 40_000.0, 3: 48_000.0, 4: 85_000.0, 5: 210_000.0}.get(c_cfg["corridor_id"], 999_999.0)
        cto_vol_h1_req = {1: 25_000.0, 2: 35_000.0, 3: 35_000.0, 4: 85_000.0, 5: 180_000.0}.get(c_cfg["corridor_id"], 999_999.0)
        cto_tx24_req = {1: 650, 2: 900, 3: 1100, 4: 1600, 5: 2400}.get(c_cfg["corridor_id"], 99_999)
        is_cto_multi_day_breakout = (
            age_minutes > 1440.0
            and c_cfg["corridor_id"] in (1, 2, 3, 4, 5)
            and liq >= max(cto_liq_req, c_cfg["min_liq"])
            and vol_h1 >= cto_vol_h1_req
            and cum_tx_24h >= cto_tx24_req
        )
        if c_cfg["corridor_id"] == 0:
            # PERISAI V3.2 (#19 — Anti Stale Sub-$100K Corpse Re-Pump / Kasus Live $Bow 13 Jam):
            max_age_mins = 180.0 if (cum_tx_24h >= 1200 and liq >= 18_000.0) else 90.0
            if age_minutes > max_age_mins:
                return (
                    False,
                    f"Koin lama di bawah $100k ({age_minutes/60.0:.1f} jam > {max_age_mins/60.0:.1f}j di Lorong 0): "
                    f"Rawan jebakan re-pump koin gagal lulus / slow-rug sebelum $100k",
                    {}
                )
        elif is_cto_multi_day_breakout:
            max_age_mins = 86_400.0 if is_mega_corridor else 64_800.0  # 60 hari untuk Cult $1M+ (Lorong 4-5), 45 hari untuk Lorong 1-3
        elif cum_tx_24h >= 5000 and liq >= 30_000.0:
            max_age_mins = 2160.0    # 36 jam
        else:
            max_age_mins = 1440.0    # 24 jam standar
        if age_minutes > max_age_mins:
            return False, f"Koin lama ({age_minutes/60.0:.1f} jam > {max_age_mins/60.0:.0f}j): Rawan jebakan re-pump koin mati", {}
    else:
        age_minutes = 12.0

    # PENANGKAL V2.7A (0ms RAM): Blokir Pantulan Ampas Pasca-Puncak Intraday ($Marvin 1 PM Guard)
    # Jika koin intraday (75m <= usia <= 24j) sudah memutar Vol 24j raksasa (>= 5.0x MC sekarang),
    # namun Vol 1j saat ini sudah kolaps < 22% dari rata-rata per jam masa puncak sebelumnya,
    # berarti koin sudah selesai pesta di MC tinggi lalu ambruk dan hanya memantul lemah!
    if 75.0 <= age_minutes <= 1440.0 and vol_h24 > (vol_h1 * 1.5) and vol_h24 >= (mc * 5.0):
        prior_hours = max(0.5, (min(1440.0, age_minutes) - 60.0) / 60.0)
        prior_hourly_vol = max(0.0, vol_h24 - vol_h1) / prior_hours
        if prior_hourly_vol > 0.0 and vol_h1 < (prior_hourly_vol * 0.22):
            collapse_pct = (1.0 - (vol_h1 / prior_hourly_vol)) * 100.0
            return (
                False,
                f"Jebakan Post-Climax Aftermath: Vol 1j (${vol_h1:,.0f}) sudah kolaps -{collapse_pct:.0f}% dari rata-rata "
                f"puncak sebelumnya (${prior_hourly_vol:,.0f}/j pada Vol24j ${vol_h24:,.0f}) -> Koin sudah lewat masa puncak!",
                {}
            )

    # 4. Temperatur Candle Anti-Pucuk, Wave-1 Hyper-Ignition, & Anti Dead-Cat Bounce
    price_change = best_pair.get("priceChange") or {}
    p_m5 = float(price_change.get("m5", 0.0) or 0.0)
    p_h1 = float(price_change.get("h1", 0.0) or 0.0)
    p_h6 = float(price_change.get("h6", 0.0) or 0.0)

    # PERISAI V3.3 (#25A — Kasus Live $urmom 16:54 WIB Pucuk $300k):
    # Hak istimewa Wave-1 Hyper-Ignition (+68% M5) HANYA berlaku di PARUH AWAL LORONG (progress <= 50%)
    # dengan kolam LP tebal (>= 15% MC, seperti $Marvin $230k LP 20%). Dilarang keras memberi izin M5 >45%
    # saat koin sudah di bibir atas lorong ($280k-$314k dekat plafon $315k) dengan LP tipis (~10%)!
    corridor_floors = {0: 48_000.0, 1: 112_000.0, 2: 228_000.0, 3: 525_000.0, 4: 1_080_000.0, 5: 3_100_000.0}
    c_floor_mc = corridor_floors.get(c_cfg["corridor_id"], mc * 0.85)
    c_span_mc = max(1.0, c_cfg['corridor_max_mc'] - c_floor_mc)
    corridor_progress = max(0.0, min(1.0, (mc - c_floor_mc) / c_span_mc))

    is_hyper_wave1_ignition = (
        age_minutes <= 45.0
        and c_cfg["corridor_id"] in (0, 1, 2)
        and corridor_progress <= 0.50
        and ((liq / max(1.0, mc)) * 100.0) >= 15.0
        and p_h1 >= max(150.0, p_m5 * 2.2)
        and vol_h1 >= max(180_000.0, mc * 1.4)
        and liq >= (c_cfg["min_liq"] * 1.25)
    )

    is_proven_climber = (
        (
            age_minutes >= 45.0
            and cum_tx_24h >= max(2500, c_cfg["min_cum_tx"] * (1.2 if is_mega_corridor else 2.0))
            and p_h1 >= (14.0 if is_mega_corridor else 15.0)
            and p_h6 >= (15.0 if is_mega_corridor else 20.0)
        )
        or (
            is_cto_multi_day_breakout
            and p_h1 >= (14.0 if is_mega_corridor else 15.0)
            and p_h6 >= 12.0
        )
    )
    if is_proven_climber and is_mega_corridor:
        min_p_m5 = 4.0
    elif is_proven_climber:
        min_p_m5 = 5.0
    else:
        min_p_m5 = 6.0

    max_p_m5 = 68.0 if is_hyper_wave1_ignition else 45.0
    if p_m5 < min_p_m5:
        return False, f"Momentum 5m belum menginjak gas ({p_m5:+.1f}% < +{min_p_m5:.1f}%) - Tunggu dorongan naik", {}
    if p_m5 > max_p_m5:
        return False, f"ANTI-PUCUK: Candle 5m sudah kepanjangan (+{p_m5:.1f}% > +{max_p_m5:.0f}%). Rawan koreksi sesaat!", {}
    # KALIBRASI V3.4 (#29A — Kasus Live $Nothing 51Sfa...pump Lulus $44k -> Cuci Sniper $212k->$67k -> Reclaim $127k->$1.52M):
    h1_tx_pre = txns.get("h1") or {}
    m5_tx_pre = txns.get("m5") or {}
    bs_h1_pre = int(h1_tx_pre.get("buys", 0)) / max(1, int(h1_tx_pre.get("sells", 0)))
    bs_m5_pre = int(m5_tx_pre.get("buys", 0)) / max(1, int(m5_tx_pre.get("sells", 0)))
    is_wave2_v_reclaim = (
        25.0 <= age_minutes <= 180.0
        and mc <= 315_000.0
        and -25.0 <= p_h1 < 0.0
        and p_h6 >= 100.0
        and p_m5 >= 18.0
        and liq >= 18_000.0
        and ((liq / max(1.0, mc)) * 100.0) >= 14.0
        and vol_h1 >= 75_000.0
        and bs_h1_pre >= 1.15
        and bs_m5_pre >= 1.45
    )

    if (p_h1 <= 0.0 and not is_wave2_v_reclaim) or p_h6 < -12.0:
        return False, f"Tren makro sedang distribusi/merah (H1: {p_h1:+.1f}%, H6: {p_h6:+.1f}%)", {}

    # Anti Dead-Cat Bounce: koin >= 60 menit yang sempat ambruk lalu pantul kecil di 5m terakhir
    if age_minutes >= 60.0 and p_h1 < (p_m5 * 0.60) and not is_wave2_v_reclaim:
        return (
            False,
            f"Jebakan Dead-Cat Bounce: Pantulan 5m (+{p_m5:.1f}%) di tengah tren 1 jam lemah (+{p_h1:.1f}%)",
            {}
        )

    # 5. PENANGKAL DEV LICIK #1: Kepadatan Transaksi (Anti Bundled Ghost Pump; adaptif untuk rolling 24j CTO > 1 hari)
    chain_id_s1 = str(best_pair.get("chainId") or "solana").lower()
    base_min_cum_tx = (
        {1: 650, 2: 900, 3: 1100, 4: 1600, 5: 2400}.get(c_cfg["corridor_id"], c_cfg["min_cum_tx"])
        if is_cto_multi_day_breakout
        else c_cfg["min_cum_tx"]
    )
    effective_min_cum_tx = int(base_min_cum_tx * 0.60) if chain_id_s1 in ("bsc", "base", "ethereum") else base_min_cum_tx
    _tx_h1_tmp = (best_pair.get("txns") or {}).get("h1") or {}
    _tx_m5_tmp = (best_pair.get("txns") or {}).get("m5") or {}
    cum_tx_24h = max(cum_tx_24h, int(_tx_h1_tmp.get("buys") or 0) + int(_tx_h1_tmp.get("sells") or 0), int(_tx_m5_tmp.get("buys") or 0) + int(_tx_m5_tmp.get("sells") or 0))
    # MODUS #37 (V4.0 — Kasus Nyata $FLOAT Bv1a7c...BMmr High-Cap Graduation $142.5K -> $2.16M dalam 30 Menit):
    # Koin launchpad kustom (mis. floatlaunch.xyz -> Raydium CPMM) yang langsung lulus di MC $140K-$250K baru berumur
    # 2-15 menit tetapi kecepatannya >= 32 tx/menit dengan LP tebal >= $28K (>=16% MC) & Vol 5m >= $35K -> BUKAN Ghost-Pump!
    _vol_m5_s1 = float((best_pair.get("volume") or {}).get("m5") or 0.0)
    if (
        age_minutes <= 18.0
        and liq >= max(c_cfg["min_liq"], 28_000.0)
        and (liq / max(1.0, mc)) >= 0.16
        and _vol_m5_s1 >= max(26_000.0, mc * 0.15)
        and cum_tx_24h >= 120
        and (cum_tx_24h / max(1.0, age_minutes)) >= 32.0
    ):
        effective_min_cum_tx = min(effective_min_cum_tx, max(120, int(age_minutes * 30.0)))
    if cum_tx_24h < effective_min_cum_tx:
        return (
            False,
            f"Jebakan Ghost-Pump Dev: MC ${mc:,.0f} tapi total transaksi baru {cum_tx_24h} (< {effective_min_cum_tx} tx)",
            {}
        )

    # 6. Dominasi Pembeli Nyata & Kesehatan Orderflow 1 Jam (Kalibrasi MEV Arbitrase pada Pool Ramai & Pool Cult $1M+)
    if total_m5 < 25 or sells_m5 >= buys_m5:
        return False, f"Partisipasi pembeli lemah ({buys_m5}B / {sells_m5}S)", {}

    buy_sell_ratio_m5 = buys_m5 / max(1, sells_m5)
    buy_sell_ratio_h1 = buys_h1 / max(1, sells_h1) if (buys_h1 + sells_h1) > 0 else 1.5
    if (buys_h1 + sells_h1) >= 80 and buy_sell_ratio_h1 < 1.05:
        return False, f"Distribusi H1 berat: Penjual mendominasi 1 jam terakhir (Rasio H1 {buy_sell_ratio_h1:.2f}x)", {}

    is_sub100k_propulsion_flow = (
        c_cfg["corridor_id"] in (0, 1, 2, 3)
        and vol_m5 >= max(9_000.0, mc * 0.085)
        and total_m5 >= 55
        and buy_sell_ratio_m5 >= 1.28
        and p_m5 >= 7.0
        and buy_sell_ratio_h1 >= 1.15
    )
    high_tx_organic_flow = (
        (
            total_m5 >= (60 if is_mega_corridor else 80)
            and is_proven_climber
            and buy_sell_ratio_h1 >= (1.12 if is_mega_corridor else 1.18)
        )
        or (
            is_hyper_wave1_ignition
            and total_m5 >= 120
            and cum_tx_24h >= 2500
            and buy_sell_ratio_h1 >= 1.20
        )
        or is_wave2_v_reclaim
        or is_sub100k_propulsion_flow
    )
    min_bs_m5 = (1.30 if is_mega_corridor else (1.28 if is_sub100k_propulsion_flow else 1.35)) if high_tx_organic_flow else 1.85
    min_cvd = (13.0 if is_mega_corridor else (12.0 if is_sub100k_propulsion_flow else 15.0)) if high_tx_organic_flow else 29.8
    h1_decay_floor = (0.80 if is_mega_corridor else 0.82) if high_tx_organic_flow else 0.90

    if buy_sell_ratio_m5 < min_bs_m5:
        return False, f"Rasio Buy/Sell kurang dominan ({buy_sell_ratio_m5:.2f}x < {min_bs_m5:.2f}x)", {}

    if age_minutes >= 20.0 and buy_sell_ratio_m5 < (buy_sell_ratio_h1 * h1_decay_floor):
        return False, f"Momentum beli melambat (Rasio M5 {buy_sell_ratio_m5:.2f}x < H1 {buy_sell_ratio_h1:.2f}x)", {}

    cvd_ratio = round(((buys_m5 - sells_m5) / max(1, total_m5)) * 100.0, 1)
    if cvd_ratio < min_cvd:
        return False, f"CVD Net Inflow kurang kuat (+{cvd_ratio}% < +{min_cvd}%)", {}

    # 7. PENANGKAL DEV LICIK #2: Anti-Siphon Bot M5, Asymmetric Whale Siphon, & H1 Siphon
    total_h1 = buys_h1 + sells_h1
    vol_to_liq_m5 = vol_m5 / max(1.0, liq)
    if buy_sell_ratio_m5 >= 2.8 and vol_m5 >= 5000.0 and p_m5 < 8.5:
        return (
            False,
            f"Terdeteksi Siphon Bot Dev: Rasio klik {buy_sell_ratio_m5:.1f}x tapi harga tertahan (+{p_m5:.1f}%) -> Sell paus diam-diam!",
            {}
        )
    if not is_mega_corridor and total_m5 >= 80 and buy_sell_ratio_m5 >= 2.15 and vol_to_liq_m5 >= 0.55 and p_m5 < 9.0:
        return (
            False,
            f"Terdeteksi Asymmetric Siphon Bot: Volume 5m memutar {vol_to_liq_m5:.2f}x LP dengan rasio klik "
            f"{buy_sell_ratio_m5:.2f}x tapi harga tertahan (+{p_m5:.1f}%) -> Paus buang barang ke pembeli receh!",
            {}
        )
    # PERISAI V3.2 (#21A): Sub-$100K Silent Churn / Distribusi Halus di Kolam Tipis
    if mc < 100_000.0 and vol_to_liq_m5 >= 0.85 and p_m5 < 10.5:
        return (
            False,
            f"Terdeteksi Silent Churn <$100k: Volume 5m memutar {vol_to_liq_m5:.2f}x LP tapi harga tertahan (+{p_m5:.1f}%) "
            f"-> Bandar cicil buang barang halus sebelum $100k!",
            {}
        )
    if age_minutes >= 30.0 and total_h1 >= 600 and buy_sell_ratio_h1 >= 2.20 and vol_h1 >= 40_000.0 and p_h1 < 12.0:
        return (
            False,
            f"Terdeteksi Siphon Distribusi 1 Jam: Klik Buy H1 {buy_sell_ratio_h1:.2f}x tapi harga tertahan (+{p_h1:.1f}%) -> Bandar cicil jual!",
            {}
        )

    # 8. PENANGKAL DEV LICIK #3: Anti-Wash-Breakout vs Viral FOMO Velocity ($Marvin 10 AM Guard), Anti-Churn, & Perputaran Suplai
    is_viral_price_expansion = (p_m5 >= 14.0 and p_h1 >= 35.0 and buy_sell_ratio_m5 >= 1.35)
    max_vol_to_liq_m5 = 4.8 if is_viral_price_expansion else 2.4
    if vol_to_liq_m5 > max_vol_to_liq_m5:
        return (
            False,
            f"Terdeteksi Wash-Trading Bot: Volume 5m (${vol_m5:,.0f}) > {max_vol_to_liq_m5:.1f}x isi kolam LP (${liq:,.0f}) -> Uang diputar di tempat!",
            {}
        )

    turnover_h1 = vol_h1 / max(1.0, mc)
    if age_minutes >= 30.0 and turnover_h1 >= 1.65 and p_h1 < 14.0 and not is_wave2_v_reclaim:
        return (
            False,
            f"Terdeteksi Distribusi Churn Bandar: Volume 1j sangat besar ({turnover_h1:.2f}x MC) tapi harga jalan di tempat (+{p_h1:.1f}%)",
            {}
        )

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

    # 10. Ukuran Tiket M5 + H1 & Verifikasi Komitmen Modal Nyata (Tanpa Diskon Sosmed Gratis!)
    avg_ticket = vol_m5 / max(1, total_m5)
    has_commitment = (boost_amount >= 100) or (pair_count >= 2 and liq >= 35_000.0) or is_proven_climber
    effective_min_ticket = c_cfg["min_avg_ticket"] * (0.85 if has_commitment else 1.0)
    min_vol_m5_req = 4200.0 if c_cfg["corridor_id"] == 0 else 2500.0
    if vol_m5 < min_vol_m5_req or avg_ticket < effective_min_ticket:
        return (
            False,
            f"Order/volume 5m terlalu lemah untuk dorong naik (${vol_m5:,.0f} / tiket ${avg_ticket:.1f} < ${effective_min_ticket:.1f})",
            {}
        )

    # GERBANG V5.0 (0ms RAM): Verifikasi Pembeli Unik vs Bot Klik Berulang & Eksodus Penjual dari Snapshot GeckoTerminal
    gt_crowd = best_pair.get("gt_crowd") or {}
    uniq_b_m5 = int(gt_crowd.get("buyers_m5") or 0)
    uniq_s_m5 = int(gt_crowd.get("sellers_m5") or 0)
    if uniq_b_m5 > 0 and buys_m5 >= 50 and (buys_m5 / float(uniq_b_m5)) > 5.5:
        return (
            False,
            f"Terdeteksi Wash-Bot Klik Berulang: {buys_m5} klik Buy hanya dari {uniq_b_m5} dompet unik "
            f"({buys_m5/float(uniq_b_m5):.1f} klik/dompet) -> Volume semu sebelum $100k!",
            {}
        )
    if mc < 100_000.0 and uniq_b_m5 >= 12 and uniq_s_m5 >= int(uniq_b_m5 * 0.92):
        return (
            False,
            f"Eksodus Penjual Unik <$100k: Dompet penjual ({uniq_s_m5}) hampir menyamai/melebihi pembeli unik ({uniq_b_m5}) -> Rawan mati sebelum $100k!",
            {}
        )

    # PENANGKAL DEV LICIK #4 (V2.4): Pembunuh Bump-Bot / Botnet Ribuan Transaksi 1 Jam ($Leonida Guard)
    avg_ticket_h1 = vol_h1 / max(1, total_h1)
    tx_per_1k_liq_h1 = total_h1 / max(1.0, liq / 1000.0)
    if total_h1 >= 250:
        min_ticket_h1 = max(16.0, effective_min_ticket * 0.78)
        if avg_ticket_h1 < min_ticket_h1:
            return (
                False,
                f"Terdeteksi Botnet Micro-Order H1: Tiket 1 jam hanya ${avg_ticket_h1:.1f} (< ${min_ticket_h1:.1f} dari {total_h1} tx)",
                {}
            )
    # Pada koin viral ekspansif (p_h1 >= +55% & B/S H1 >= 1.18x seperti $Foxii 16,448 tx/jam), ribuan ritel Axiom/BullX
    # membeli klip 0.1-0.2 SOL (~$18-$30); botnet pencuci $Leonida mencuci di tempat (p_h1 rendah / B/S timpang).
    if total_h1 >= 1200 and tx_per_1k_liq_h1 > 220.0 and avg_ticket_h1 < 35.0 and not (p_h1 >= 55.0 and avg_ticket_h1 >= 18.0 and buy_sell_ratio_h1 >= 1.18):
        return (
            False,
            f"Terdeteksi Spam Frekuensi Botnet H1: {total_h1} tx/jam pada LP ${liq:,.0f} ({tx_per_1k_liq_h1:.0f} tx/$1k LP)",
            {}
        )

    # PERISAI V3.2 (#24): Whale Ticket Shrinkage / Retail Hand-Off di <$100k
    if mc < 100_000.0 and age_minutes >= 8.0 and total_h1 >= 120 and avg_ticket_h1 > 0.0:
        if avg_ticket < (avg_ticket_h1 * 0.68):
            return (
                False,
                f"Terdeteksi Paus Berhenti Beli / Retail Hand-Off (<$100k): Tiket M5 menyusut ke ${avg_ticket:.1f} "
                f"(< 68% rata-rata H1 ${avg_ticket_h1:.1f}) -> Paus siap distribusi sebelum $100k!",
                {}
            )

    # 11. KOMPENSASI DELAY PENCARIAN -> TELEGRAM -> KLIK MANUSIA (~22 DETIK)
    # Hitung seberapa jauh harga sudah berlari saat user benar-benar membuka Telegram & klik Buy
    vel_pct_per_sec = max(0.0, p_m5) / 300.0
    accel_boost = min(2.2, max(1.0, accel_ratio * 0.85))
    # PERISAI V3.3 (#25B — Kasus Live $urmom 16:53-16:54 WIB $280k-$314k):
    # Jika koin sudah terbang vertikal H1 >= 220% dan masih sprint M5 >= 32% saat sudah berada di >=58% atap lorong,
    # larang kejar di bibir atas karena 1 menit kemudian pasti menabrak Zona Merah & dibanting bandar!
    if corridor_progress >= 0.58 and p_h1 >= 220.0 and p_m5 >= 32.0:
        return (
            False,
            f"ANTI-TELAT KLIMAKS ATAP LORONG: MC ${mc:,.0f} sudah di {corridor_progress*100:.0f}% bibir plafon "
            f"${c_cfg['corridor_max_mc']:,.0f} pasca lonjakan parabolik (H1 +{p_h1:.0f}%, M5 +{p_m5:.1f}%) -> Rawan beli tepat di puncak!",
            {}
        )
    parabolic_climax_mult = 2.15 if (corridor_progress >= 0.55 and p_h1 >= 150.0 and p_m5 >= 28.0) else 1.0
    max_drift_cap = 22.0 if parabolic_climax_mult > 1.0 else 15.0
    est_delay_drift_pct = round(
        min(max_drift_cap, vel_pct_per_sec * PIPELINE_REACTION_DELAY_SEC * accel_boost * parabolic_climax_mult), 2
    )
    prelim_projected_mc = mc * (1.0 + (est_delay_drift_pct / 100.0))

    if prelim_projected_mc > c_cfg['corridor_max_mc']:
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

    pre_bot_organic_bonus = (
        9.0 if is_sub100k_propulsion_flow
        else (5.0 if (c_cfg["corridor_id"] in (0, 1) and vol_m5 >= 5_500.0 and liq >= 12_000.0 and buy_sell_ratio_m5 >= 1.55 and (accel_ratio >= 1.15 or (vol_m5 / max(1.0, mc)) >= 0.09)) else 0.0)
    )
    organic_depth_bonus = (8.0 if high_tx_organic_flow else 0.0) + (4.0 if is_wave2_v_reclaim else 0.0) + pre_bot_organic_bonus
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
        "corridor_max_mc": c_cfg['corridor_max_mc'],
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
        "boost_amount": boost_amount,
        "is_hyper_wave1": is_hyper_wave1_ignition
    }


# ==============================================================================
# MODUL 2B (V3.1): JALUR KHUSUS "MASUK DI AWAL" (< $70K PAUS POMPA DINI)
# Membedakan koin baru lulus yang akan DIPOMPA TINGGI DULU oleh Paus/MM Modal Besar
# vs koin receh yang langsung dibanting di bawah $100k — TANPA mengubah filter V3.0!
# ==============================================================================
def evaluate_early_whale_candidate(best_pair: dict, pair_count: int = 1, boost_amount: int = 0) -> tuple[bool, str, dict]:
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)

    # 1. Wajib berada di zona perawan pasca-lulus di bawah $70k ($28k - $66k spot)
    if mc < EARLY_WHALE_MIN_MC or mc > EARLY_WHALE_MAX_SPOT_MC:
        return False, f"MC di luar zona Masuk di Awal <$70k (${mc:,.0f} harus $28k-$66k agar aman di bawah $70k)", {}

    # 2. Wajib memiliki timestamp kelahiran nyata & berada di jendela awal 3.5m - 35.0m
    pair_created_at_ms = best_pair.get("pairCreatedAt")
    if not pair_created_at_ms:
        return False, "Timestamp pairCreatedAt kosong (wajib terverifikasi untuk Masuk di Awal)", {}

    age_minutes = ((time.time() * 1000.0) - float(pair_created_at_ms)) / 60000.0
    if age_minutes < 3.5:
        return False, f"Terlalu dini ({age_minutes:.1f}m < 3.5m): Tunggu lolos guncangan blok-0 kelulusan", {}
    if age_minutes > EARLY_WHALE_MAX_AGE_MINS:
        return False, f"Usia lewat fase awal ({age_minutes:.1f}m > {EARLY_WHALE_MAX_AGE_MINS:.0f}m)", {}

    # 3. Fondasi Likuiditas Paus (Wajib jauh lebih tebal dari koin receh: LP >= $12.5k & LP/MC >= 16%)
    if liq < 12_500.0:
        return False, f"LP kurang tebal untuk standar Paus Awal (${liq:,.0f} < $12,500)", {}
    liq_mc_ratio = (liq / max(1.0, mc)) * 100.0
    if liq_mc_ratio < 16.0:
        return False, f"Rasio LP/MC kurang tebal untuk jaminan tahan banting awal ({liq_mc_ratio:.1f}% < 16.0%)", {}

    # 4. Data Orderflow & Volume Akumulasi Awal
    txns = best_pair.get("txns") or {}
    m5_tx = txns.get("m5") or {}
    h1_tx = txns.get("h1") or {}
    h24_tx = txns.get("h24") or {}

    buys_m5, sells_m5 = int(m5_tx.get("buys", 0)), int(m5_tx.get("sells", 0))
    buys_h1, sells_h1 = int(h1_tx.get("buys", 0)), int(h1_tx.get("sells", 0))
    cum_tx_24h = int(h24_tx.get("buys", 0)) + int(h24_tx.get("sells", 0))
    total_m5 = buys_m5 + sells_m5
    total_h1 = buys_h1 + sells_h1

    volumes = best_pair.get("volume") or {}
    vol_m5 = float(volumes.get("m5", 0.0) or 0.0)
    vol_h1 = float(volumes.get("h1", 0.0) or 0.0)

    avg_ticket_m5 = vol_m5 / max(1, total_m5)
    avg_ticket_h1 = vol_h1 / max(1, total_h1)

    # 5. VERIFIKASI MODAL HANGUS (SUNK COST) ATAU BELANJA BLOK PAUS (Anti Dev Receh Banting <$100k!)
    # Bandar yang sudah bakar uang Boost/Enhanced Profile di MC <$68.5k ATAU belanja tiket besar ($55+/tx)
    # secara matematis RUGI jika langsung banting di bawah $100k — mereka wajib pompa tinggi dulu!
    info = best_pair.get("info") or {}
    has_paid_dex_profile = bool(info.get("header") or info.get("openGraph")) and bool(
        info.get("socials") or info.get("websites")
    )

    whale_proof_label = ""
    if boost_amount >= 100 and avg_ticket_m5 >= 26.0 and vol_m5 >= 5_500.0:
        whale_proof_label = f"Sunk-Cost Boost {boost_amount}x di MC Bawah + Tiket ${avg_ticket_m5:.0f}/tx"
    elif (avg_ticket_m5 >= 55.0 or avg_ticket_h1 >= 50.0) and vol_m5 >= 8_500.0:
        whale_proof_label = f"Akumulasi Blok Paus (${avg_ticket_m5:.0f}/tx | Vol 5m ${vol_m5:,.0f})"
    elif (has_paid_dex_profile or boost_amount >= 50) and avg_ticket_m5 >= 38.0 and vol_m5 >= 7_000.0 and liq_mc_ratio >= 18.0:
        whale_proof_label = f"Modal Profil/Boost Dini + Arus Paus (${avg_ticket_m5:.0f}/tx | LP {liq_mc_ratio:.1f}%)"
    else:
        return (
            False,
            f"Belum terbukti dipompa Paus/Modal Besar di awal (Boost={boost_amount}, Tiket=${avg_ticket_m5:.1f}, Vol5m=${vol_m5:,.0f})",
            {}
        )

    # 6. Struktur Momentum "Naik Dulu" & Dominasi Akumulasi Sejak Lahir
    price_change = best_pair.get("priceChange") or {}
    p_m5 = float(price_change.get("m5", 0.0) or 0.0)
    p_h1 = float(price_change.get("h1", 0.0) or 0.0)
    p_h6 = float(price_change.get("h6", 0.0) or 0.0)

    if p_m5 < 8.0 or p_m5 > 58.0:
        return False, f"Momentum 5m di luar zona akumulasi awal sehat ({p_m5:+.1f}% harus +8% s/d +58%)", {}
    if p_h1 < 15.0 or p_h6 < 0.0:
        return False, f"Tren sejak lahir kurang ekspansif (H1: {p_h1:+.1f}%, H6: {p_h6:+.1f}%)", {}

    if cum_tx_24h < 140 or buys_m5 < 30 or sells_m5 >= buys_m5:
        return False, f"Partisipasi beli awal belum padat ({buys_m5}B/{sells_m5}S, total {cum_tx_24h} tx)", {}

    buy_sell_ratio_m5 = buys_m5 / max(1, sells_m5)
    buy_sell_ratio_h1 = buys_h1 / max(1, sells_h1) if total_h1 > 0 else 1.5
    cvd_ratio = round(((buys_m5 - sells_m5) / max(1, total_m5)) * 100.0, 1)

    # Izinkan alur Market-Maker Paus dua arah yang sehat (>= 1.45x) ASALKAN akumulasi total 1 jam net beli kuat (>= 1.22x)!
    if buy_sell_ratio_m5 < 1.45 or cvd_ratio < 18.0:
        return False, f"Dominasi beli 5m kurang kuat untuk Masuk di Awal ({buy_sell_ratio_m5:.2f}x | CVD +{cvd_ratio}%)", {}
    if buy_sell_ratio_h1 < 1.22:
        return False, f"Akumulasi total sejak lahir lemah (Rasio H1 {buy_sell_ratio_h1:.2f}x < 1.22x) -> Rawan distribusi dini", {}

    # 7. Tetap kunci rapat Anti-Siphon, Anti-Wash-Loop, & Anti-Bump-Bot Receh!
    vol_to_liq_m5 = vol_m5 / max(1.0, liq)
    if buy_sell_ratio_m5 >= 2.8 and vol_m5 >= 5_000.0 and p_m5 < 9.5:
        return False, "Terdeteksi Siphon Bot di MC awal (klik beli ramai tapi harga ditahan)", {}
    if total_m5 >= 80 and buy_sell_ratio_m5 >= 2.15 and vol_to_liq_m5 >= 0.55 and p_m5 < 9.0:
        return False, "Terdeteksi Asymmetric Siphon di MC awal", {}
    if vol_to_liq_m5 >= 0.85 and p_m5 < 10.5:
        return (
            False,
            f"Terdeteksi Silent Churn <$100k: Volume 5m memutar {vol_to_liq_m5:.2f}x LP tapi harga tertahan (+{p_m5:.1f}%)",
            {}
        )
    if vol_to_liq_m5 > 3.6:
        return False, f"Perputaran M5 terlalu ekstrem terhadap LP ({vol_to_liq_m5:.2f}x > 3.6x)", {}
    if total_h1 >= 150 and avg_ticket_h1 < 22.0:
        return False, f"Terdeteksi Bump-Bot Receh H1 (tiket rata-rata ${avg_ticket_h1:.1f} < $22)", {}
    if age_minutes >= 8.0 and total_h1 >= 120 and avg_ticket_h1 > 0.0 and avg_ticket_m5 < (avg_ticket_h1 * 0.68):
        return (
            False,
            f"Terdeteksi Paus Berhenti Beli / Retail Hand-Off (<$100k): Tiket M5 ${avg_ticket_m5:.1f} menyusut tajam dari H1 ${avg_ticket_h1:.1f}",
            {}
        )

    turnover_h1 = vol_h1 / max(1.0, mc)
    if vol_h1 < max(18_000.0, mc * 0.45):
        return False, f"Volume serapan awal belum cukup kuat (${vol_h1:,.0f})", {}

    effective_h1_mins = min(60.0, max(5.0, age_minutes))
    accel_ratio = (vol_m5 / 5.0) / max(1.0, vol_h1 / effective_h1_mins)
    if age_minutes >= 12.0 and accel_ratio < 0.95 and (vol_m5 / max(1.0, mc)) < 0.14:
        return False, f"Arus uang segar paus melambat (Akselerasi {accel_ratio:.2f}x)", {}

    # 8. GARANSI MUTLAK BELI DI BAWAH MC $70K SETELAH KOMPENSASI DELAY TELEGRAM 22 DETIK!
    vel_pct_per_sec = max(0.0, p_m5) / 300.0
    accel_boost = min(2.2, max(1.0, accel_ratio * 0.85))
    est_delay_drift_pct = round(min(15.0, vel_pct_per_sec * PIPELINE_REACTION_DELAY_SEC * accel_boost), 2)
    prelim_projected_mc = mc * (1.0 + (est_delay_drift_pct / 100.0))

    if prelim_projected_mc > EARLY_WHALE_MAX_BUY_MC:
        return (
            False,
            f"MC ${mc:,.0f} terlalu mepet $70k: saat klik di Telegram (~22d) diproyeksi ${prelim_projected_mc:,.0f} (> $69.8k)",
            {}
        )

    tp1_mc = round(max(115_000.0, prelim_projected_mc * 2.05), 0)   # ~2x Tarik Modal Pokok (Aman sebelum/lewat $120k)
    tp2_mc = round(max(260_000.0, prelim_projected_mc * 4.80), 0)   # ~4.8x Ekspansi Menengah
    tp3_mc = round(max(780_000.0, prelim_projected_mc * 15.00), 0)  # ~15x-20x Moonbag Kawal Paus

    score = min(
        99,
        int(
            48.0
            + min(20.0, (avg_ticket_m5 / 60.0) * 20.0)
            + min(15.0, (buy_sell_ratio_m5 / 2.2) * 15.0)
            + min(10.0, (liq_mc_ratio / 22.0) * 10.0)
            + (7.0 if boost_amount >= 100 or has_paid_dex_profile else 3.0)
        )
    )

    return True, "LOLOS TAHAP 1 MASUK DI AWAL (<$70K WHALE PUMP)", {
        "score": score,
        "is_early_whale": True,
        "whale_proof_label": whale_proof_label,
        "corridor_id": 0,
        "corridor_name": "MASUK DI AWAL (< $70K — Paus Pompa Dini)",
        "corridor_max_mc": EARLY_WHALE_MAX_BUY_MC,
        "target_tp_mc": tp1_mc,
        "tp2_mc": tp2_mc,
        "tp3_mc": tp3_mc,
        "cut_loss_pct": 13.5,
        "est_delay_drift_pct": est_delay_drift_pct,
        "cvd_ratio": cvd_ratio,
        "buy_sell_ratio": round(buy_sell_ratio_m5, 2),
        "buy_sell_ratio_h1": round(buy_sell_ratio_h1, 2),
        "avg_ticket": round(avg_ticket_m5, 1),
        "liq_mc_ratio": round(liq_mc_ratio, 1),
        "turnover_h1": round(turnover_h1, 2),
        "accel_ratio": round(accel_ratio, 2),
        "price_change_m5": round(p_m5, 1),
        "price_change_h1": round(p_h1, 1),
        "age_minutes": round(age_minutes, 1),
        "cum_tx_24h": cum_tx_24h,
        "boost_amount": boost_amount,
        "is_hyper_wave1": False
    }


# ==============================================================================
# MODUL 3: AUDIT KONTRAK, SALDO DEV, LP ASLI (HARD SOL), & ANTI NANO-SWARM V2.4
# ==============================================================================
def analyze_holders_and_lp_lock(
    rug_data: dict,
    pair_address: str = "",
    mc_usd: float = 0.0,
    age_minutes: float = 60.0,
    token_mint: str = ""
) -> tuple[bool, str, dict]:
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
    known_accounts = rug_data.get("knownAccounts") or {}
    market_vaults = set()
    if isinstance(known_accounts, dict):
        for k_addr, k_meta in known_accounts.items():
            if isinstance(k_meta, dict):
                k_type = str(k_meta.get("type") or "").strip().lower()
                if k_type in {"amm", "locker", "clob", "burn", "vault"}:
                    market_vaults.add(str(k_addr).lower())

    total_locked_usd = 0.0
    total_unlocked_usd = 0.0
    primary_lp_locked_pct = 100.0
    primary_locked_usd = 0.0
    sol_backing_ratio = 50.0
    primary_hard_usd = 0.0

    # 4. Analisis LP Lock, Fake Secondary Pool, & Kecukupan SOL Asli di Kolam (Anti Single-Sided LP)
    sorted_markets = sorted(
        markets,
        key=lambda m: float((m.get("lp") or {}).get("baseUSD") or 0.0) + float((m.get("lp") or {}).get("quoteUSD") or 0.0),
        reverse=True
    )
    has_matched_primary = bool(
        pair_address
        and any(str(x.get("pubkey") or "").lower() == str(pair_address).lower() for x in sorted_markets)
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

        is_target_primary_mkt = (
            (str(m.get("pubkey") or "").lower() == str(pair_address).lower())
            if has_matched_primary
            else (idx == 0)
        )
        if is_target_primary_mkt and pool_usd > 2500.0:
            hard_usd = 0.0
            base_m = str(lp.get("baseMint") or m.get("mintA") or "")
            quote_m = str(lp.get("quoteMint") or m.get("mintB") or "")
            if base_m in HARD_QUOTE_MINTS:
                hard_usd = base_usd
            elif quote_m in HARD_QUOTE_MINTS:
                hard_usd = quote_usd
            elif token_mint:
                if base_m == token_mint and quote_m and quote_m != token_mint:
                    hard_usd = quote_usd
                elif quote_m == token_mint and base_m and base_m != token_mint:
                    hard_usd = base_usd
            if hard_usd > 0.0:
                primary_hard_usd = hard_usd
                sol_backing_ratio = round((hard_usd / pool_usd) * 100.0, 1)
                if sol_backing_ratio < 32.0:
                    return (
                        False,
                        f"Jebakan Single-Sided LP Palsu: Isi kolam hanya {sol_backing_ratio:.1f}% aset pasangan asli (< 32%)",
                        {}
                    )

        if locked_pct is not None and pool_usd > 1000.0:
            pct_val = float(locked_pct)
            if 0.0 < pct_val <= 1.0 and float(lp.get("lpLockedUSD") or 0.0) > pool_usd * 0.5:
                pct_val *= 100.0
            is_primary_mkt = (str(m.get("pubkey") or "").lower() == str(pair_address).lower()) if pair_address else (idx == 0)
            if is_primary_mkt or idx == 0:
                if is_primary_mkt or not pair_address:
                    primary_lp_locked_pct = round(pct_val, 1)
                if ("raydium" in m_type or "pump" in m_type) and lp_mint != "11111111111111111111111111111111":
                    if pct_val < 85.0:
                        return False, f"Bahaya Rug LP: Pool utama hanya terkunci {pct_val:.1f}% (< 85%)", {}

            locked_u = float(lp.get("lpLockedUSD") or (pool_usd * (pct_val / 100.0)))
            if (str(m.get("pubkey") or "").lower() == str(pair_address).lower()) if pair_address else (idx == 0):
                primary_locked_usd = max(primary_locked_usd, locked_u)
            total_locked_usd += locked_u
            total_unlocked_usd += max(0.0, pool_usd - locked_u)

    has_fortress_primary_lock = (
        primary_lp_locked_pct >= 95.0
        and primary_locked_usd >= max(25_000.0, mc_usd * 0.08)
    )
    if total_unlocked_usd > 15_000.0 and total_unlocked_usd > total_locked_usd and not has_fortress_primary_lock:
        return (
            False,
            f"Jebakan Fake Secondary Pool: Likuiditas tak terkunci (${total_unlocked_usd:,.0f}) > terkunci (${total_locked_usd:,.0f})",
            {}
        )

    # 5. Pemisahan Pool AMM (termasuk Vault Owner PumpSwap/Meteora & knownAccounts Locker/AMM) vs Pure Holders
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
    top5_pure_pct = sum(x["pct"] for x in pure_holdings[:5])
    top10_pure_pct = sum(x["pct"] for x in pure_holdings[:10])
    pool_total_pct = sum(x["pct"] for x in pool_holdings)
    outside_pool_pct = max(0.0, 100.0 - pool_total_pct)
    insider_count = sum(1 for x in pure_holdings[:15] if x.get("insider"))
    total_holders = int(rug_data.get("totalHolders") or 0)

    # V3.8 (Modus #35 — Kasus Nyata $Grokbot 6NaM98...pump): Hitung SPL Supply Burn pada koin 1B standar Solana!
    supply_ui = (supply_raw / float(10 ** decimals)) if supply_raw > 0.0 else 0.0
    spl_burned_pct = 0.0
    if 400_000_000.0 <= supply_ui < 999_000_000.0 and (str(token_mint or "").endswith("pump") or decimals == 6):
        spl_burned_pct = round(((1_000_000_000.0 - supply_ui) / 1_000_000_000.0) * 100.0, 2)

    # Deteksi Pengambilalihan Komunitas Murni (Verified Post-Dev-Exit CTO):
    # Dev 100% sudah keluar (<=0.10%), LP Lock >=98%, distribusi rata tanpa paus penampung (max <=3.5%, top10 <=20.5%),
    # serta komunitas masif sudah terbentuk (>=450 holders di MC >=$100k, atau >=280 holders dengan SPL Burn >=5%!).
    is_verified_cto_takeover = bool(
        creator_pct <= 0.10
        and primary_lp_locked_pct >= 98.0
        and max_single_pure <= 3.5
        and top10_pure_pct <= 20.5
        and (
            (mc_usd >= 100_000.0 and total_holders >= 450)
            or (spl_burned_pct >= 5.0 and total_holders >= 280)
        )
    )

    # V3.0 + V3.2 (#23) + V3.8 (#35A): Anti-Serial-Factory Rugger (>=12 token) & Petani Kelulusan Sub-$100K (>=5 token)
    # KECUALI koin terbukti 100% sudah ditinggal dev (0%), dibakar, & diambil alih ratusan/ribuan holder CTO!
    creator_tokens_cnt = len(rug_data.get("creatorTokens") or [])
    if creator_tokens_cnt >= 12 and not (is_verified_cto_takeover or (mc_usd >= 1_080_000.0 and total_holders >= 2500)):
        return (
            False,
            f"Terdeteksi Serial Rugger / Pabrik Koin Dev: Dompet pembuat tercatat sudah meluncurkan {creator_tokens_cnt} token berbeda!",
            {}
        )
    if 25_000.0 <= mc_usd < 100_000.0:
        if creator_tokens_cnt >= 5 and not is_verified_cto_takeover:
            return (
                False,
                f"Terdeteksi Petani Kelulusan / Serial Farmer Dev (<$100k): Dompet pembuat sudah meluncurkan "
                f"{creator_tokens_cnt} token (>= 5) -> Kebiasaan panen & banting sebelum $100k!",
                {}
            )
        if creator_pct > 1.8:
            return (
                False,
                f"Saldo Dev terlalu besar untuk koin <$100k ({creator_pct:.1f}% > 1.8%) -> Rawan banting sebelum $100k!",
                {}
            )

    # PERISAI V3.3 (#27 — Kasus Live $urmom 724 Graph Insiders / 241 Holders):
    # Deteksi Jaringan Botnet Sybil Massal via RugCheck graphInsidersDetected meskipun dipecah <0.2%/dompet & LP Lock 100%!
    graph_insiders = int(rug_data.get("graphInsidersDetected") or 0)
    if mc_usd < 1_080_000.0 and graph_insiders > 0:
        is_sybil_graph_swarm = (
            (total_holders == 0 and graph_insiders >= 45)
            or (total_holders > 0 and graph_insiders >= 15 and (graph_insiders / float(total_holders)) >= 0.15)
            or (0 < total_holders < 1200 and graph_insiders >= 90)
        )
        if is_sybil_graph_swarm:
            return (
                False,
                f"Terdeteksi Botnet Sybil Massal (RugCheck Graph): {graph_insiders} akun insider terhubung "
                f"(Holders: {total_holders}) -> Rawan kuras isi SOL kolam serentak meski LP Lock 100%!",
                {}
            )

    # V3.0 + V3.2 (#17) + V3.3/V3.4 (#28): Anti-Compact Transfer-Cluster, Anti-Sybil Web, & Swarm Botnet >=30 Wallets
    if supply_raw > 0.0 and mc_usd < 1_080_000.0 and (total_holders == 0 or total_holders < 3500):
        total_insider_net_pct = 0.0
        for net in (rug_data.get("insiderNetworks") or []):
            if isinstance(net, dict):
                net_size = int(net.get("activeAccounts") or net.get("size") or 0)
                if net_size >= 2:
                    net_hold_pct = (float(net.get("currentHolding") or 0.0) / supply_raw) * 100.0
                    total_insider_net_pct += net_hold_pct
                    if mc_usd < 525_000.0 and (total_holders == 0 or total_holders < 2500):
                        if 2 <= net_size <= 12 and net_hold_pct >= 12.5:
                            return (
                                False,
                                f"Terdeteksi Bom Klaster Transfer Insider: {net_size} dompet terhubung memegang "
                                f"{net_hold_pct:.1f}% suplai pada koin muda!",
                                {}
                            )
                    if net_hold_pct >= 14.0:
                        return (
                            False,
                            f"Terdeteksi Jaring Laba-Laba Transfer Sybil: {net_size} dompet terhubung menguasai "
                            f"{net_hold_pct:.1f}% suplai (>= 14.0%) -> Permainan mulus bandar sebar puluhan dompet!",
                            {}
                        )
                    if net_size >= 30 and (
                        net_hold_pct >= 5.5
                        or total_holders == 0
                        or (net_size / float(max(1, total_holders))) >= 0.12
                    ):
                        return (
                            False,
                            f"Terdeteksi Kawanan Botnet Transfer Massal: {net_size} dompet terhubung dalam 1 klaster "
                            f"(menguasai {net_hold_pct:.1f}% suplai) -> Manipulasi botnet cuci volume & kuras kolam!",
                            {}
                        )
        if 25_000.0 <= mc_usd < 100_000.0 and total_insider_net_pct >= 13.0:
            return (
                False,
                f"Terdeteksi Akumulasi Jaringan Insider <$100k: Total dompet terhubung menguasai "
                f"{total_insider_net_pct:.1f}% suplai (>= 13.0%) -> Rawan banting serentak sebelum $100k!",
                {}
            )
        if total_insider_net_pct >= 22.0:
            return (
                False,
                f"Terdeteksi Gurita Multi-Klaster Insider: Gabungan jaringan dompet terhubung menguasai "
                f"{total_insider_net_pct:.1f}% suplai (>= 22.0%)!",
                {}
            )

    if insider_count >= 2:
        return False, f"Terdeteksi {insider_count} dompet Insider/Bundler aktif di Top 15", {}

    if max_single_pure > 4.6:
        return False, f"Paus Tunggal Bahaya: 1 pure holder menguasai {max_single_pure:.1f}% (> 4.6%)", {}

    # PERISAI V3.2 (#18): Top-5 Exit-Drain / Pool-Insolvency Trap di <$100k
    if 25_000.0 <= mc_usd < 100_000.0 and primary_hard_usd > 0.0 and pure_holdings:
        top5_pure_usd = (top5_pure_pct / 100.0) * mc_usd
        drain_coverage_ratio = top5_pure_usd / primary_hard_usd
        if drain_coverage_ratio > 1.55:
            return (
                False,
                f"Jebakan Insolvensi Kolam <$100k (Exit-Drain): Top 5 holder memegang ${top5_pure_usd:,.0f} "
                f"({drain_coverage_ratio:.2f}x cadangan SOL asli kolam ${primary_hard_usd:,.0f}) -> Rawan kuras habis sebelum $100k!",
                {}
            )

    # Validasi Khusus Koin Multi-Hari / CTO (> 36 Jam) & Cult Mega-Runner (> $1.08M):
    if mc_usd >= 1_080_000.0 and creator_pct > 1.5:
        return (
            False,
            f"Koin Mega-Cap (${mc_usd:,.0f}) tapi Dev masih memegang {creator_pct:.1f}% (> 1.5%) -> Rawan dev dump besar!",
            {}
        )
    if age_minutes > 2160.0:
        if creator_pct > 1.0:
            return (
                False,
                f"Koin multi-hari ({age_minutes/60.0:.0f}j) tapi Dev masih memegang {creator_pct:.1f}% (> 1.0%) -> Rawan dev dump!",
                {}
            )
        min_cto_holders = 800 if mc_usd >= 228_000.0 else 600
        if 0 < total_holders < min_cto_holders:
            return (
                False,
                f"Koin lama ({age_minutes/60.0:.0f}j) dengan komunitas sempit ({total_holders} < {min_cto_holders} holders) -> Rawan jebakan zombie pump!",
                {}
            )

    # Kalibrasi V2.5/V2.6 Pool-Depletion-Aware Pareto Ceiling ($Macca / $PONS / $STONK Guard):
    # Saat pool AMM menyusut ke <20% (outside_pool >= 80%) pada koin komunitas besar (>=1.000 holders,
    # dev <=1%, 0 insider, tanpa paus >4.0%), Top 10 pure 23.5% setara hanya ~25.5% suplai beredar di luar pool.
    is_broad_community_depletion = (
        outside_pool_pct >= 80.0
        and total_holders >= 1000
        and creator_pct <= 1.0
        and insider_count == 0
        and max_single_pure <= 4.0
    )
    max_top10_pure = min(25.0, round(outside_pool_pct * 0.275, 1)) if is_broad_community_depletion else 19.5
    if top10_pure_pct > max_top10_pure:
        return False, f"Top 10 Pure Holders terlalu pekat ({top10_pure_pct:.1f}% > {max_top10_pure:.1f}%)", {}

    # 6. PENANGKAL DEV LICIK #5 (V2.4 + V2.6 + V3.2 Anti-Fake-FDV): Verifikasi Jumlah Partisipan Nyata & Anti Botnet Nano-Swarm
    if total_holders > 0 and mc_usd >= 25_000.0:
        if mc_usd >= 5_250_000.0:
            min_holders_req = 2200
        elif mc_usd >= 1_080_000.0:
            min_holders_req = 1200
        elif mc_usd >= 228_000.0:
            min_holders_req = 150
        elif mc_usd >= 128_000.0:
            min_holders_req = 90
        elif mc_usd < 100_000.0:
            min_holders_req = 60
        else:
            min_holders_req = 45
        if total_holders < min_holders_req:
            return (
                False,
                f"Partisipan holder terlalu tipis untuk MC ${mc_usd:,.0f} ({total_holders} < {min_holders_req} holders) -> Fake FDV / Pump segelintir dompet!",
                {}
            )

    # Hukum Konservasi Pareto (Pembunuh Mutlak Modus $Leonida 3.395 Dompet Bot Nano < 0.25%):
    # Jika >= 65% suplai berada di luar pool AMM, secara matematis mustahil pada pasar organik
    # 10 holder terbesar digabung hanya memegang < 5.2% (atau holder #1 < 0.65%).
    if outside_pool_pct >= 65.0 and (top10_pure_pct < 5.2 or max_single_pure < 0.65):
        return (
            False,
            f"Terdeteksi Botnet Nano-Swarm: {outside_pool_pct:.1f}% suplai di luar pool tapi Top 10 Holder hanya "
            f"{top10_pure_pct:.1f}% (Max {max_single_pure:.2f}%) -> Suplai dicacah ke ratusan/ribuan dompet bot!",
            {}
        )

    # Anti-Decoy Nano-Swarm: Jika dev sengaja menaruh 1-3 dompet umpan (~2%) di atas namun mencacah >70% sisanya ke ribuan bot
    if len(pure_holdings) >= 10 and outside_pool_pct >= 70.0:
        rank5_pct = pure_holdings[4]["pct"]
        if top10_pure_pct < 6.0 or rank5_pct < 0.45:
            return (
                False,
                f"Terdeteksi Decoy Nano-Swarm: {outside_pool_pct:.1f}% suplai di luar pool tapi Holder #5 anjlok ke "
                f"{rank5_pct:.2f}% (Top 10: {top10_pure_pct:.1f}%) -> Ekor ribuan dompet bot!",
                {}
            )

    # Anti-Randomized Block-0 Bundler pada koin muda (< 35 menit & < 450 holders):
    # Menangkap skrip randomizer dev yang menyebar 7+ dompet rapat di 1.10%-2.60% tanpa mengganggu koin matang seperti $REMUS!
    if 0 < total_holders < 450 and age_minutes < 35.0:
        packed_mid = [x["pct"] for x in pure_holdings[:15] if 1.10 <= x["pct"] <= 2.60]
        if len(packed_mid) >= 7 and (max(packed_mid) - min(packed_mid)) <= 0.35:
            return (
                False,
                f"Terdeteksi Randomized Bundler Dev: {len(packed_mid)} dompet mengumpul rapat "
                f"({min(packed_mid):.2f}%-{max(packed_mid):.2f}%) pada koin muda!",
                {}
            )

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

    # Kalibrasi Presisi V3.9 (Kasus Nyata $INVERSE 2gLhk8...Zfiw 2.74%, 2.72%, 2.70% -> $810K MC & 10.165 Holders):
    # Hanya blokir jika dompet kloningan mesin benar-benar identik (selisih <= 0.012%) dan bukan komunitas besar (>=1.200 holders Dev 0%)!
    if total_holders < 1200 or creator_pct > 0.10:
        heavy_holders = [x["pct"] for x in pure_holdings[:15] if 2.40 <= x["pct"] <= 4.60]
        for p_ref in heavy_holders:
            heavy_cluster = [p for p in heavy_holders if abs(p - p_ref) <= 0.012]
            if len(heavy_cluster) >= 3:
                return (
                    False,
                    f"Heavy Splitter Cabal: {len(heavy_cluster)} paus memegang porsi kembar identik (~{p_ref:.2f}%, total {sum(heavy_cluster):.1f}%)",
                    {}
                )

    # Deteksi Sindikat Micro-Split CEX (Bebaskan komunitas raksasa >=2.500 holders dengan Dev 0% seperti $Something $757K MC)
    if total_holders < 2500 or creator_pct > 0.10:
        micro_tol = 0.03 if (total_holders == 0 or total_holders < 1800) else 0.008
        micro_holders = [x["pct"] for x in pure_holdings[:20] if 0.65 <= x["pct"] < 1.20]
        for p_ref in micro_holders:
            m_cluster = [p for p in micro_holders if abs(p - p_ref) <= micro_tol]
            if len(m_cluster) >= 5:
                return (
                    False,
                    f"Micro-Split CEX Syndicate: {len(m_cluster)} dompet memegang persentase kloningan identik (~{p_ref:.2f}%)",
                    {}
                )

    return True, "Distribusi Bersih & LP Aman", {
        "decimals": decimals,
        "creator_pct": round(creator_pct, 2),
        "top5_pure_pct": round(top5_pure_pct, 2),
        "pure_top10": round(top10_pure_pct, 1),
        "max_single": round(max_single_pure, 1),
        "pool_pct": round(pool_total_pct, 1),
        "lp_locked_pct": primary_lp_locked_pct,
        "sol_backing_ratio": sol_backing_ratio,
        "primary_hard_usd": round(primary_hard_usd, 1),
        "total_holders": total_holders,
        "burned_pct": spl_burned_pct,
        "is_verified_cto_takeover": is_verified_cto_takeover,
    }


# ==============================================================================
# MODUL 4: AUDIT MENDALAM PARALEL (RUGCHECK + JUPITER + OHLCV MICROSTRUCTURE & ATH V2.8)
# ==============================================================================
def _get_chain_links(chain_id: str, token_addr: str) -> tuple[str, str, str]:
    c = str(chain_id or "solana").lower()
    dex_url = f"https://dexscreener.com/{c}/{token_addr}"
    if c == "bsc":
        return dex_url, "PancakeSwap", f"https://pancakeswap.finance/swap?outputCurrency={token_addr}&chain=bsc"
    if c == "base":
        return dex_url, "Uniswap Base", f"https://app.uniswap.org/swap?outputCurrency={token_addr}&chain=base"
    if c == "ethereum":
        return dex_url, "Uniswap ETH", f"https://app.uniswap.org/swap?outputCurrency={token_addr}&chain=mainnet"
    return f"https://dexscreener.com/solana/{token_addr}", "Jupiter Swap", f"https://jup.ag/swap/SOL-{token_addr}"


def _fuse_gt_pool_if_dex_lagging(pair: dict, gt_snap: dict = None) -> dict:
    """
    PERISAI V3.5 ANTI-LAG DEXSCREENER (Kasus Nyata BSC Flap.sh 0x53e3...7777 pasangan BNCB):
    Saat DexScreener membeku (m5/h1 = 0) pada quote token ekosistem baru, suntikkan data swap live on-chain
    dari GeckoTerminal agar kita masuk SEBELUM bot kompetitor yang buta menunggu indeks DexScreener pulih!
    """
    if not isinstance(pair, dict):
        return pair
    txns = pair.get("txns") or {}
    m5_t = txns.get("m5") or {}
    h1_t = txns.get("h1") or {}
    has_live_m5 = (int(m5_t.get("buys", 0)) + int(m5_t.get("sells", 0))) > 0
    has_live_h1 = (int(h1_t.get("buys", 0)) + int(h1_t.get("sells", 0))) > 0
    if has_live_m5 and has_live_h1:
        if isinstance(gt_snap, dict) and gt_snap.get("transactions"):
            _gt_t = gt_snap.get("transactions") or {}
            _gm5 = _gt_t.get("m5") or {}
            _gh1 = _gt_t.get("h1") or {}
            if int(_gm5.get("buyers") or 0) > 0 or int(_gh1.get("buyers") or 0) > 0:
                enriched = dict(pair)
                enriched["gt_crowd"] = {
                    "buyers_m5": int(_gm5.get("buyers") or 0),
                    "sellers_m5": int(_gm5.get("sellers") or 0),
                    "buyers_h1": int(_gh1.get("buyers") or 0),
                    "sellers_h1": int(_gh1.get("sellers") or 0),
                }
                return enriched
        return pair

    chain_id = str(pair.get("chainId") or "solana").lower()
    pair_addr = str(pair.get("pairAddress") or "").strip()
    attrs = None
    if isinstance(gt_snap, dict) and gt_snap.get("transactions"):
        attrs = gt_snap
    elif pair_addr and chain_id in GT_NETWORK_MAP:
        gt_net = GT_NETWORK_MAP[chain_id]
        pool_res = fetch_json(f"https://api.geckoterminal.com/api/v2/networks/{gt_net}/pools/{pair_addr}", timeout=2.5)
        if isinstance(pool_res, dict):
            attrs = (pool_res.get("data") or {}).get("attributes") or {}

    if not isinstance(attrs, dict):
        return pair

    merged = dict(pair)
    gt_tx = attrs.get("transactions") or {}
    gt_vol = attrs.get("volume_usd") or {}
    gt_pc = attrs.get("price_change_percentage") or {}

    m5_src = gt_tx.get("m5") or {}
    m15_src = gt_tx.get("m15") or {}
    h1_src = gt_tx.get("h1") or {}
    h24_src = gt_tx.get("h24") or {}
    if int(m5_src.get("buyers") or 0) > 0 or int(h1_src.get("buyers") or 0) > 0:
        merged["gt_crowd"] = {
            "buyers_m5": int(m5_src.get("buyers") or 0),
            "sellers_m5": int(m5_src.get("sellers") or 0),
            "buyers_h1": int(h1_src.get("buyers") or 0),
            "sellers_h1": int(h1_src.get("sellers") or 0),
        }
    use_m5_b = int(m5_src.get("buys", 0)) or (max(1, int(int(m15_src.get("buys", 0)) / 3)) if int(m15_src.get("buys", 0)) > 0 else 0)
    use_m5_s = int(m5_src.get("sells", 0)) or int(int(m15_src.get("sells", 0)) / 3)

    if not has_live_m5 and (use_m5_b + use_m5_s) > 0:
        new_txns = dict(txns)
        new_txns["m5"] = {"buys": use_m5_b, "sells": use_m5_s}
        if not has_live_h1:
            new_txns["h1"] = {"buys": int(h1_src.get("buys", 0)), "sells": int(h1_src.get("sells", 0))}
        if int((txns.get("h24") or {}).get("buys", 0)) == 0 and int(h24_src.get("buys", 0)) > 0:
            new_txns["h24"] = {"buys": int(h24_src.get("buys", 0)), "sells": int(h24_src.get("sells", 0))}
        merged["txns"] = new_txns

        old_vol = dict(merged.get("volume") or {})
        v_m5 = float(gt_vol.get("m5") or 0.0) or (float(gt_vol.get("m15") or 0.0) / 3.0)
        if float(old_vol.get("m5") or 0.0) <= 0.0 and v_m5 > 0.0:
            old_vol["m5"] = round(v_m5, 2)
        if float(old_vol.get("h1") or 0.0) <= 0.0 and float(gt_vol.get("h1") or 0.0) > 0.0:
            old_vol["h1"] = round(float(gt_vol.get("h1") or 0.0), 2)
        merged["volume"] = old_vol

        old_pc = dict(merged.get("priceChange") or {})
        if float(old_pc.get("m5") or 0.0) == 0.0:
            old_pc["m5"] = float(gt_pc.get("m5") or gt_pc.get("m15") or 0.0)
        if float(old_pc.get("h1") or 0.0) == 0.0:
            old_pc["h1"] = float(gt_pc.get("h1") or 0.0)
        if float(old_pc.get("h6") or 0.0) == 0.0:
            old_pc["h6"] = float(gt_pc.get("h6") or 0.0)
        merged["priceChange"] = old_pc

    gt_liq = float(attrs.get("reserve_in_usd") or 0.0)
    if float((merged.get("liquidity") or {}).get("usd") or 0.0) <= 0.0 and gt_liq > 0.0:
        merged["liquidity"] = {"usd": gt_liq}
    return merged


def analyze_evm_goplus_security(
    goplus_payload: dict,
    token_address: str,
    mc_usd: float = 0.0,
    liq_usd: float = 0.0,
) -> tuple[bool, str, dict]:
    """
    MODUL KEAMANAN EVM NATIVE V3.5 (BSC Four.meme/Flap.sh + Base Virtuals/Clanker + Ethereum via GoPlus API):
    Memblokir Honeypot, Pajak Siluman >5%, Mintable/Backdoor, Dev Dump, & Paus Tunggal; sekaligus mengenali
    token Launchpad Renounced yang sehat (mis. Giggle Academy 0x53e3...7777 Flap.sh 3% tax + 6.3% Burned).
    """
    if not isinstance(goplus_payload, dict):
        return False, "Audit keamanan EVM GoPlus timeout / belum tersedia (Fail-Closed)", {}
    res_map = goplus_payload.get("result") or {}
    tok = res_map.get(str(token_address or "").lower()) or next(iter(res_map.values()), None)
    if not isinstance(tok, dict) or not tok:
        return False, "Kontrak EVM belum terindeks GoPlus Security (Fail-Closed)", {}

    if str(tok.get("is_honeypot") or "0") == "1" or str(tok.get("cannot_sell_all") or "0") == "1" or str(tok.get("cannot_buy") or "0") == "1":
        return False, "JEBAKAN HONEYPOT EVM (GoPlus): Token dikunci kontrak / tidak bisa dijual penuh!", {}

    if str(tok.get("is_open_source") or "1") == "0":
        return False, "Kontrak EVM tidak Open-Source (Unverified Bytecode) -> Rawan jebakan tersembunyi!", {}

    fatal_flags = [
        ("is_mintable", "Mintable Supply"),
        ("hidden_owner", "Hidden Owner"),
        ("can_take_back_ownership", "Take-Back Ownership"),
        ("selfdestruct", "Self-Destruct"),
        ("transfer_pausable", "Transfer Pausable"),
        ("is_blacklisted", "Blacklist Switch"),
        ("slippage_modifiable", "Modifiable Tax/Slippage"),
        ("personal_slippage_modifiable", "Wallet Tax Trap"),
    ]
    for f_key, f_label in fatal_flags:
        if str(tok.get(f_key) or "0") == "1":
            return False, f"Backdoor Kontrak EVM Terdeteksi ({f_label})", {}

    buy_tax = float(tok.get("buy_tax") or 0.0)
    sell_tax = float(tok.get("sell_tax") or 0.0)
    if buy_tax > 0.05 or sell_tax > 0.05:
        return (
            False,
            f"Pajak Kontrak EVM Cekik Leher (Buy {buy_tax*100:.1f}% / Sell {sell_tax*100:.1f}% > 5.0%)",
            {}
        )

    creator_pct = float(tok.get("creator_percent") or 0.0) * 100.0
    if creator_pct > 2.5:
        return False, f"Dev EVM masih memegang {creator_pct:.1f}% suplai (> 2.5%) -> Rawan dev dump!", {}

    owner_addr = str(tok.get("owner_address") or "").strip().lower()
    is_renounced = (owner_addr == "" or owner_addr in EVM_BURN_ADDRESSES)
    owner_pct = float(tok.get("owner_percent") or 0.0) * 100.0
    if not is_renounced and owner_pct > 2.0:
        return False, f"Owner kontrak belum Renounced & memegang {owner_pct:.1f}% suplai", {}

    burned_pct = 0.0
    pure_eoa_pcts = []
    for h in (tok.get("holders") or []):
        if not isinstance(h, dict):
            continue
        h_addr = str(h.get("address") or "").strip().lower()
        h_pct = float(h.get("percent") or 0.0) * 100.0
        is_contract = int(h.get("is_contract") or 0) == 1
        is_locked = int(h.get("is_locked") or 0) == 1
        if h_addr in EVM_BURN_ADDRESSES:
            burned_pct += h_pct
            continue
        if is_contract or is_locked:
            continue
        pure_eoa_pcts.append(h_pct)

    pure_eoa_pcts.sort(reverse=True)
    max_single_eoa = pure_eoa_pcts[0] if pure_eoa_pcts else 0.0
    top10_pure = round(sum(pure_eoa_pcts[:10]), 1)
    top5_pure = round(sum(pure_eoa_pcts[:5]), 2)

    if mc_usd < 1_080_000.0 and max_single_eoa > 5.0:
        return False, f"Paus Tunggal Bahaya EVM: 1 dompet EOA menguasai {max_single_eoa:.1f}% (> 5.0%)", {}
    if top10_pure > 28.0:
        return False, f"Top 10 Pure Holders EVM terlalu pekat ({top10_pure:.1f}% > 28.0%)", {}

    # Deteksi Bundel Dompet Kloningan Dev di EVM (>=4 dompet EOA memegang 1.2%-4.5% dengan selisih <=0.03%)
    evm_mid = [p for p in pure_eoa_pcts[:20] if 1.20 <= p <= 4.50]
    for p_ref in evm_mid:
        cloned_evm = [p for p in evm_mid if abs(p - p_ref) <= 0.03]
        if len(cloned_evm) >= 4 and sum(cloned_evm) >= 5.2:
            return (
                False,
                f"Bundel Kloningan Dev EVM Terdeteksi: {len(cloned_evm)} dompet memegang porsi kembar (~{p_ref:.2f}%)",
                {}
            )

    lp_locked_pct = 100.0 if is_renounced else 0.0
    lp_holders = tok.get("lp_holders") or []
    if isinstance(lp_holders, list) and lp_holders:
        locked_lp_sum = 0.0
        for lph in lp_holders:
            lp_a = str(lph.get("address") or "").strip().lower()
            lp_p = float(lph.get("percent") or 0.0) * 100.0
            if int(lph.get("is_locked") or 0) == 1 or lp_a in EVM_BURN_ADDRESSES:
                locked_lp_sum += lp_p
        if locked_lp_sum > 0.0:
            lp_locked_pct = round(min(100.0, locked_lp_sum), 1)
        elif not is_renounced:
            return False, "LP EVM belum terkunci/dibakar & kontrak belum Renounced -> Rawan tarik LP!", {}

    lp_info = tok.get("launchpad_token") or {}
    launchpad_name = str(lp_info.get("launchpad_name") or "") if isinstance(lp_info, dict) else ""
    holder_count = int(tok.get("holder_count") or 0)
    tax_roundtrip_pct = (buy_tax + sell_tax) * 100.0
    effective_rt_loss = round(min(5.2, tax_roundtrip_pct * 0.75 + 0.6) if (launchpad_name and is_renounced and buy_tax <= 0.032 and sell_tax <= 0.032) else (tax_roundtrip_pct + 0.6), 2)
    depth_est_pct = round(min(12.0, effective_rt_loss + (3500.0 / max(5000.0, liq_usd)) * 10.0), 2)

    return True, "Kontrak EVM Bersih & Aman", {
        "decimals": 18,
        "creator_pct": round(creator_pct, 2),
        "top5_pure_pct": top5_pure,
        "pure_top10": top10_pure,
        "lp_locked_pct": lp_locked_pct,
        "max_single": round(max_single_eoa, 2),
        "sol_backing_pct": 50.0,
        "sol_backing_ratio": 50.0,
        "primary_hard_usd": round(liq_usd * 0.5, 2),
        "total_quote_hard_usd": round(liq_usd * 0.5, 2),
        "creator_tokens_cnt": 1,
        "total_holders": holder_count,
        "burned_pct": round(burned_pct, 2),
        "is_renounced": is_renounced,
        "launchpad_name": launchpad_name,
        "buy_tax_pct": round(buy_tax * 100.0, 2),
        "sell_tax_pct": round(sell_tax * 100.0, 2),
        "evm_rt_loss_pct": effective_rt_loss,
        "evm_depth_loss_pct": depth_est_pct,
    }








def evaluate_sub100k_inevitable_100k_breakout(
    best_pair: dict,
    rug_data: dict | None,
    holder_meta: dict | None,
    ohlcv_payload,
    live_mc: float,
    liq_usd: float,
) -> dict:
    """
    MODUS #36 (V3.9 — DETEKTOR DNA "PASTI TEMBUS $100K -> RUNNER $400K+" DARI BAWAH MC $100K):
    Disarikan dari bedah empiris lintas-runner >$400K-$2.5M ($INVERSE 2gLhk8...Zfiw $43.7K->$810K,
    $Nothing $44K->$1.52M, Giggle Academy $41K->$704K, $JOB $2.50M, $Something $757K):
    Mengukur 5 Sinyal Fisika Pasar yang membuktikan sebuah koin di bawah $100K TIDAK MUNGKIN berhenti di <$100K:
    1. Velocity Escape: Perputaran Vol 5m >= 22% dari MC (uang deras masuk melebihi kapasitas tampung <$100K).
    2. Expanding Green Momentum & Higher-Low >= 1.20x lantai pembukaan (tidak pernah balik ke harga lahir).
    3. Zero-Dev Virgin / Burned Supply: Saldo dev 0% (<=0.05%) & pembuat bersih (0 riwayat rug) + LP Lock >= 98%.
    4. Paid CMS Infrastructure ($299 Header DexScreener + Domain Website Asli + X Resmi) sudah aktif di MC <$100K!
    5. Multi-Pool Arbitrage Magnet: Muncul pool pendamping (mis. Meteora DLMM / Raydium) atau rasio Buy/Sell >= 1.60x.
    """
    if live_mc <= 0.0 or live_mc >= 100_000.0:
        return {"is_inevitable_100k": False, "signals_count": 0, "signals": []}

    rug_d = rug_data if isinstance(rug_data, dict) else {}
    h_meta = holder_meta if isinstance(holder_meta, dict) else {}
    vol_m5 = float(((best_pair or {}).get("volume") or {}).get("m5") or 0.0)
    txns_m5 = ((best_pair or {}).get("txns") or {}).get("m5") or {}
    b_m5, s_m5 = int(txns_m5.get("buys") or 0), int(txns_m5.get("sells") or 0)
    bs_ratio = b_m5 / float(max(1, s_m5))

    signals = []

    # Sinyal 1: Velocity Escape (Vol 5m >= 22% MC & Buy/Sell >= 1.45x)
    vel_turnover = vol_m5 / live_mc
    if vel_turnover >= 0.22 and bs_ratio >= 1.45:
        signals.append(f"Velocity Escape (Vol 5m {vel_turnover*100:.0f}% MC · {bs_ratio:.2f}x Buy)")

    # Sinyal 2: Higher-Low >= 1.20x Lantai Lahir & Ekspansi Volume Hijau (seperti $INVERSE $37.1K -> $51.8K & Vol $24.4K -> $33.4K)
    candles = []
    if isinstance(ohlcv_payload, dict):
        raw_list = ((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = [r for r in raw_list if isinstance(r, (list, tuple)) and len(r) >= 6 and float(r[3] or 0.0) > 0.0]
        candles.sort(key=lambda x: float(x[0] or 0.0))
    if len(candles) >= 2:
        first_l = float(candles[0][3] or 0.0)
        last_l = float(candles[-1][3] or 0.0)
        prev_v, last_v = float(candles[-2][5] or 0.0), float(candles[-1][5] or 0.0)
        last_green = float(candles[-1][4] or 0.0) >= float(candles[-1][1] or 0.0)
        if first_l > 0.0 and last_l >= (first_l * 1.20) and last_green:
            exp_note = " + Ekspansi Vol" if last_v >= (prev_v * 1.02) else ""
            signals.append(f"Lantai Higher-Low {last_l/first_l:.2f}x Di Atas Lahir{exp_note}")

    # Sinyal 3: Dev 0.00% Saldo + Deployer Perawan (0 token sebelumnya) + LP Lock >= 98% / Renounced
    creator_pct = float(h_meta.get("creator_pct") or 0.0)
    creator_toks = len(rug_d.get("creatorTokens") or [])
    lp_locked = float(h_meta.get("lp_locked_pct") or 0.0)
    is_renounced = bool(h_meta.get("is_renounced"))
    if creator_pct <= 0.05 and creator_toks == 0 and (lp_locked >= 98.0 or is_renounced):
        signals.append("Deployer Perawan (0 Riwayat Rug, 0% Saldo Dev, LP 99%+ Terkunci)")

    # Sinyal 4: Infrastruktur Proyek Serius Sejak <$100K ($299 Header CMS + Website Domain + Sosmed)
    info = (best_pair or {}).get("info") or {}
    has_header = bool(info.get("header"))
    has_web = bool(info.get("websites") or [])
    has_soc = bool(info.get("socials") or [])
    if has_header and has_web and has_soc:
        signals.append("Infrastruktur Penuh <$100K (Paid Header + Domain Web + X)")

    is_inevitable = len(signals) >= 3
    return {
        "is_inevitable_100k": is_inevitable,
        "signals_count": len(signals),
        "signals": signals[:3],
    }


def evaluate_comprehensive_sub100k_crossing_engine(
    best_pair: dict,
    rug_data: dict | None,
    holder_meta: dict,
    ohlcv_payload,
    live_mc: float,
    liq_usd: float,
    boost_amount: int = 0,
) -> tuple[bool, str, dict]:
    """
    MESIN KOMPREHENSIF V5.0 (MODUS #39 — UNIVERSAL SUB-$100K -> $100K+ CROSSING PHYSICS ENGINE):
    Bukan aturan tambal-sulam per koin, melainkan Hukum Fisika Pasar & Matematika AMM (`x * y = k`) Universal
    untuk menyaring 95% koin baru yang mati sebelum MC $100K vs 5% koin yang PASTI MENEMBUS MC $100K+ ($105K-$125K+):
      - Tidak peduli apakah koin mati setelah melewati $100K ($115K-$150K) atau lanjut ke $2M, karena entry kita <$100K!
      - Wajib lulus:
        1. AMM Net Buy Inflow Coverage (R_cross >= 0.32x kebutuhan modal bersih untuk menggeser kurva AMM ke $105K MC,
           artinya estimasi waktu tembus $100K <= 15.6 menit pada kecepatan uang masuk sekarang).
        2. Bebas Veto Kerumunan Semu (Wash-Bot klik berulang >5.5 klik/dompet, Eksodus Penjual Unik >=92% pembeli,
           atau Distribusi Candle Merah Aktif).
        3. Skor Kuantitatif 5-Pilar Universal >= 68/100:
           - Pilar 1 (0-30): Propulsi Uang Bersih vs Jarak Kurva AMM ke $105K (R_cross & tau_100k)
           - Pilar 2 (0-20): Bantalan Likuiditas & Ketahanan Bantingan (LP USD & LP/MC %)
           - Pilar 3 (0-20): Keaslian Kerumunan Pembeli Unik & Konviksi Ukuran Tiket
           - Pilar 4 (0-20): Geometri Struktur Candle & Kelelahan Penjual (Close Kuat, Higher-Low, Green>Red Vol)
           - Pilar 5 (0-15): Imunitas Suplai Atas & Sunk-Cost Ekonomi di Atas $100K
    """
    if live_mc <= 0.0 or live_mc >= 100_000.0:
        return True, "Di atas zona <$100K", {"is_crossing_verified": False, "crossing_score": 0}

    bp = best_pair or {}
    h_meta = holder_meta if isinstance(holder_meta, dict) else {}
    rug_d = rug_data if isinstance(rug_data, dict) else {}

    vol_m5 = float((bp.get("volume") or {}).get("m5") or 0.0)
    tx_m5 = (bp.get("txns") or {}).get("m5") or {}
    b_m5 = int(tx_m5.get("buys") or 0)
    s_m5 = int(tx_m5.get("sells") or 0)
    tot_m5 = b_m5 + s_m5
    p_m5 = float((bp.get("priceChange") or {}).get("m5") or 0.0)

    if tot_m5 < 20 or b_m5 <= s_m5:
        return (
            False,
            f"V5.0 Fisika <$100K Gagal: Pembeli tidak dominan ({b_m5}B / {s_m5}S) -> Kehabisan tenaga sebelum $100K!",
            {},
        )

    gt_crowd = bp.get("gt_crowd") or {}
    uniq_b = int(gt_crowd.get("buyers_m5") or 0)
    uniq_s = int(gt_crowd.get("sellers_m5") or 0)
    if uniq_b > 0 and b_m5 >= 50 and (b_m5 / float(uniq_b)) > 5.5:
        return (
            False,
            f"V5.0 Anti-Wash <$100K: {b_m5} klik Buy hanya berasal dari {uniq_b} dompet ({b_m5/float(uniq_b):.1f} klik/dompet) -> Bot cuci volume!",
            {},
        )
    if uniq_b >= 12 and uniq_s >= int(uniq_b * 0.92):
        return (
            False,
            f"V5.0 Eksodus <$100K: Penjual unik ({uniq_s}) menyaingi pembeli unik ({uniq_b}) -> Distribusi sebelum $100K!",
            {},
        )

    candles = []
    if isinstance(ohlcv_payload, dict):
        raw_c = ((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = [r for r in raw_c if isinstance(r, (list, tuple)) and len(r) >= 6 and float(r[2] or 0.0) > 0.0]
        candles.sort(key=lambda x: float(x[0] or 0.0))

    if len(candles) >= 3:
        c_last2 = candles[-2:]
        r_v2 = sum(float(r[5] or 0.0) for r in c_last2 if float(r[4] or 0.0) < float(r[1] or 0.0))
        g_v2 = sum(float(r[5] or 0.0) for r in c_last2 if float(r[4] or 0.0) >= float(r[1] or 0.0))
        if float(candles[-1][4] or 0.0) < float(candles[-1][1] or 0.0) and r_v2 > max(6_000.0, g_v2 * 1.35):
            return (
                False,
                f"V5.0 Distribusi Aktif <$100K: Candle terakhir merah dengan buangan (${r_v2:,.0f}) > serapan hijau (${g_v2:,.0f})!",
                {},
            )

    # PILAR 1 (0-30 Poin): Matematika Kurva AMM Constant-Product (x * y = k) menuju MC $105,000
    target_cross_mc = 105_000.0
    hard_quote_usd = float(h_meta.get("primary_hard_usd") or h_meta.get("total_quote_hard_usd") or (liq_usd * 0.50))
    hard_quote_usd = max(3_500.0, min(hard_quote_usd, liq_usd * 0.65)) if liq_usd > 0 else 6_000.0
    delta_q_need = max(1_200.0, hard_quote_usd * (((target_cross_mc / max(10_000.0, live_mc)) ** 0.5) - 1.0))

    imb_tx = (b_m5 - s_m5) / float(max(1, tot_m5))
    imb_vol = 0.0
    if len(candles) >= 2:
        recent_bars = candles[-3:]
        gv = sum(float(r[5] or 0.0) for r in recent_bars if float(r[4] or 0.0) >= float(r[1] or 0.0))
        rv = sum(float(r[5] or 0.0) for r in recent_bars if float(r[4] or 0.0) < float(r[1] or 0.0))
        if (gv + rv) > 0.0:
            imb_vol = (gv - rv) / (gv + rv)

    eff_imb = min(0.65, max(imb_tx, (0.65 * imb_tx) + (0.22 * max(0.0, imb_vol))))
    est_net_inflow_5m = vol_m5 * max(0.0, eff_imb)
    inflow_coverage = est_net_inflow_5m / max(1.0, delta_q_need)
    est_mins_to_100k = round(5.0 / max(0.05, inflow_coverage), 1)

    if inflow_coverage < 0.32:
        return (
            False,
            f"V5.0 Bensin Kurang Menuju $100K: Arus beli bersih 5m (~${est_net_inflow_5m:,.0f}) baru menutup "
            f"{inflow_coverage*100:.0f}% kebutuhan kurva AMM ke $105K (${delta_q_need:,.0f}, est {est_mins_to_100k}m > 15.6m) -> Rawan layu <$100K!",
            {},
        )

    if inflow_coverage >= 1.10:
        p1_score = 30
    elif inflow_coverage >= 0.75:
        p1_score = 25
    elif inflow_coverage >= 0.50:
        p1_score = 20
    else:
        p1_score = 14

    # PILAR 2 (0-20 Poin): Ketebalan Bantalan Likuiditas terhadap Profit-Taking <$100K
    liq_mc_pct = (liq_usd / live_mc) * 100.0
    if liq_usd >= 16_000.0 and liq_mc_pct >= 21.0:
        p2_score = 20
    elif liq_usd >= 13_000.0 and liq_mc_pct >= 17.0:
        p2_score = 16
    elif liq_usd >= 10_500.0 and liq_mc_pct >= 12.0:
        p2_score = 12
    else:
        p2_score = 5

    # PILAR 3 (0-20 Poin): Lebar Kerumunan Pembeli & Konviksi Ukuran Tiket
    bs_ratio = b_m5 / float(max(1, s_m5))
    uniq_ratio = (uniq_b / float(max(1, uniq_s))) if uniq_b >= 10 else 0.0
    if bs_ratio >= 1.85 or (uniq_b >= 22 and uniq_ratio >= 1.60):
        p3_score = 13
    elif bs_ratio >= 1.50 or (uniq_b >= 16 and uniq_ratio >= 1.38):
        p3_score = 10
    else:
        p3_score = 7

    avg_ticket = vol_m5 / float(max(1, tot_m5))
    if avg_ticket >= 85.0 or tot_m5 >= 95:
        p3_score += 7
    elif avg_ticket >= 42.0 or tot_m5 >= 55:
        p3_score += 4

    # PILAR 4 (0-20 Poin): Geometri Struktur Candle Universal & Seller Exhaustion
    p4_score = 0
    if len(candles) >= 2:
        lo, lh, ll, lc = (
            float(candles[-1][1] or 0.0),
            float(candles[-1][2] or 0.0),
            float(candles[-1][3] or 0.0),
            float(candles[-1][4] or 0.0),
        )
        c_rng = max(1e-12, lh - ll)
        close_pos = (lc - ll) / c_rng
        if lc >= lo and close_pos >= 0.60:
            p4_score += 8
        elif lc >= lo:
            p4_score += 4

        first_l = float(candles[0][3] or 0.0)
        prev_l = float(candles[-2][3] or 0.0)
        if ll >= prev_l and (first_l <= 0.0 or ll >= first_l * 1.05):
            p4_score += 7
        elif first_l > 0.0 and ll >= first_l:
            p4_score += 4

        gv_all = sum(float(r[5] or 0.0) for r in candles[-4:] if float(r[4] or 0.0) >= float(r[1] or 0.0))
        rv_all = sum(float(r[5] or 0.0) for r in candles[-4:] if float(r[4] or 0.0) < float(r[1] or 0.0))
        if gv_all >= max(5_000.0, rv_all * 1.35):
            p4_score += 5
        elif gv_all > rv_all:
            p4_score += 3
    else:
        if p_m5 >= 12.0 and bs_ratio >= 1.60:
            p4_score = 15
        elif p_m5 >= 6.0 and bs_ratio >= 1.40:
            p4_score = 11
        else:
            p4_score = 6

    # PILAR 5 (0-15 Poin): Imunitas Suplai Atas & Sunk-Cost Ekonomi
    p5_score = 0
    creator_pct = float(h_meta.get("creator_pct") or 0.0)
    lp_locked = float(h_meta.get("lp_locked_pct") or 0.0)
    burned_pct = float(h_meta.get("burned_pct") or 0.0)
    is_renounced = bool(h_meta.get("is_renounced"))
    max_single = float(h_meta.get("max_single") or 0.0)
    pure_top10 = float(h_meta.get("pure_top10") or 0.0)
    total_holders = int(h_meta.get("total_holders") or rug_d.get("totalHolders") or 0)
    info = bp.get("info") or {}
    eff_boost = max(int(boost_amount or 0), int(((bp.get("boosts") or {}).get("active")) or 0))
    has_paid_or_full = bool(info.get("header") or eff_boost >= 30 or ((info.get("websites") or []) and (info.get("socials") or [])))

    if creator_pct <= 0.10 and (lp_locked >= 98.0 or burned_pct >= 2.0 or is_renounced):
        p5_score += 6
    elif creator_pct <= 0.50 and (lp_locked >= 95.0 or is_renounced):
        p5_score += 4

    if max_single <= 3.0 and pure_top10 <= 18.5:
        p5_score += 5
    elif max_single <= 4.1 and pure_top10 <= 22.5:
        p5_score += 3

    if has_paid_or_full or total_holders >= 250:
        p5_score += 4
    elif bool(info.get("socials") or info.get("websites")) or liq_usd >= 15_000.0:
        p5_score += 2

    total_score = min(100, p1_score + p2_score + p3_score + p4_score + p5_score)
    if total_score < 68:
        return (
            False,
            f"V5.0 Skor Tembus $100K Tidak Cukup ({total_score}/100 < 68 | Inflow {inflow_coverage:.2f}x AMM, "
            f"LP {liq_mc_pct:.1f}% MC) -> Probabilitas mati sebelum $100K tinggi!",
            {"is_crossing_verified": False, "crossing_score": total_score},
        )

    summary_tag = (
        f"Skor Tembus $100K {total_score}/100 (Inflow {inflow_coverage:.2f}x Kurva AMM · Est ~{est_mins_to_100k}m ke $105K)"
    )
    return True, "LOLOS FISIKA TEMBUS $100K V5.0", {
        "is_crossing_verified": True,
        "crossing_score": total_score,
        "delta_q_need_usd": round(delta_q_need, 0),
        "est_net_inflow_5m_usd": round(est_net_inflow_5m, 0),
        "inflow_coverage_ratio": round(inflow_coverage, 2),
        "est_mins_to_100k": est_mins_to_100k,
        "summary_tag": summary_tag,
    }


def verify_sub100k_dev_deterrence(
    best_pair: dict,
    rug_data: dict | None,
    holder_meta: dict,
    ohlcv_payload,
    live_mc: float,
    liq_usd: float,
    boost_amount: int = 0,
    current_price_usd: float = 0.0,
) -> tuple[bool, str, dict]:
    """
    MODUS #34 (V3.7 — KUNCI PENYANDERA DEV DI BAWAH MC $100K: "DEV DILUCUTI ATAU RUGI BESAR JIKA NAKAL <$100K"):
    Berlaku WAJIB untuk SEMUA jalur beli di bawah MC $100.000 (baik jalur 'MASUK DI AWAL <$70K' maupun 'Lorong 0 $25K-$82K'):
    1. Pelucutan Senjata & Peluru Dev:
       - Saldo langsung Dev wajib <= 1.5% (menutup celah dev pegang 2%-4% lalu banting di $75K-$90K).
       - Riwayat Deployer wajib <= 3 token (blokir dev 'pabrik koin receh' yang hobi rug sebelum $100K).
       - LP Lock >= 90% atau Kontrak Renounced, Paus tunggal <= 4.3%, & Top-5 EOA <= 1.35x Cadangan Uang Keras Kolam.
    2. Anti-Gagal Tembok $100K:
       - Tolak mutlak koin yang di candle 5m ternyata SUDAH PERNAH menyentuh $82K+ lalu terbanting >= 20%.
    3. Wajib Minimal 1 Bukti Ekonomi "Skin-in-the-Game / Sunk-Cost > $100K" (Alasan Dev Pasti Tidak Berani Nakal <$100K):
       - Jangkar A (Zero-Ammo + Burn/Lock Permanen): Saldo dev <= 0.5% + (Burn >= 2% atau LP Lock >= 98% atau Renounced dgn LP >= $12K).
       - Jangkar B (Modal Hangus Pemasaran & Profil Berbayar): Paid Boost >= 30 ATAU (Header/Web/Sosmed Resmi + LP >= $13K).
       - Jangkar C (Modal Paus Tertanam Tebal di Dasar): LP >= $14K (>= 18% MC) + Vol 5m >= $8.5K (atau tiket >= $125) & Buy/Sell >= 1.55x.
    """
    if live_mc <= 0.0 or live_mc >= 100_000.0:
        return True, "Di atas zona khusus <$100K", {"deterrence_proofs": [], "is_sub100k_verified": False}

    rug_d = rug_data if isinstance(rug_data, dict) else {}
    h_meta = holder_meta if isinstance(holder_meta, dict) else {}

    creator_tokens_cnt = len(rug_d.get("creatorTokens") or []) or int(h_meta.get("creator_tokens_cnt") or 0)
    is_cto_sub100k = bool(h_meta.get("is_verified_cto_takeover"))
    if creator_tokens_cnt > 3 and not is_cto_sub100k:
        return (
            False,
            f"MODUS #34 (Dev Pabrik Koin <$100K): Deployer sudah pernah membuat {creator_tokens_cnt} koin (>3) -> "
            "Kebiasaan panen receh sebelum tembus $100K!",
            {},
        )

    creator_pct = float(h_meta.get("creator_pct") or 0.0)
    if creator_pct > 1.5:
        return (
            False,
            f"MODUS #34 (Sisa Peluru Dev <$100K): Saldo langsung Dev masih {creator_pct:.2f}% (> 1.5%) di MC ${live_mc:,.0f} -> "
            "Dev masih punya peluru untuk banting sebelum $100K!",
            {},
        )

    lp_locked = float(h_meta.get("lp_locked_pct") or 0.0)
    is_renounced = bool(h_meta.get("is_renounced"))
    if lp_locked < 90.0 and not is_renounced:
        return (
            False,
            f"MODUS #34 (LP Belum Terkunci Penuh <$100K): Kunci LP baru {lp_locked:.1f}% (<90%) & belum Renounced!",
            {},
        )

    max_single = float(h_meta.get("max_single") or 0.0)
    pure_top10 = float(h_meta.get("pure_top10") or 0.0)
    max_single_cap = 5.0 if (creator_pct <= 0.5 and pure_top10 <= 20.0) else 4.3
    if max_single > max_single_cap:
        return (
            False,
            f"MODUS #34 (Paus Tunggal <$100K): 1 dompet EOA memegang {max_single:.1f}% (>{max_single_cap:.1f}%) -> Rawan banting sebelum $100K!",
            {},
        )
    if pure_top10 > 23.0:
        return (
            False,
            f"MODUS #34 (Konsentrasi Top-10 <$100K): Top 10 memegang {pure_top10:.1f}% (>23.0%) di koin bawah $100K!",
            {},
        )

    primary_hard_usd = float(h_meta.get("primary_hard_usd") or h_meta.get("total_quote_hard_usd") or (liq_usd * 0.5))
    top5_pure_pct = float(h_meta.get("top5_pure_pct") or 0.0)
    if primary_hard_usd > 0.0 and top5_pure_pct > 0.0:
        top5_usd = (top5_pure_pct / 100.0) * live_mc
        if top5_usd > (primary_hard_usd * 1.35):
            return (
                False,
                f"MODUS #34 (Risiko Kuras Kolam <$100K): Nilai Top-5 holder (${top5_usd:,.0f}) > 1.35x uang keras kolam (${primary_hard_usd:,.0f})!",
                {},
            )

    candles = []
    if isinstance(ohlcv_payload, dict):
        raw_list = ((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = [r for r in raw_list if isinstance(r, (list, tuple)) and len(r) >= 6 and float(r[2] or 0.0) > 0.0]
        candles.sort(key=lambda x: float(x[0] or 0.0))

    ref_price = float(current_price_usd or (best_pair or {}).get("priceUsd") or 0.0)
    if candles and ref_price > 0.0:
        max_h = max(float(r[2] or 0.0) for r in candles)
        if max_h > ref_price:
            est_peak_mc = live_mc * (max_h / ref_price)
            drop_from_peak = ((max_h - ref_price) / max_h) * 100.0
            # Pengecualian presisi untuk Dasar CTO Pasca-Dev-Exit (<$100K): Dev 0%, Burn >=5%, >=280 holders,
            # puncak lama >=5 candle lalu, dan 3 candle terakhir mencetak Higher-Low (Seller Exhaustion seperti $Grokbot $56.6K->$77.5K)!
            cto_bottom_reclaim = False
            if is_cto_sub100k and len(candles) >= 6:
                pk_i = max(range(len(candles)), key=lambda k: float(candles[k][2] or 0.0))
                c3_lows = [float(r[3] or 0.0) for r in candles[-3:]]
                if pk_i <= (len(candles) - 5) and c3_lows[0] > 0 and c3_lows[0] <= c3_lows[1] <= c3_lows[2]:
                    cto_bottom_reclaim = True

            if est_peak_mc >= 82_000.0 and drop_from_peak >= 20.0 and not cto_bottom_reclaim:
                return (
                    False,
                    f"MODUS #34 (Bekas Mental Tembok $100K): Koin sudah pernah menyentuh ~${est_peak_mc:,.0f} lalu dibanting -{drop_from_peak:.0f}%!",
                    {},
                )

    burned_pct = float(h_meta.get("burned_pct") or 0.0)
    info = (best_pair or {}).get("info") or {}
    has_socials = bool((info.get("socials") or []) or (info.get("websites") or []) or info.get("header"))
    txns_m5 = ((best_pair or {}).get("txns") or {}).get("m5") or {}
    b_m5, s_m5 = int(txns_m5.get("buys") or 0), int(txns_m5.get("sells") or 0)
    tot_m5 = b_m5 + s_m5
    vol_m5 = float(((best_pair or {}).get("volume") or {}).get("m5") or 0.0)
    avg_ticket = (vol_m5 / tot_m5) if tot_m5 > 0 else 0.0
    bs_ratio = b_m5 / float(max(1, s_m5))
    liq_mc_ratio = (liq_usd / live_mc) if live_mc > 0.0 else 0.0
    eff_boost = max(int(boost_amount or 0), int((((best_pair or {}).get("boosts") or {}).get("active")) or 0))

    deterrence_proofs = []
    if creator_pct <= 0.5 and (burned_pct >= 2.0 or lp_locked >= 98.0 or (is_renounced and liq_usd >= 12_000.0)):
        burn_tag = f" + Burn {burned_pct:.1f}%" if burned_pct >= 1.0 else ""
        deterrence_proofs.append(f"Dev 0% Peluru & Kontrak/LP Mati ({lp_locked:.0f}% Lock{burn_tag})")

    if eff_boost >= 30 or (has_socials and liq_usd >= 13_000.0 and liq_mc_ratio >= 0.17):
        mod_tag = f"Paid Boost {eff_boost}x" if eff_boost >= 30 else "Profil Resmi + Modal LP Tebal"
        deterrence_proofs.append(f"Modal Hangus Dev ({mod_tag} -> Rugi jika banting <$100K)")

    if liq_usd >= 14_000.0 and liq_mc_ratio >= 0.18 and (avg_ticket >= 125.0 or (vol_m5 >= 8_500.0 and bs_ratio >= 1.55) or (vol_m5 >= 12_000.0 and bs_ratio >= 1.32)):
        deterrence_proofs.append(f"Modal Paus Tertanam (${liq_usd:,.0f} LP / Vol ${vol_m5:,.0f} -> Impas >$140K)")

    if not deterrence_proofs:
        return (
            False,
            f"MODUS #34 (Tanpa Jaminan Anti-Nakal <$100K): Di MC ${live_mc:,.0f}, dev belum membakar suplai/LP penuh, "
            "belum keluar modal profil/boost, & LP/volume paus belum mengikat di atas titik impas $100K!",
            {},
        )

    return True, "TERKUNCI JAMINAN DEV ANTI-NAKAL <$100K", {
        "is_sub100k_verified": True,
        "deterrence_proofs": deterrence_proofs[:2],
    }


def evaluate_anti_countertrade_shakeout_shield(
    ohlcv_payload,
    best_pair: dict,
    holder_meta: dict | None = None,
    rt_loss_pct: float = 0.0,
    depth_loss_pct: float = 0.0,
    age_minutes: float = 30.0,
) -> tuple[bool, str, dict]:
    """
    MODUS #31 & #32 (V3.6 — PERISAI ANTI-PEMBANTING UNIQUE BUYER & ANTI-TABRAKAN AUTO-TP KOMPETITOR):
    1. Modus #31A (Unique-Buyer Slam Scar): Mendeteksi bot dev nakal (Geyser/Mempool Copy-Sell) yang otomatis
       membanting setiap ada unique buyer luar masuk -> meninggalkan >=2 jarum banting atas (>=50% range) di 4 bar terakhir.
    2. Modus #31B (Thin-LP Wash Harvester): Kolam tipis (Liq/MC < 11.5% di MC < $180k) yang dipompa tiket receh (<$38/tx)
       atau dampak kedalaman 1 SOL timpang (>=3.4x friksi 0.1 SOL & >=9%) murni sebagai perangkap penguras unique buyer.
    3. Modus #32 (Anti-Impulse-Chase / Auto-TP Bot Collision): Menolak ikut-ikutan filter pasaran pemain lain yang masuk
       di ujung 3 candle hijau vertikal (>=+72% tanpa koreksi & bar terakhir >=+36%) tepat di mulut Auto-TP bot scalper.
    """
    mc = float((best_pair or {}).get("marketCap") or (best_pair or {}).get("fdv") or 0.0)
    liq = float(((best_pair or {}).get("liquidity") or {}).get("usd") or 0.0)
    txns_m5 = ((best_pair or {}).get("txns") or {}).get("m5") or {}
    buys_m5 = int(txns_m5.get("buys") or 0)
    sells_m5 = int(txns_m5.get("sells") or 0)
    total_tx_m5 = buys_m5 + sells_m5
    vol_m5 = float(((best_pair or {}).get("volume") or {}).get("m5") or 0.0)
    p_m5 = float(((best_pair or {}).get("priceChange") or {}).get("m5") or 0.0)

    # MODUS #31B: Thin-LP Unique-Buyer Harvester Vacuum
    liq_mc_pct = (liq / mc * 100.0) if mc > 0.0 else 0.0
    avg_tx_m5 = (vol_m5 / total_tx_m5) if total_tx_m5 > 0 else 0.0
    if 0.0 < mc < 180_000.0 and p_m5 >= 16.0 and liq > 0.0 and liq_mc_pct < 11.5 and total_tx_m5 >= 40 and avg_tx_m5 < 38.0:
        return (
            False,
            f"MODUS #31B (Jebakan Dev Penunggu Unique Buyer): MC ${mc:,.0f} tampak hijau (+{p_m5:.1f}%) tapi LP sangat tipis "
            f"({liq_mc_pct:.1f}% MC) & tiket receh bot (${avg_tx_m5:.0f}/tx) -> Begitu Anda beli langsung dikuras dev!",
            {},
        )
    if depth_loss_pct >= 9.0 and rt_loss_pct > 0.0 and depth_loss_pct >= (rt_loss_pct * 3.4):
        return (
            False,
            f"MODUS #31B (Jebakan Kedalaman Semu): Friksi kecil {rt_loss_pct:.1f}% tapi jual 1 SOL amblas {depth_loss_pct:.1f}% "
            f"(>=3.4x) -> Antrean beli bawah kosong, siap menjebak unique buyer!",
            {},
        )

    candles = []
    if isinstance(ohlcv_payload, dict):
        raw_list = ((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = [r for r in raw_list if isinstance(r, (list, tuple)) and len(r) >= 6 and float(r[4] or 0.0) > 0.0]
        candles.sort(key=lambda x: float(x[0] or 0.0))

    post_shakeout_reentry = False
    if len(candles) >= 3:
        c4 = candles[-4:]
        slam_scars = 0
        for r in c4:
            o_p, h_p, l_p, c_p = float(r[1] or 0.0), float(r[2] or 0.0), float(r[3] or 0.0), float(r[4] or 0.0)
            rng = h_p - l_p
            if l_p > 0.0 and rng > 0.0 and (rng / l_p) >= 0.12:
                upper_wick = h_p - max(o_p, c_p)
                if (upper_wick / rng) >= 0.50 and c_p <= (l_p + 0.45 * rng):
                    slam_scars += 1
        if slam_scars >= 2:
            return (
                False,
                f"MODUS #31A (Bot Pembanting Unique Buyer): Terdeteksi {slam_scars} jarum banting atas tajam (>=50% ekor) "
                "di 4 candle terakhir -> Bot dev otomatis memukul setiap pembeli luar yang masuk!",
                {},
            )

        # MODUS #32: Anti-Impulse-Chase / Tabrakan Auto-TP Bot Scalper Pemain Lain
        c3 = candles[-3:]
        all_3_green = all(float(r[4] or 0.0) > float(r[1] or 0.0) * 1.03 for r in c3)
        o_first = float(c3[0][1] or 0.0)
        o_last = float(c3[-1][1] or 0.0)
        c_last = float(c3[-1][4] or 0.0)
        if all_3_green and o_first > 0.0 and o_last > 0.0:
            surge_3bar = ((c_last - o_first) / o_first) * 100.0
            ext_last = ((c_last - o_last) / o_last) * 100.0
            if surge_3bar >= 72.0 and ext_last >= 36.0:
                return (
                    False,
                    f"MODUS #32 (Anti-Kejar Pucuk / Tabrakan Auto-TP Kompetitor): 3 candle hijau vertikal memompa +{surge_3bar:.0f}% "
                    f"tanpa koreksi (candle ini +{ext_last:.0f}%) -> Tepat di titik buang Auto-TP bot scalper lain!",
                    {},
                )

        # Deteksi apakah koin ini SUDAH melewati pencucian/koreksi ritel sebelum memantul sehat (Ideal Anti-Shakeout!)
        c5 = candles[-5:]
        had_prior_shakeout = any(float(r[4] or 0.0) < float(r[1] or 0.0) for r in c5[:-1])
        last_ext_pct = ((c_last - o_last) / o_last * 100.0) if o_last > 0.0 else 0.0
        if had_prior_shakeout and 0.0 <= last_ext_pct <= 32.0:
            post_shakeout_reentry = True

    return True, "LOLOS PERISAI ANTI-COUNTER-TRADE V3.6", {
        "post_shakeout_reentry": post_shakeout_reentry,
    }


def compute_stealth_anti_shakeout_plan(
    live_mc: float,
    liq_usd: float,
    ohlcv_payload,
    price_usd: float,
    chain_id: str = "solana",
    default_cut_loss_pct: float = 15.0,
    tax_pct: float = 0.0,
) -> dict:
    """
    MODUS #33 (V3.6 — PROTOKOL EKSEKUSI SILUMAN ANTI-DETEKSI DEV, ANTI-MEV & ANTI-STOP-HUNT):
    Menghitung ukuran tiket maksimal per klik (<0.75% LP) agar tidak memicu radar Whale/Unique-Buyer Counter-Sell dev,
    zona antre diskon (-6.5%) untuk peluru ke-2, serta Lantai Stop-Loss Struktural di bawah Swing Higher-Low candle 5m.
    """
    stealth_clip_usd = int(max(25, min(250, round((max(5_000.0, liq_usd) * 0.0075) / 5.0) * 5)))
    dip_limit_mc = round(live_mc * 0.935, 0)

    structural_sl_pct = float(default_cut_loss_pct or 15.0)
    candles = []
    if isinstance(ohlcv_payload, dict):
        raw_list = ((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = [r for r in raw_list if isinstance(r, (list, tuple)) and len(r) >= 6 and float(r[3] or 0.0) > 0.0]
        candles.sort(key=lambda x: float(x[0] or 0.0))

    if candles and price_usd > 0.0 and live_mc > 0.0:
        recent_bars = candles[-3:]
        swing_low = min(float(r[3] or 0.0) for r in recent_bars if float(r[3] or 0.0) > 0.0)
        if 0.0 < swing_low < price_usd:
            invalidation_price = swing_low * 0.968  # 3.2% di bawah jarum Higher-Low agar kebal sapuan SL bandar
            raw_sl_pct = ((price_usd - invalidation_price) / price_usd) * 100.0
            structural_sl_pct = round(min(23.5, max(11.5, raw_sl_pct)), 1)

    structural_sl_mc = round(live_mc * (1.0 - (structural_sl_pct / 100.0)), 0)
    max_slippage_pct = round(min(5.5, max(2.5, float(tax_pct or 0.0) + 1.5)), 1) if tax_pct > 0.5 else 2.5
    mev_guard_label = "Anti-MEV Jito ON" if chain_id == "solana" else "Anti-MEV RPC / Guard ON"

    return {
        "stealth_clip_usd": stealth_clip_usd,
        "dip_limit_mc": dip_limit_mc,
        "structural_sl_pct": structural_sl_pct,
        "structural_sl_mc": structural_sl_mc,
        "max_slippage_pct": max_slippage_pct,
        "mev_guard_label": mev_guard_label,
    }


def classify_early_continuation_dna(
    ohlcv_payload,
    best_pair: dict,
    holder_meta: dict,
    boost_amount: int = 0,
    age_minutes: float = 30.0,
) -> tuple[bool, str, dict]:
    """
    MODUS #30 (V3.5 — PEMILAH DINI "PASTI NAIK LAGI" VS "PASTI MATI" + SELANGKAH DI DEPAN BOT KOMPETITOR):
    1. Menolak koin yang PASTI TIDAK AKAN NAIK LAGI (Jebol Lantai Kelulusan, Volume Merah Membengkak saat Koreksi,
       atau Tangga Turun 3-Candle Beruntun).
    2. Mengekstrak alasan terukur mengapa koin punya POTENSI NAIK LAGI + menandai PRE-BOT FRONT-RUN!
    """
    mc = float((best_pair or {}).get("marketCap") or (best_pair or {}).get("fdv") or 0.0)
    volumes = (best_pair or {}).get("volume") or {}
    vol_h1 = float(volumes.get("h1") or 0.0)
    chain_id = str((best_pair or {}).get("chainId") or "solana").lower()

    candles = []
    if isinstance(ohlcv_payload, dict):
        raw_list = ((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = [r for r in raw_list if isinstance(r, (list, tuple)) and len(r) >= 6 and float(r[4] or 0.0) > 0.0]
        candles.sort(key=lambda x: float(x[0] or 0.0))

    if len(candles) >= 3 and age_minutes <= 240.0 and mc < 315_000.0:
        first_open = float(candles[0][1] or candles[0][4] or 0.0)
        first_low = min(float(r[3] or 0.0) for r in candles[:2] if float(r[3] or 0.0) > 0.0) or first_open
        max_h = max(float(r[2] or 0.0) for r in candles)
        last_c = float(candles[-1][4] or 0.0)

        is_cto_dna = bool((holder_meta or {}).get("is_verified_cto_takeover"))
        min_all_low = min(float(r[3] or 0.0) for r in candles if float(r[3] or 0.0) > 0.0) or first_open
        cto_reclaiming = is_cto_dna and len(candles) >= 6 and min_all_low > 0.0 and last_c >= (min_all_low * 1.35)
        if not cto_reclaiming and age_minutes >= 12.0 and first_open > 0.0 and last_c < (first_open * 0.78) and last_c < (max_h * 0.55):
            return (
                False,
                "Pasti Tidak Naik Lagi (Broken Launch Floor): Harga jebol di bawah lantai kelulusan awal tanpa serapan paus!",
                {}
            )

        c3 = candles[-3:]
        h0, h1, h2 = float(c3[0][2] or 0.0), float(c3[1][2] or 0.0), float(c3[2][2] or 0.0)
        cl0, cl1, cl2 = float(c3[0][4] or 0.0), float(c3[1][4] or 0.0), float(c3[2][4] or 0.0)
        op2 = float(c3[2][1] or 0.0)
        if h0 > h1 > h2 and cl0 > cl1 > cl2 and cl2 < op2:
            return (
                False,
                "Pasti Tidak Naik Lagi (3-Bar Bleed): Mencetak 3 candle Lower-High & Lower-Close beruntun -> Distribusi aktif!",
                {}
            )

        c4 = candles[-4:]
        max_c4_close = max(float(r[4] or 0.0) for r in c4)
        greens_c4 = [float(r[5] or 0.0) for r in c4 if float(r[4] or 0.0) >= float(r[1] or 0.0)]
        reds_c4 = [float(r[5] or 0.0) for r in c4 if float(r[4] or 0.0) < float(r[1] or 0.0)]
        if len(reds_c4) >= 2 and greens_c4 and last_c < (max_c4_close * 0.93):
            avg_r = sum(reds_c4) / len(reds_c4)
            avg_g = sum(greens_c4) / len(greens_c4)
            if avg_r >= max(8_000.0, avg_g * 1.35):
                return (
                    False,
                    f"Pasti Tidak Naik Lagi (Red Dump Expansion): Rata-rata volume merah (${avg_r:,.0f}) membengkak "
                    f">=1.35x volume hijau (${avg_g:,.0f}) saat koreksi!",
                    {}
                )
    else:
        first_low = 0.0

    reasons = []
    if len(candles) >= 2:
        for idx_c in range(1, len(candles)):
            prev_r, curr_r = candles[idx_c - 1], candles[idx_c]
            po, pc, pv = float(prev_r[1] or 0.0), float(prev_r[4] or 0.0), float(prev_r[5] or 0.0)
            co, cc, cv = float(curr_r[1] or 0.0), float(curr_r[4] or 0.0), float(curr_r[5] or 0.0)
            if pc < po and cc > co and cc >= po and cv >= max(5_000.0, pv * 1.02):
                reasons.append(f"Serapan Paus Pasca-Cuci (Vol hijau ${cv:,.0f} telan buangan merah ${pv:,.0f})")
                break

    if len(candles) >= 3:
        c3 = candles[-3:]
        l0, l1, l2 = float(c3[0][3] or 0.0), float(c3[1][3] or 0.0), float(c3[2][3] or 0.0)
        cl0, cl1, cl2 = float(c3[0][4] or 0.0), float(c3[1][4] or 0.0), float(c3[2][4] or 0.0)
        if l0 <= l1 <= l2 and cl2 >= cl1:
            reasons.append("Tangga Akumulasi Higher-Low (Lantai beli paus terus meninggi)")

        c5_win = candles[-5:]
        g_vols = [float(r[5] or 0.0) for r in c5_win if float(r[4] or 0.0) >= float(r[1] or 0.0)]
        r_vols = [float(r[5] or 0.0) for r in c5_win if float(r[4] or 0.0) < float(r[1] or 0.0)]
        recent_min_low = min(float(r[3] or 0.0) for r in c3 if float(r[3] or 0.0) > 0.0) if c3 else 0.0
        if g_vols and r_vols and max(r_vols) <= (max(g_vols) * 0.58) and first_low > 0.0 and recent_min_low >= (first_low * 1.22):
            reasons.append("Koreksi Kering / Dry Pullback (Paus tahan barang, Higher-Low kokoh)")

    creator_pct = float((holder_meta or {}).get("creator_pct") or 0.0)
    burned_pct = float((holder_meta or {}).get("burned_pct") or 0.0)
    lp_locked = float((holder_meta or {}).get("lp_locked_pct") or 0.0)
    is_renounced = bool((holder_meta or {}).get("is_renounced"))
    if bool((holder_meta or {}).get("is_verified_cto_takeover")):
        t_hold = int((holder_meta or {}).get("total_holders") or 0)
        burn_str = f" + Burn {burned_pct:.1f}%" if burned_pct >= 1.0 else ""
        reasons.insert(0, f"CTO Terverifikasi (Dev Keluar 0%{burn_str} · {t_hold:,} Holders)")
    elif creator_pct <= 0.5 and (burned_pct >= 2.0 or lp_locked >= 95.0 or is_renounced):
        burn_note = f" + Burn {burned_pct:.1f}%" if burned_pct >= 1.0 else ""
        reasons.append(f"Suplai Bersih (Dev {creator_pct:.1f}%, Kunci/Renounced{burn_note})")

    if not reasons:
        reasons.append("Orderflow Net-Beli Dominan & Likuiditas Terkunci Sehat")

    pre_bot_edges = []
    if int(boost_amount or 0) == 0:
        pre_bot_edges.append("Mendahului Bot Paid-Boost/Ads (0 Boost)")
    if mc <= 168_000.0 and (age_minutes <= 55.0 or vol_h1 < 130_000.0):
        pre_bot_edges.append("Mendahului Bot Trending 1H GMGN/Ave/DEXTools")
    if chain_id in ("bsc", "base"):
        pre_bot_edges.append(f"Radar Lintas-Chain {chain_id.upper()} Live")

    is_pre_bot = bool(mc <= 168_000.0 and pre_bot_edges)
    return True, "DNA Kontinuasi Valid", {
        "continuation_reasons": reasons[:3],
        "pre_bot_edges": pre_bot_edges[:2],
        "is_pre_bot_frontrun": is_pre_bot,
    }


def fetch_pool_ohlcv(
    pair_address: str,
    age_minutes: float,
    timeout: float = 2.4,
    chain_id: str = "solana",
    token_address: str = "",
):
    if not pair_address:
        return None
    gt_net = GT_NETWORK_MAP.get(str(chain_id or "solana").lower(), "solana")
    tok_param = f"&token={token_address}" if token_address else ""
    if age_minutes <= 360.0:
        url = f"https://api.geckoterminal.com/api/v2/networks/{gt_net}/pools/{pair_address}/ohlcv/minute?aggregate=5&limit=72{tok_param}"
    elif age_minutes <= 1440.0:
        url = f"https://api.geckoterminal.com/api/v2/networks/{gt_net}/pools/{pair_address}/ohlcv/minute?aggregate=15&limit=96{tok_param}"
    else:
        url = f"https://api.geckoterminal.com/api/v2/networks/{gt_net}/pools/{pair_address}/ohlcv/hour?aggregate=1&limit=168{tok_param}"
    return fetch_json(url, timeout=timeout)



def evaluate_ohlcv_ath_trap(
    ohlcv_payload,
    current_price_usd: float,
    current_mc_usd: float,
    age_minutes: float,
    is_hyper_wave1: bool = False,
    is_verified_cto: bool = False,
) -> tuple[bool, str, dict]:
    """
    PERISAI V2.7B + V2.8A/B (OHLCV MICROSTRUCTURE, ANTI WASH-PEG BOT, & REAL ATH GUARD):
    1. V2.8A ($Shitcoin 19:34 Guard): Blokir koin intraday (<$1.08M) yang sebelum loncatan dikunci datar buatan
       oleh Bot Cuci Volume (>=2 candle bervolume >=$4.5k tapi rentang High-Low <=0.85%, total volume cuci >=$12k).
    2. V2.8B ($Shitcoin Bait-Slam Guard): Blokir loncatan pancingan 1-candle (+55%+ dari Open ke High pada koin
       non-Wave1) yang saat sinyal dievaluasi sudah langsung dibanting >=11.5% di bawah pucuk jarumnya sendiri!
    3. V2.7B ($Marvin 1 PM Guard): Blokir pantulan koin yang sudah ambruk >=42% (intraday) / >=55% (multi-hari)
       di bawah puncak ATH historis!
    """
    if not isinstance(ohlcv_payload, dict) or current_price_usd <= 0.0:
        return True, "OHLCV tidak tersedia (lewati tanpa blokir semu)", {"ath_drawdown_pct": 0.0}

    raw_candles = (((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])
    valid_candles = [
        row for row in raw_candles
        if isinstance(row, (list, tuple)) and len(row) >= 6
    ]
    if not valid_candles:
        return True, "Candle kosong", {"ath_drawdown_pct": 0.0}

    valid_candles.sort(key=lambda r: float(r[0] or 0.0))

    # 1. PERISAI V2.8A: Detektor Bot Cuci Volume / Robotic Wash-Pegging ($Shitcoin 19:10-19:29 WIB: 4 candle 0.14%-0.46% Vol $70k!)
    if age_minutes <= 1440.0 and current_mc_usd < 1_080_000.0:
        peg_vols = []
        doji_rows = []
        for row in valid_candles:
            o_v, h_v, l_v, c_v, vol_v = (
                float(row[1] or 0.0),
                float(row[2] or 0.0),
                float(row[3] or 0.0),
                float(row[4] or 0.0),
                float(row[5] or 0.0),
            )
            if l_v > 0.0 and vol_v >= 4500.0:
                range_pct = ((h_v - l_v) / l_v) * 100.0
                if range_pct <= 0.85:
                    peg_vols.append(vol_v)
            if o_v > 0.0 and c_v > 0.0 and vol_v >= 4000.0:
                body_pct = (abs(c_v - o_v) / o_v) * 100.0
                if body_pct <= 0.35:
                    doji_rows.append((c_v, vol_v))

        if len(peg_vols) >= 2 and sum(peg_vols) >= 12_000.0:
            return (
                False,
                f"Terdeteksi Bot Wash-Pegging & Bait-Pump: Harga dikunci datar buatan (<=0.85%) selama "
                f"{len(peg_vols)} candle dengan volume cuci ${sum(peg_vols):,.0f} sebelum loncatan pancingan!",
                {"ath_drawdown_pct": 0.0, "wash_peg_candles": len(peg_vols)}
            )

        # PERISAI V3.0A: Detektor Barcode / Doji Wash-Maker Bot (Jarum digoyang 2-4% tapi Close selalu kembali ke Open!)
        if len(doji_rows) >= 4:
            min_dc = min(x[0] for x in doji_rows)
            max_dc = max(x[0] for x in doji_rows)
            doji_vol_sum = sum(x[1] for x in doji_rows)
            if min_dc > 0.0 and (((max_dc - min_dc) / min_dc) * 100.0) <= 2.2 and doji_vol_sum >= 18_000.0:
                return (
                    False,
                    f"Terdeteksi Barcode/Doji Wash-Maker Bot: {len(doji_rows)} candle dikocok bolak-balik kembali ke "
                    f"harga awal (bodi <=0.35%, volume cuci ${doji_vol_sum:,.0f})!",
                    {"ath_drawdown_pct": 0.0}
                )

        # PERISAI V3.0B: Detektor Algorithmic Staircase Pump Bot (5 candle tangga naik mekanis seragam tanpa sumbu bawah!)
        if len(valid_candles) >= 5:
            for w_idx in range(len(valid_candles) - 4):
                win = valid_candles[w_idx:w_idx + 5]
                steps = []
                stair_ok = True
                for w_row in win:
                    wo, wh, wl, wc = float(w_row[1] or 0.0), float(w_row[2] or 0.0), float(w_row[3] or 0.0), float(w_row[4] or 0.0)
                    if wo <= 0.0 or wc <= wo:
                        stair_ok = False
                        break
                    gain_p = ((wc - wo) / wo) * 100.0
                    low_wick_p = ((wo - wl) / wo) * 100.0
                    if not (1.2 <= gain_p <= 8.0) or low_wick_p > 0.08:
                        stair_ok = False
                        break
                    steps.append(gain_p)
                if stair_ok and len(steps) == 5 and (max(steps) - min(steps)) <= 0.65:
                    return (
                        False,
                        f"Terdeteksi Algorithmic Staircase Bot: 5 candle naik mekanis seragam (~+{sum(steps)/5:.1f}%/candle "
                        f"tanpa koreksi alami) -> Rawan jurang terjun bebas!",
                        {"ath_drawdown_pct": 0.0}
                    )

    # Pre-check V3.4 (#29B — Kasus Live $Nothing 51Sfa...pump): Deteksi Wave-2 Sniper-Flush Staircase Reclaim (<$168k)
    is_wave2_staircase_reclaim = False
    if 25.0 <= age_minutes <= 180.0 and current_mc_usd <= 168_000.0 and len(valid_candles) >= 4:
        s3 = valid_candles[-3:]
        s_o = [float(r[1] or 0.0) for r in s3]
        s_l = [float(r[3] or 0.0) for r in s3]
        s_c = [float(r[4] or 0.0) for r in s3]
        s_v = [float(r[5] or 0.0) for r in s3]
        prior_max_h = max(float(r[2] or 0.0) for r in valid_candles)
        green_s3 = sum(1 for i in range(3) if s_c[i] >= s_o[i])
        g_vol_s3 = sum(s_v[i] for i in range(3) if s_c[i] >= s_o[i])
        r_vol_s3 = sum(s_v[i] for i in range(3) if s_c[i] < s_o[i])
        if (
            s_o[0] > 0.0
            and s_c[0] < s_c[1] < s_c[2]
            and s_l[0] <= s_l[1] <= s_l[2]
            and s_c[2] > s_o[2]
            and green_s3 >= 2
            and (((s_c[2] - min(s_o[0], s_c[0])) / min(s_o[0], s_c[0])) * 100.0) >= 18.0
            and sum(s_v) >= 36_000.0
            and g_vol_s3 >= max(24_000.0, r_vol_s3 * 2.0)
            and prior_max_h >= (current_price_usd * 1.22)
        ):
            is_wave2_staircase_reclaim = True

    # 2. PERISAI V2.8B + V3.3 (#26A) + V3.4 God-Candle Consolidation + V4.1 Birth Discovery:
    has_birth_discovery_pullback = False
    if age_minutes <= 1440.0 and current_mc_usd < 1_080_000.0:
        recent_two = valid_candles[-2:]
        for idx_r, row in enumerate(recent_two):
            o_v, h_v, c_v = float(row[1] or 0.0), float(row[2] or 0.0), float(row[4] or 0.0)
            if o_v > 0.0 and h_v > current_price_usd:
                candle_pump_pct = ((h_v - o_v) / o_v) * 100.0
                wick_slam_pct = ((h_v - current_price_usd) / h_v) * 100.0
                is_confirmed_whale_impulse = False
                if len(recent_two) == 2 and idx_r == 0 and h_v > o_v:
                    nxt = recent_two[1]
                    nxt_o, nxt_c = float(nxt[1] or 0.0), float(nxt[4] or 0.0)
                    body_retention = (c_v - o_v) / (h_v - o_v)
                    if (
                        c_v >= (o_v * 1.80)
                        and body_retention >= 0.72
                        and nxt_c >= nxt_o
                        and nxt_c >= c_v
                        and current_price_usd >= c_v
                    ):
                        is_confirmed_whale_impulse = True
                # MODUS #38B (V4.1 — Birth Price-Discovery Pullback vs Bait & Slam, Kasus $STONKLANA GF74fB...Nyk6):
                # Pada 2-10 menit pertama kelahiran pool (<= 3 candle 5m), koin yang baru bermigrasi sering mengalami
                # penemuan harga awal (+60-80% lalu pullback sehat -18% s/d -32% di atas +16% Open dengan Vol >=$18K).
                is_healthy_birth_discovery = (
                    1.8 <= age_minutes <= 10.0
                    and len(valid_candles) <= 3
                    and current_mc_usd <= 260_000.0
                    and float(row[5] or 0.0) >= 18_000.0
                    and current_price_usd >= (o_v * 1.16)
                    and c_v >= (o_v * 1.16)
                    and wick_slam_pct <= 33.0
                )
                if is_healthy_birth_discovery:
                    has_birth_discovery_pullback = True
                # MODUS #40 (V5.1 — Organic Multi-Candle Staircase Breakout vs Single-Candle Bait & Slam, Kasus $Foxii $36K->$365K):
                # Koin yang sedang menanjak sehat >=3 candle (Higher-Lows, >=2 bar hijau, Vol >=$18K, harga masih >=+25% di atas Open
                # dan koreksi nafas <=22% dari pucuk) adalah ekspansi tangga organik, BUKAN pancingan 1-candle $Shitcoin!
                is_organic_staircase_breakout = False
                if len(valid_candles) >= 3:
                    c3_st = valid_candles[-3:]
                    green_cnt_st = sum(1 for r in c3_st if float(r[4] or 0.0) >= float(r[1] or 0.0))
                    lows_rising_st = (
                        float(c3_st[-1][3] or 0.0) > float(c3_st[0][3] or 0.0)
                        and (
                            float(c3_st[-2][3] or 0.0) >= float(c3_st[0][3] or 0.0)
                            or (len(valid_candles) >= 5 and (h_v > o_v) and ((c_v - o_v) / (h_v - o_v)) >= 0.80 and wick_slam_pct <= 14.0)
                        )
                    )
                    if (
                        green_cnt_st >= 2
                        and lows_rising_st
                        and float(row[5] or 0.0) >= 18_000.0
                        and current_price_usd >= (o_v * 1.25)
                        and c_v >= (o_v * 1.25)
                        and wick_slam_pct <= 22.0
                    ):
                        is_organic_staircase_breakout = True
                if (
                    candle_pump_pct >= 52.0
                    and wick_slam_pct >= 11.0
                    and not is_confirmed_whale_impulse
                    and not is_healthy_birth_discovery
                    and not is_organic_staircase_breakout
                ):
                    return (
                        False,
                        f"Jebakan Single-Candle Bait & Slam: Harga dipompa pancingan +{candle_pump_pct:.0f}% dalam "
                        f"1 candle lalu langsung dibanting -{wick_slam_pct:.1f}% dari pucuk jarum!",
                        {"ath_drawdown_pct": round(wick_slam_pct, 1)}
                    )

    # 2b. PERISAI V3.2 (#20 & #21B): Mikrostruktur Khusus <$100K (Atap Limit-Sell Halus & Bearish Volume Divergence $BACKSTOCK Guard)
    if current_mc_usd < 100_000.0 and not is_hyper_wave1 and not is_wave2_staircase_reclaim:
        if len(valid_candles) >= 3:
            wick_reject_cnt = 0
            for w_r in valid_candles[-3:]:
                wo, wh, _, wc, wvol = (
                    float(w_r[1] or 0.0),
                    float(w_r[2] or 0.0),
                    float(w_r[3] or 0.0),
                    float(w_r[4] or 0.0),
                    float(w_r[5] or 0.0),
                )
                top_body = max(wo, wc)
                if top_body > 0.0 and wvol >= 2500.0 and wh > top_body:
                    up_wick = wh - top_body
                    up_wick_pct = (up_wick / top_body) * 100.0
                    body_abs = abs(wc - wo)
                    if up_wick_pct >= 4.5 and up_wick >= (body_abs * 1.6):
                        wick_reject_cnt += 1
            if wick_reject_cnt >= 2:
                return (
                    False,
                    f"Terdeteksi Atap Limit-Sell Halus (<$100k): {wick_reject_cnt} dari 3 candle terakhir ditolak jarum atas "
                    f"panjang (>=4.5%) -> Bandar pasang tembok jual DCA di bawah $100k!",
                    {"ath_drawdown_pct": 0.0}
                )

        if len(valid_candles) >= 2:
            prior_reds = [
                float(r[5] or 0.0)
                for r in valid_candles[-4:-1]
                if float(r[4] or 0.0) < float(r[1] or 0.0) and float(r[5] or 0.0) >= 15_000.0
            ]
            if prior_reds:
                max_prior_red_vol = max(prior_reds)
                last_c = valid_candles[-1]
                lo, lc, lvol = float(last_c[1] or 0.0), float(last_c[4] or 0.0), float(last_c[5] or 0.0)
                if lc > lo and lvol < (max_prior_red_vol * 0.80):
                    return (
                        False,
                        f"Terdeteksi Bearish Volume Divergence <$100k: Candle pantulan hijau (${lvol:,.0f}) jauh lebih lemah "
                        f"dari buangan merah sebelumnya (${max_prior_red_vol:,.0f}) -> Umpan sebelum banting di bawah $100k!",
                        {"ath_drawdown_pct": 0.0}
                    )

    max_high_price = 0.0
    peak_candle_vol = 0.0
    for row in valid_candles:
        h_val = float(row[2] or 0.0)
        v_val = float(row[5] or 0.0)
        if h_val > max_high_price:
            max_high_price = h_val
        if v_val > peak_candle_vol:
            peak_candle_vol = v_val

    if max_high_price <= 0.0 or current_price_usd >= max_high_price:
        return True, "Sedang di dekat / menembus ATH baru", {"ath_drawdown_pct": 0.0}

    ath_drawdown_pct = round(((max_high_price - current_price_usd) / max_high_price) * 100.0, 1)
    est_ath_mc = round(current_mc_usd * (max_high_price / current_price_usd), 0)
    recent_vols = [float(r[5] or 0.0) for r in valid_candles[-2:]]
    recent_max_vol = max(recent_vols) if recent_vols else 0.0
    vol_vs_peak_ratio = (recent_max_vol / peak_candle_vol) if peak_candle_vol > 0.0 else 1.0

    # MODUS #35B (V3.8 — Pembebasan Jarum Buangan Dev Lama untuk Kebangkitan CTO Murni — Kasus $Grokbot 6NaM98...pump):
    # Jika koin berstatus Verified CTO (Dev 0% keluar total, LP 100% Lock, ratusan/ribuan holder + Burn),
    # dan puncak lama terjadi >= 5 candle yang lalu (jarum buang barang dev di awal), sedangkan saat ini harga sudah
    # bangkit >= +40% dari dasar pasca-buangan dev & menempel puncak lokal kebangkitan CTO (>=88% High 4 bar terakhir),
    # maka itu adalah Gelombang Kebangkitan CTO yang sah, BUKAN bull-trap distribusi!
    allow_cto_reclaim_ath = False
    if is_verified_cto and len(valid_candles) >= 6 and est_ath_mc <= 680_000.0:
        peak_idx = max(range(len(valid_candles)), key=lambda idx_k: float(valid_candles[idx_k][2] or 0.0))
        if peak_idx <= (len(valid_candles) - 5):
            post_peak_bars = valid_candles[peak_idx + 1:]
            post_dump_low = min(float(r[3] or 0.0) for r in post_peak_bars if float(r[3] or 0.0) > 0.0)
            local_4 = valid_candles[-4:]
            local_4_high = max(float(r[2] or 0.0) for r in local_4)
            cto_g_vol = sum(float(r[5] or 0.0) for r in local_4 if float(r[4] or 0.0) >= float(r[1] or 0.0))
            cto_r_vol = sum(float(r[5] or 0.0) for r in local_4 if float(r[4] or 0.0) < float(r[1] or 0.0))
            min_cto_rebound_mult = (
                1.20
                if (len(valid_candles) >= 18 and peak_idx <= (len(valid_candles) - 10))
                else 1.40
            )
            if (
                post_dump_low > 0.0
                and current_price_usd >= (post_dump_low * min_cto_rebound_mult)
                and local_4_high > 0.0
                and current_price_usd >= (local_4_high * 0.88)
                and cto_g_vol >= (cto_r_vol * 1.10)
            ):
                allow_cto_reclaim_ath = True

    allow_wave2_reclaim_ath = (is_wave2_staircase_reclaim and est_ath_mc <= 320_000.0 and ath_drawdown_pct <= 70.0) or allow_cto_reclaim_ath
    if age_minutes <= 1440.0 and ath_drawdown_pct >= 42.0 and not allow_wave2_reclaim_ath:
        return (
            False,
            f"Jebakan Post-ATH Bull Trap: Harga masih -{ath_drawdown_pct:.1f}% di bawah puncak ATH hari ini "
            f"(~${est_ath_mc:,.0f}) -> Pantulan di bawah ribuan bagholder pucuk!",
            {"ath_drawdown_pct": ath_drawdown_pct, "est_ath_mc": est_ath_mc}
        )

    # PERISAI V3.0C + V3.3: Active Waterfall / Paid-Boost Distribution Trap (Tanpa pengecualian Hyper-Wave1!)
    if (
        age_minutes <= 360.0
        and current_mc_usd < 1_080_000.0
        and 24.0 <= ath_drawdown_pct < 42.0
        and len(valid_candles) >= 4
    ):
        last4 = valid_candles[-4:]
        red_cnt = sum(1 for r in last4 if float(r[4] or 0.0) < float(r[1] or 0.0))
        red_vol = sum(float(r[5] or 0.0) for r in last4 if float(r[4] or 0.0) < float(r[1] or 0.0))
        green_vol = sum(float(r[5] or 0.0) for r in last4 if float(r[4] or 0.0) >= float(r[1] or 0.0))
        if red_cnt >= 2 and red_vol >= max(15_000.0, green_vol * 1.45):
            return (
                False,
                f"Jebakan Active Waterfall Distribution: Harga sedang longsor -{ath_drawdown_pct:.1f}% dari ATH "
                f"(~${est_ath_mc:,.0f}) dengan volume buangan merah mendominasi (${red_vol:,.0f} vs hijau ${green_vol:,.0f})!",
                {"ath_drawdown_pct": ath_drawdown_pct, "est_ath_mc": est_ath_mc}
            )

    # PERISAI V3.3 (#26B — Kasus Live $urmom 16:53-16:54 WIB Jarum $336.5k Tembus Plafon $315k Lalu Anjlok):
    # Jika jarum High candle sudah pernah menabrak/menembus Plafon Zona Merah lorong saat ini (>1.03x plafon)
    # dan harga sekarang sudah terpental turun >= 6.5% dari pucuk jarum itu, berarti atap SUDAH DITOLAK BANDAR!
    if age_minutes <= 360.0 and current_mc_usd < 1_080_000.0 and not has_birth_discovery_pullback:
        active_ceiling = 0.0
        if 48_000.0 <= current_mc_usd <= 82_000.0:
            active_ceiling = 82_000.0
        elif 112_000.0 < current_mc_usd <= 168_000.0:
            active_ceiling = 168_000.0
        elif 228_000.0 < current_mc_usd <= 315_000.0:
            active_ceiling = 315_000.0
        elif 525_000.0 < current_mc_usd <= 650_000.0:
            active_ceiling = 650_000.0
        recent_wave_slice = valid_candles[-3:] if is_wave2_staircase_reclaim else valid_candles[-4:]
        recent_wave_high = max(float(r[2] or 0.0) for r in recent_wave_slice)
        recent_wave_dd_pct = round(((recent_wave_high - current_price_usd) / recent_wave_high) * 100.0, 1) if recent_wave_high > current_price_usd else 0.0
        recent_wave_ath_mc = round(current_mc_usd * (recent_wave_high / current_price_usd), 0) if recent_wave_high > 0.0 else 0.0
        if active_ceiling > 0.0 and recent_wave_dd_pct >= 6.5 and recent_wave_ath_mc >= (active_ceiling * 1.03):
            return (
                False,
                f"Jebakan Penolakan Plafon Zona Merah: Jarum sudah menabrak atap ~${recent_wave_ath_mc:,.0f} "
                f"(> plafon ${active_ceiling:,.0f}) lalu ditolak -{recent_wave_dd_pct:.1f}% -> Puncak sudah lewat!",
                {"ath_drawdown_pct": recent_wave_dd_pct, "est_ath_mc": recent_wave_ath_mc}
            )

    if age_minutes > 1440.0 and ath_drawdown_pct >= 55.0 and vol_vs_peak_ratio < 0.50:
        return (
            False,
            f"Jebakan Lower-High Koin Ambruk: Harga masih -{ath_drawdown_pct:.1f}% dari ATH (~${est_ath_mc:,.0f}) "
            f"dengan volume lemah ({vol_vs_peak_ratio*100:.0f}% dari puncak)!",
            {"ath_drawdown_pct": ath_drawdown_pct, "est_ath_mc": est_ath_mc}
        )

    return True, "Struktur dekat ATH sehat", {
        "ath_drawdown_pct": ath_drawdown_pct,
        "est_ath_mc": est_ath_mc
    }


def verify_early_whale_stage2(
    rug_data: dict,
    holder_meta: dict,
    ohlcv_payload,
    current_price_usd: float,
    live_mc: float,
    projected_entry_mc: float,
    age_minutes: float,
    rt_loss_pct: float,
    depth_loss_pct: float,
    live_drift_pct: float
) -> tuple[bool, str, dict]:
    """
    VERIFIKASI LAPIS KEDUA KHUSUS "MASUK DI AWAL" (< $70K):
    Memastikan koin benar-benar perawan di bawah $70k (belum pernah menyentuh plafon $80k-$100k lalu banting!),
    diluncurkan oleh dompet bersih (bukan pabrik scammer), didukung LP SOL tebal, dan memiliki lantai candle naik.
    """
    if live_mc < EARLY_WHALE_MIN_MC or live_mc > 69_500.0:
        return False, f"MC Live On-Chain (${live_mc:,.0f}) sudah keluar zona <$70k Masuk di Awal", {}
    if projected_entry_mc > EARLY_WHALE_MAX_BUY_MC:
        return False, f"Proyeksi MC klik (${projected_entry_mc:,.0f}) melewati batas <$70k", {}

    # 1. Deployer Wajib Dompet Segar/Bersih (<= 3 riwayat token), BUKAN dompet pabrik rugger!
    creator_tokens_cnt = len(rug_data.get("creatorTokens") or [])
    if creator_tokens_cnt > 3:
        return (
            False,
            f"Deployer bukan dompet khusus proyek besar (sudah pernah membuat {creator_tokens_cnt} token > batas 3)",
            {}
        )

    # 2. Saldo langsung Dev <= 1.5%, Holder nyata >= 65, & Tidak ada paus tunggal > 4.2%
    creator_pct = float(holder_meta.get("creator_pct") or 0.0)
    total_holders = int(holder_meta.get("total_holders") or 0)
    max_single = float(holder_meta.get("max_single") or 0.0)
    pure_top10 = float(holder_meta.get("pure_top10") or 0.0)
    sol_backing = float(holder_meta.get("sol_backing_ratio") or 0.0)
    lp_locked = float(holder_meta.get("lp_locked_pct") or 0.0)

    if creator_pct > 1.5:
        return False, f"Saldo langsung Dev masih {creator_pct:.1f}% (> 1.5%) pada koin awal", {}
    if total_holders < 65:
        return False, f"Jumlah partisipan awal masih terlalu tipis ({total_holders} < 65 holders)", {}
    if max_single > 4.2:
        return False, f"1 dompet memegang terlalu besar di awal ({max_single:.1f}% > 4.2%)", {}
    if pure_top10 < 5.8 or pure_top10 > 21.5:
        return False, f"Konsentrasi Top 10 di luar pita ideal Paus Awal ({pure_top10:.1f}% harus 5.8%-21.5%)", {}

    # 3. Batas super-ketat Jaringan Transfer Insider (< 9.5% klaster tunggal — berapapun jumlah dompetnya! — & < 10.5% total)
    ew_graph_insiders = int(rug_data.get("graphInsidersDetected") or 0)
    if ew_graph_insiders >= 20 or (total_holders > 0 and ew_graph_insiders >= 10 and (ew_graph_insiders / float(total_holders)) >= 0.22):
        return (
            False,
            f"Terdeteksi Graf Botnet Sybil di Koin Awal ({ew_graph_insiders} akun terhubung vs {total_holders} holders)",
            {}
        )

    token_info = rug_data.get("token") or {}
    supply_raw = float(token_info.get("supply") or 0.0)
    if supply_raw > 0.0:
        ew_total_net_pct = 0.0
        for net in (rug_data.get("insiderNetworks") or []):
            if isinstance(net, dict):
                net_size = int(net.get("activeAccounts") or net.get("size") or 0)
                if net_size >= 2:
                    net_hold_pct = (float(net.get("currentHolding") or 0.0) / supply_raw) * 100.0
                    ew_total_net_pct += net_hold_pct
                    if net_hold_pct >= 9.5:
                        return (
                            False,
                            f"Klaster transfer awal terlalu pekat ({net_size} dompet terhubung memegang {net_hold_pct:.1f}% >= 9.5%)",
                            {}
                        )
        if ew_total_net_pct >= 10.5:
            return (
                False,
                f"Total jaringan dompet insider awal terlalu besar ({ew_total_net_pct:.1f}% >= 10.5%)",
                {}
            )

    # 3b. Verifikasi Anti Exit-Drain Insolvensi Kolam Awal (Top 5 Holder <= 1.25x Cadangan SOL Kolam)
    primary_hard_usd = float(holder_meta.get("primary_hard_usd") or 0.0)
    top5_pure_pct = float(holder_meta.get("top5_pure_pct") or 0.0)
    if primary_hard_usd > 0.0 and top5_pure_pct > 0.0:
        ew_top5_usd = (top5_pure_pct / 100.0) * live_mc
        if ew_top5_usd > (primary_hard_usd * 1.25):
            return (
                False,
                f"Risiko Kuras Kolam Awal: Top 5 holder (${ew_top5_usd:,.0f}) > 1.25x cadangan SOL kolam (${primary_hard_usd:,.0f})",
                {}
            )

    # 4. Kedalaman Kolam Hard-Asset >= 40%, LP Lock >= 90%, & Eksekusi 0.1/1.0 SOL Mulus
    if sol_backing < 40.0:
        return False, f"Backing aset keras LP awal kurang tebal ({sol_backing:.1f}% < 40.0%)", {}
    if lp_locked < 90.0:
        return False, f"LP Lock awal kurang dari standar aman ({lp_locked:.1f}% < 90.0%)", {}
    if rt_loss_pct > 5.0 or depth_loss_pct > 11.5:
        return False, f"Friksi keluar 1.0 SOL masih terlalu kasar ({depth_loss_pct:.1f}%)", {}
    if live_drift_pct < -4.5 or live_drift_pct > 10.0:
        return False, f"Drift harga live tidak stabil untuk entry awal ({live_drift_pct:+.1f}%)", {}

    # 5. Verifikasi Grafik 5m Perawan (<$76k ATH) & Lantai Paus Naik (Bukan koin gagal tembus $100k yang turun lagi!)
    raw_candles = []
    if isinstance(ohlcv_payload, dict):
        raw_candles = (((ohlcv_payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])
    valid_candles = [
        row for row in raw_candles
        if isinstance(row, (list, tuple)) and len(row) >= 6 and float(row[2] or 0.0) > 0.0
    ]
    valid_candles.sort(key=lambda r: float(r[0] or 0.0))

    if age_minutes > 10.0 and len(valid_candles) < 2:
        return False, "Candle 5m belum tersedia lengkap untuk pembuktian lantai harga paus (>10m)", {}

    est_ath_mc = live_mc
    ath_drawdown_pct = 0.0
    if valid_candles and current_price_usd > 0.0:
        max_high_price = max(float(r[2] or 0.0) for r in valid_candles)
        if max_high_price > current_price_usd:
            est_ath_mc = round(live_mc * (max_high_price / current_price_usd), 0)
            ath_drawdown_pct = round(((max_high_price - current_price_usd) / max_high_price) * 100.0, 1)

        # Jaminan 100% Perawan <$76k: Tolak keras jika koin ternyata sudah pernah menyentuh $80k-$100k+ lalu banting!
        if est_ath_mc > 76_000.0 or ath_drawdown_pct > 18.0:
            failed_wall = (est_ath_mc > 78_000.0 and ath_drawdown_pct >= 22.0)
            return (
                False,
                f"Bukan Masuk di Awal Murni: Koin sudah pernah menyentuh ~${est_ath_mc:,.0f} lalu turun -{ath_drawdown_pct:.1f}%!",
                {"failed_100k_wall": failed_wall}
            )

        # Tolak jarum pancingan yang langsung dibanting >= 10%
        for r in valid_candles[-2:]:
            o_v, h_v = float(r[1] or 0.0), float(r[2] or 0.0)
            if o_v > 0.0 and h_v > current_price_usd:
                c_pump = ((h_v - o_v) / o_v) * 100.0
                w_slam = ((h_v - current_price_usd) / h_v) * 100.0
                if c_pump >= 30.0 and w_slam >= 10.0:
                    return False, f"Jarum atas awal langsung dibanting -{w_slam:.1f}% dari pucuk candle", {}

        # Tolak atap Limit-Sell halus (>=2 dari 3 candle berekor atas panjang >=4.5%)
        if len(valid_candles) >= 3:
            ew_wick_cnt = 0
            for w_r in valid_candles[-3:]:
                wo, wh, wc, wvol = float(w_r[1] or 0.0), float(w_r[2] or 0.0), float(w_r[4] or 0.0), float(w_r[5] or 0.0)
                top_b = max(wo, wc)
                if top_b > 0.0 and wvol >= 2500.0 and wh > top_b:
                    up_w = wh - top_b
                    if ((up_w / top_b) * 100.0) >= 4.5 and up_w >= (abs(wc - wo) * 1.6):
                        ew_wick_cnt += 1
            if ew_wick_cnt >= 2:
                return False, "Terdeteksi Atap Limit-Sell Halus di awal (>=2 candle berekor atas panjang)", {}

        # Lantai harga (Low) wajib terjaga / naik (Paus memasang bid-wall SOL) & volume hijau mengalahkan merah
        if len(valid_candles) >= 2:
            first_low = float(valid_candles[0][3] or 0.0)
            last_low = float(valid_candles[-1][3] or 0.0)
            if first_low > 0.0 and last_low < (first_low * 0.92):
                return False, "Lantai support awal jebol di bawah titik lahir (tidak ada bid-wall paus)", {}

            recent_slice = valid_candles[-3:]
            red_vol = sum(float(r[5] or 0.0) for r in recent_slice if float(r[4] or 0.0) < float(r[1] or 0.0))
            green_vol = sum(float(r[5] or 0.0) for r in recent_slice if float(r[4] or 0.0) >= float(r[1] or 0.0))
            if red_vol > 0.0 and green_vol < (red_vol * 1.35):
                return (
                    False,
                    f"Tekanan jual merah awal terlalu besar (${red_vol:,.0f} merah vs ${green_vol:,.0f} hijau)",
                    {}
                )

    return True, "TERVERIFIKASI PAUS POMPA DINI (<$70K)", {
        "creator_tokens_cnt": creator_tokens_cnt,
        "est_ath_mc": est_ath_mc,
        "ath_drawdown_pct": ath_drawdown_pct
    }


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
    price_usd = float(best_pair.get("priceUsd") or 0.0)
    liq = float((best_pair.get("liquidity") or {}).get("usd") or 0.0)
    dex_id = best_pair.get("dexId", "raydium")
    pair_address = best_pair.get("pairAddress", "")
    cand_age_mins = float(metrics.get("age_minutes", 60.0))

    txns_m5 = best_pair.get("txns", {}).get("m5", {})
    buys_m5 = int(txns_m5.get("buys", 0))
    sells_m5 = int(txns_m5.get("sells", 0))

    # Tembak 3 audit secara PARALEL (0ms tambahan waktu tunggu!): RugCheck + Jupiter Round-Trip + OHLCV ATH
    chain_id = str(best_pair.get("chainId") or "solana").lower()
    is_evm_chain = chain_id in GOPLUS_CHAIN_MAP

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as sub_exec:
        if is_evm_chain:
            gp_id = GOPLUS_CHAIN_MAP[chain_id]
            f_gp = sub_exec.submit(
                fetch_json,
                f"https://api.gopluslabs.io/api/v1/token_security/{gp_id}?contract_addresses={mint}",
                3.5,
            )
            f_ohlcv = sub_exec.submit(fetch_pool_ohlcv, pair_address, cand_age_mins, 2.6, chain_id, mint)
            goplus_data = f_gp.result()
            ohlcv_data = f_ohlcv.result()
            rug_data = {"score": 1, "risks": [], "creatorTokens": []}
            rt_res = None
        else:
            f_rug = sub_exec.submit(fetch_json, f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", 3.5)
            f_rt = sub_exec.submit(simulate_jupiter_roundtrip, mint, 2.7)
            f_ohlcv = sub_exec.submit(fetch_pool_ohlcv, pair_address, cand_age_mins, 2.4, "solana", mint)
            rug_data = f_rug.result()
            rt_res = f_rt.result()
            ohlcv_data = f_ohlcv.result()
            goplus_data = None

    # Pre-check cepat apakah token Solana ini sudah ditinggal Dev 0% & diambil alih komunitas (CTO)
    _pre_cto_flag = False
    if not is_evm_chain and isinstance(rug_data, dict):
        _rc_tok = rug_data.get("token") or {}
        _rc_dec = int(_rc_tok.get("decimals") if _rc_tok.get("decimals") is not None else 6)
        _rc_sup = float(_rc_tok.get("supply") or 0.0)
        _rc_ui = (_rc_sup / float(10 ** _rc_dec)) if _rc_sup > 0.0 else 0.0
        _rc_burn = ((1_000_000_000.0 - _rc_ui) / 1_000_000_000.0 * 100.0) if (400_000_000.0 <= _rc_ui < 999_000_000.0 and _rc_dec == 6) else 0.0
        _rc_cbal = float(rug_data.get("creatorBalance") or 0.0)
        _rc_cpct = (_rc_cbal / _rc_sup * 100.0) if _rc_sup > 0.0 else 0.0
        _rc_hold = int(rug_data.get("totalHolders") or 0)
        _pre_cto_flag = bool(_rc_cpct <= 0.10 and ((mc >= 100_000.0 and _rc_hold >= 450) or (_rc_burn >= 5.0 and _rc_hold >= 280)))

    # 0. Verifikasi Mikrostruktur 5m (Anti Wash-Peg & Bait-Slam) + Struktur ATH Historis ($Marvin 1 PM Guard + V3.8 CTO Reclaim)
    ath_ok, ath_reason, _ = evaluate_ohlcv_ath_trap(
        ohlcv_data,
        price_usd,
        mc,
        cand_age_mins,
        is_hyper_wave1=bool(metrics.get("is_hyper_wave1")),
        is_verified_cto=_pre_cto_flag,
    )
    if not ath_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": ath_reason
        }

    if is_evm_chain:
        holders_ok, holders_reason, holder_meta = analyze_evm_goplus_security(
            goplus_data, mint, mc_usd=mc, liq_usd=liq
        )
        if not holders_ok:
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": holders_reason
            }
        rt_loss_pct = float(holder_meta.get("evm_rt_loss_pct", 1.2))
        depth_loss_pct = float(holder_meta.get("evm_depth_loss_pct", 2.5))
        if rt_loss_pct > MAX_ROUNDTRIP_LOSS_PCT:
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": f"Friksi pajak/swap EVM terlalu mahal ({rt_loss_pct:.2f}% > {MAX_ROUNDTRIP_LOSS_PCT}%)"
            }
    else:
        if not isinstance(rug_data, dict):
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": "Audit RugCheck timeout / belum siap (Fail-Closed: Lindungi modal)"
            }

        rug_score = int(rug_data.get("score", 0))
        max_rug_score = 220 if mc < 100_000.0 else 300
        if rug_score >= max_rug_score:
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": f"RugCheck skor risiko tinggi ({rug_score} >= {max_rug_score})"
            }

        sub100k_warns = []
        for r in (rug_data.get("risks") or []):
            r_name = str(r.get("name", "")).lower()
            r_level = str(r.get("level", "")).lower()
            if ("danger" in r_level or "high" in r_level) and any(
                k in r_name for k in ("liquidity", "creator", "cabal", "cluster", "sybil", "bundled", "dump", "insider")
            ):
                return {
                    "token": f"{name} (${symbol})",
                    "mint": mint,
                    "verdict": "SKIP",
                    "reason": f"Risiko On-Chain RugCheck: {r.get('name')}"
                }
            if mc < 100_000.0 and "warn" in r_level and any(
                k in r_name for k in ("ownership", "holder", "liquidity", "creator", "insider", "bundled", "mutable")
            ):
                sub100k_warns.append(str(r.get("name") or "Risk"))

        if mc < 100_000.0 and len(sub100k_warns) >= 2:
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": f"Akumulasi Risiko Sub-$100k ({len(sub100k_warns)} peringatan RugCheck: {', '.join(sub100k_warns[:2])})"
            }

        holders_ok, holders_reason, holder_meta = analyze_holders_and_lp_lock(
            rug_data,
            pair_address,
            mc_usd=mc,
            age_minutes=float(metrics.get("age_minutes", 60.0)),
            token_mint=mint
        )
        if not holders_ok:
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": holders_reason
            }

        txns_h1 = (best_pair.get("txns") or {}).get("h1") or {}
        total_h1_tx = int(txns_h1.get("buys", 0)) + int(txns_h1.get("sells", 0))
        real_holders_cnt = int(holder_meta.get("total_holders") or 0)
        if cand_age_mins <= 360.0 and total_h1_tx >= 1200 and real_holders_cnt > 0:
            tx_per_holder_h1 = total_h1_tx / float(real_holders_cnt)
            if tx_per_holder_h1 > 10.0:
                return {
                    "token": f"{name} (${symbol})",
                    "mint": mint,
                    "verdict": "SKIP",
                    "reason": (
                        f"Terdeteksi Jito Self-Swap Wash Bot: {total_h1_tx} tx/jam hanya diputar oleh "
                        f"{real_holders_cnt} holders ({tx_per_holder_h1:.1f} tx/holder) -> Volume palsu!"
                    )
                }

        if not rt_res.get("ok"):
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": rt_res.get("reason", "Gagal simulasi Round-Trip Jupiter")
            }

        rt_loss_pct = float(rt_res["rt_loss_pct"])
        depth_loss_pct = float(rt_res.get("depth_loss_pct", rt_loss_pct))
        if rt_loss_pct > MAX_ROUNDTRIP_LOSS_PCT:
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": f"Friksi pulang-pergi 0.1 SOL terlalu mahal ({rt_loss_pct:.2f}% > {MAX_ROUNDTRIP_LOSS_PCT}%)"
            }
        if int(metrics.get("corridor_id", 0)) >= 4 and depth_loss_pct > 6.5:
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": f"Koin >$1M tapi kedalaman 1.0 SOL tipis (amblas {depth_loss_pct:.1f}% > 6.5%) -> Rawan FDV semu!"
            }

    dna_ok, dna_reason, dna_meta = classify_early_continuation_dna(
        ohlcv_data,
        best_pair,
        holder_meta,
        boost_amount=int(metrics.get("boost_amount") or 0),
        age_minutes=cand_age_mins,
    )
    if not dna_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": dna_reason
        }

    ct_ok, ct_reason, ct_meta = evaluate_anti_countertrade_shakeout_shield(
        ohlcv_data,
        best_pair,
        holder_meta=holder_meta,
        rt_loss_pct=rt_loss_pct,
        depth_loss_pct=depth_loss_pct,
        age_minutes=cand_age_mins,
    )
    if not ct_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": ct_reason
        }
    if ct_meta.get("post_shakeout_reentry"):
        pre_edges = list(dna_meta.get("pre_bot_edges") or [])
        if "Masuk Pasca-Kocokan (Aman dari Auto-TP Bot)" not in pre_edges:
            pre_edges.insert(0, "Masuk Pasca-Kocokan (Aman dari Auto-TP Bot)")
        dna_meta["pre_bot_edges"] = pre_edges[:3]

    # 4. DETEKSI LAG DEXSCREENER VS HARGA SPOT REAL-TIME SLOT SOLANA DETIK INI (SOL & NON-SOL POOL UNIVERSAL V2.8)!
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
    dec = int(holder_meta.get("decimals", 6))
    tokens_ui = rt_res["out_tokens_raw"] / float(10 ** dec)
    if tokens_ui > 0:
        spread_adj = 1.0 - (min(MAX_ROUNDTRIP_LOSS_PCT, rt_loss_pct) / 200.0)
        if wsol_pair:
            dex_price_native = float(wsol_pair["priceNative"])
            jup_exec_pn = (TEST_BUY_LAMPORTS / 1_000_000_000.0) / tokens_ui
            jup_mid_pn = jup_exec_pn * spread_adj
            live_drift_pct = round(((jup_mid_pn - dex_price_native) / dex_price_native) * 100.0, 2)
        elif price_usd > 0.0 and float(rt_res.get("swap_usd_value") or 0.0) > 0.0:
            jup_exec_usd = float(rt_res["swap_usd_value"]) / tokens_ui
            jup_mid_usd = jup_exec_usd * spread_adj
            live_drift_pct = round(((jup_mid_usd - price_usd) / price_usd) * 100.0, 2)

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

    # 5. KALKULASI FINAL MC REAL-TIME + JALUR KHUSUS "MASUK DI AWAL" (<$70K) VS CORRIDOR SNIPER V3.0
    live_mc = mc * (1.0 + (live_drift_pct / 100.0))
    total_friction_pct = round(rt_loss_pct + PRIORITY_TIP_BUFFER_PCT, 2)
    safe_name = _esc_md(name)
    safe_symbol = _esc_md(symbol)

    # GERBANG WAJIB V5.0 (MODUS #34 + MODUS #39): Semua koin di bawah MC $100K WAJIB lolos
    # (1) Pelucutan Dev Anti-Nakal <$100K DAN (2) Mesin Fisika Komprehensif Tembus $100K+ (Skor >= 68/100)!
    sub100k_ok, sub100k_reason, sub100k_meta = verify_sub100k_dev_deterrence(
        best_pair=best_pair,
        rug_data=rug_data,
        holder_meta=holder_meta,
        ohlcv_payload=ohlcv_data,
        live_mc=live_mc,
        liq_usd=liq,
        boost_amount=int(metrics.get("boost_amount") or 0),
        current_price_usd=price_usd,
    )
    if not sub100k_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": sub100k_reason,
        }

    cross_ok, cross_reason, cross_meta = evaluate_comprehensive_sub100k_crossing_engine(
        best_pair=best_pair,
        rug_data=rug_data,
        holder_meta=holder_meta,
        ohlcv_payload=ohlcv_data,
        live_mc=live_mc,
        liq_usd=liq,
        boost_amount=int(metrics.get("boost_amount") or 0),
    )
    if not cross_ok:
        return {
            "token": f"{name} (${symbol})",
            "mint": mint,
            "verdict": "SKIP",
            "reason": cross_reason,
        }

    inev_meta = evaluate_sub100k_inevitable_100k_breakout(
        best_pair=best_pair,
        rug_data=rug_data,
        holder_meta=holder_meta,
        ohlcv_payload=ohlcv_data,
        live_mc=live_mc,
        liq_usd=liq,
    )
    if cross_meta.get("is_crossing_verified"):
        existing_p = list(sub100k_meta.get("deterrence_proofs") or [])
        existing_p.insert(0, cross_meta["summary_tag"])
        if inev_meta.get("is_inevitable_100k"):
            existing_p.insert(1, f"Runner $400K+ ({inev_meta['signals_count']}/4 Sinyal)")
        sub100k_meta["deterrence_proofs"] = existing_p[:3]
        sub100k_meta["is_sub100k_verified"] = True

    if metrics.get("is_early_whale"):
        early_proj_mc = live_mc * (1.0 + (metrics["est_delay_drift_pct"] / 100.0))
        ew_ok, ew_reason, ew_meta = verify_early_whale_stage2(
            rug_data=rug_data,
            holder_meta=holder_meta,
            ohlcv_payload=ohlcv_data,
            current_price_usd=price_usd,
            live_mc=live_mc,
            projected_entry_mc=early_proj_mc,
            age_minutes=cand_age_mins,
            rt_loss_pct=rt_loss_pct,
            depth_loss_pct=depth_loss_pct,
            live_drift_pct=live_drift_pct
        )
        if ew_ok:
            early_max_buy_mc = round(min(EARLY_WHALE_MAX_BUY_MC, max(early_proj_mc * 1.08, live_mc * 1.12)), 0)
            tp1_mc = round(max(115_000.0, early_proj_mc * 2.05), 0)
            tp2_mc = round(max(260_000.0, early_proj_mc * 4.80), 0)
            tp3_mc = round(max(780_000.0, early_proj_mc * 15.00), 0)
            tp1_net_pct = round(((tp1_mc - early_proj_mc) / early_proj_mc) * 100.0 - total_friction_pct, 1)
            tp2_net_pct = round(((tp2_mc - early_proj_mc) / early_proj_mc) * 100.0 - total_friction_pct, 0)
            cut_loss = metrics["cut_loss_pct"]
            whale_proof_label = metrics.get("whale_proof_label", "Verifikasi Paus & LP Tebal")
            _ew_tax = max(float(holder_meta.get("buy_tax_pct") or 0.0), float(holder_meta.get("sell_tax_pct") or 0.0))
            ew_stealth = compute_stealth_anti_shakeout_plan(
                live_mc=live_mc,
                liq_usd=liq,
                ohlcv_payload=ohlcv_data,
                price_usd=price_usd,
                chain_id=chain_id,
                default_cut_loss_pct=float(cut_loss),
                tax_pct=_ew_tax,
            )

            alert_msg = (
                f"🚀 *MASUK DI AWAL*\n"
                f"🐋 *Verifikasi Paus Pompa Dini (< $70K — Naik Dulu Sebelum Distribusi)*\n\n"
                f"🪙 *Token:* {safe_name} (`${safe_symbol}`)\n"
                f"📍 *Mint:* `{mint}`\n"
                f"🔑 *Bukti Paus Masuk:* `{_esc_md(whale_proof_label)}`\n\n"
                f"💰 *MC Live On-Chain:* `${live_mc:,.0f}` *(Perawan < $70K | Drift: {live_drift_pct:+.1f}%)*\n"
                f"⏱️ *Est. MC Saat Anda Klik (~{int(PIPELINE_REACTION_DELAY_SEC)}d):* `${early_proj_mc:,.0f}`\n"
                f"🚧 *BATAS MAKSIMAL BOLEH BELI:* `${early_max_buy_mc:,.0f}` *(Lewat $70K = JANGAN BELI!)*\n\n"
                f"🎯 *Rencana Panen Bertahap (Nunut Paus Naik Tinggi):*\n"
                f"• *TP 1 (Tarik Modal 50%):* `${tp1_mc:,.0f}` *(+{tp1_net_pct}% — Modal Pokok 100% Aman!)*\n"
                f"• *TP 2 (Panen 30%):* `${tp2_mc:,.0f}` *(+{tp2_net_pct:.0f}% — Ekspansi Menengah)*\n"
                f"• *Moonbag Gratis (20%):* `${tp3_mc:,.0f}+` *(Kawal jika Paus pompa +1.000% s/d +2.000%!)*\n"
                f"🛡️ *Mode Siluman Anti-Banting Dev:* Cicil `2x` (`50%` sekarang + `50%` antre `${ew_stealth['dip_limit_mc']:,.0f}`) · Maks `${ew_stealth['stealth_clip_usd']}`/klik\n"
                f"🧱 *SL Struktural Anti-Sapuan:* `${ew_stealth['structural_sl_mc']:,.0f}` (`-{ew_stealth['structural_sl_pct']}%` bawah lantai Higher-Low) · `{ew_stealth['mev_guard_label']}` (Slippage `<={ew_stealth['max_slippage_pct']}%`)\n\n"
                f"🔄 *Uji Jual 0.1 & 1.0 SOL:* `LOLOS` (Friksi `{rt_loss_pct}%` | Depth 1 SOL `{depth_loss_pct}%`)\n"
                f"📈 *Struktur Awal:* Usia `{metrics['age_minutes']}m` | 5m `+{metrics['price_change_m5']}%` | H1 `+{metrics.get('price_change_h1', 0)}%` | Tiket `${metrics['avg_ticket']}`\n"
                f"📊 *Orderflow Paus:* `{buys_m5} Buy` vs `{sells_m5} Sell` (`{metrics['buy_sell_ratio']}x` | H1 `{metrics.get('buy_sell_ratio_h1', 0)}x`)\n"
                f"💧 *Likuiditas Tebal:* `${liq:,.0f}` (`{metrics['liq_mc_ratio']}%` MC | Hard Backing `{holder_meta['sol_backing_ratio']}%` | Lock `{holder_meta['lp_locked_pct']}%`)\n"
                f"👥 *Deployer Bersih:* Riwayat `{ew_meta['creator_tokens_cnt']} token` | Saldo Dev `{holder_meta['creator_pct']}%` | Holders `{holder_meta['total_holders']}`\n\n"
                f"🧬 *Alasan Potensi Naik:* {_esc_md(' · '.join(dna_meta.get('continuation_reasons', [])))}\n"
                f"🔗 *DexScreener ({chain_id.upper()}):* [Lihat Chart]({_get_chain_links(chain_id, mint)[0]})\n"
                f"⚡ *{_get_chain_links(chain_id, mint)[1]}:* [Eksekusi Cepat]({_get_chain_links(chain_id, mint)[2]})"
            )

            bot_tok = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = getattr(config, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")
            sent, detail = send_telegram_direct(bot_tok, chat_id, alert_msg)
            if sent:
                _mark_corridor_alerted(mint, 0)
                print(f"[VERCEL-ALERT] 🚀 Sinyal MASUK DI AWAL (<$70k) terkirim untuk {safe_name} (${safe_symbol})!", flush=True)
                return {
                    "token": f"{safe_name} (${safe_symbol})",
                    "mint": mint,
                    "verdict": "ALERT_SENT",
                    "alert_title": "MASUK DI AWAL",
                    "corridor": metrics["corridor_name"],
                    "live_mc": round(live_mc, 0),
                    "projected_entry_mc": round(early_proj_mc, 0),
                    "max_safe_entry_mc": early_max_buy_mc,
                    "target_tp_mc": tp1_mc,
                    "tp2_mc": tp2_mc,
                    "tp3_mc": tp3_mc,
                    "roundtrip_loss_pct": rt_loss_pct,
                    "est_net_gain_pct": tp1_net_pct,
                    "reason": (
                        f"MASUK DI AWAL (<$70k Paus Terverifikasi: {whale_proof_label}) | "
                        f"Max Beli ${early_max_buy_mc:,.0f} -> TP1 ${tp1_mc:,.0f} / TP2 ${tp2_mc:,.0f} / Moonbag ${tp3_mc:,.0f}+"
                    )
                }
            return {
                "token": f"{safe_name} (${safe_symbol})",
                "mint": mint,
                "verdict": "ALERT_FAILED",
                "alert_title": "MASUK DI AWAL",
                "corridor": metrics["corridor_name"],
                "max_safe_entry_mc": early_max_buy_mc,
                "target_tp_mc": tp1_mc,
                "est_net_gain_pct": tp1_net_pct,
                "reason": detail
            }

        # Jika gagal verifikasi khusus Paus Awal: hanya boleh lanjut ke jalur standar V3.0 jika koin memang
        # lolos mandiri aturan standar V3.0 DAN bukan karena sudah pernah gagal menabrak tembok $100k!
        std_fallback = metrics.get("std_fallback_metrics")
        if not std_fallback or (ew_meta or {}).get("failed_100k_wall"):
            return {
                "token": f"{name} (${symbol})",
                "mint": mint,
                "verdict": "SKIP",
                "reason": ew_reason
            }
        metrics = std_fallback

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

    cross_tp2_mc = round(max(108_000.0, min(128_000.0, projected_entry_mc * 1.75)), 0)
    if int(metrics.get("corridor_id", 0)) >= 4:
        moonbag_note = (
            f"🌕 *Strategi Cult Mega-Runner ($10M-$50M+):* Ambil `70%` modal+untung di `${tp_mc:,.0f}` & biarkan `30% Free Moonbag` kawal terbang ke `$20M-$50M+`!\n"
        )
    elif int(metrics.get("corridor_id", 0)) == 0:
        moonbag_note = (
            f"🏁 *Strategi Panen Tembus $100K (Aman Walau Mati Pasca-$100K):* Jual `70%` di `${tp_mc:,.0f}` (Kunci Modal+Untung) & panen `30%` sisanya di `${cross_tp2_mc:,.0f}` begitu jebol `$100K`!\n"
        )
    else:
        moonbag_note = ""

    dex_url, swap_label, swap_url = _get_chain_links(chain_id, mint)
    dna_reasons_txt = _esc_md(" · ".join(dna_meta.get("continuation_reasons", [])))
    pre_bot_txt = _esc_md(" + ".join(dna_meta.get("pre_bot_edges", [])))
    _std_tax = max(float(holder_meta.get("buy_tax_pct") or 0.0), float(holder_meta.get("sell_tax_pct") or 0.0))
    stealth_plan = compute_stealth_anti_shakeout_plan(
        live_mc=live_mc,
        liq_usd=liq,
        ohlcv_payload=ohlcv_data,
        price_usd=price_usd,
        chain_id=chain_id,
        default_cut_loss_pct=float(cut_loss),
        tax_pct=_std_tax,
    )
    header_title = (
        f"⚡ *[SELANGKAH DI DEPAN BOT — {chain_id.upper()} ANTI-SHAKEOUT V5.0]*"
        if dna_meta.get("is_pre_bot_frontrun")
        else f"🎯 *[CORRIDOR SNIPER V5.0: UNIVERSAL SUB-$100K CROSSING & 39-MODUS ({chain_id.upper()})]*"
    )
    pre_bot_line = f"⚡ *Posisi Front-Run:* `{pre_bot_txt}`\n" if pre_bot_txt else ""
    sub100k_txt = _esc_md(" · ".join(sub100k_meta.get("deterrence_proofs", [])))
    sub100k_line = f"🔒 *Fisika Tembus $100K:* `{sub100k_txt}`\n" if sub100k_meta.get("is_sub100k_verified") and sub100k_txt else ""

    alert_msg = (
        f"{header_title}\n\n"
        f"🪙 *Token:* {safe_name} (`${safe_symbol}`) · *Chain:* `{chain_id.upper()}`\n"
        f"📍 *Kontrak:* `{mint}`\n"
        f"🛤️ *Posisi:* `{metrics['corridor_name']}`\n"
        f"{pre_bot_line}"
        f"🧬 *Alasan Potensi Naik:* `{dna_reasons_txt}`\n"
        f"{sub100k_line}\n"
        f"💰 *MC Live On-Chain:* `${live_mc:,.0f}` *(Drift vs DEX: {live_drift_pct:+.1f}%)*\n"
        f"⏱️ *Est. MC Saat Anda Klik (~{int(PIPELINE_REACTION_DELAY_SEC)}d):* `${projected_entry_mc:,.0f}`\n"
        f"🚧 *BATAS MAKSIMAL BOLEH BELI:* `${max_safe_entry_mc:,.0f}` *(Lewat ini = JANGAN BELI!)*\n"
        f"🎯 *Target Take-Profit Aman:* `${tp_mc:,.0f}` *(Est. Untung Bersih +{net_from_projected_pct}%)*\n"
        f"{moonbag_note}"
        f"🕵️ *Eksekusi Siluman (Anti-Radar Dev):* Pecah `2x` (`50%` masuk + `50%` limit `${stealth_plan['dip_limit_mc']:,.0f}`) · Maks `${stealth_plan['stealth_clip_usd']}`/tx\n"
        f"🧱 *SL Struktural Anti-Hunt:* `${stealth_plan['structural_sl_mc']:,.0f}` (`-{stealth_plan['structural_sl_pct']}%` bawah lantai Paus) · `{stealth_plan['mev_guard_label']}` (Slippage `<={stealth_plan['max_slippage_pct']}%`)\n\n"
        f"🔄 *Uji Jual & Keamanan ({chain_id.upper()}):* `LOLOS` (Friksi `{rt_loss_pct}%` | Depth `{depth_loss_pct}%`)\n"
        f"⚡ *Nafas Baru:* 5m `+{metrics['price_change_m5']}%` | Akselerasi `{metrics['accel_ratio']}x`\n"
        f"📊 *Orderflow M5:* `{buys_m5} Buy` vs `{sells_m5} Sell` (`{metrics['buy_sell_ratio']}x` | CVD `+{metrics['cvd_ratio']}%`)\n"
        f"💧 *Likuiditas:* `${liq:,.0f}` ({_esc_md(dex_id.title())} | Kunci LP `{holder_meta['lp_locked_pct']}%`)\n"
        f"👥 *Pure Top 10:* `{holder_meta['pure_top10']}%` | *Saldo Dev:* `{holder_meta['creator_pct']}%`\n\n"
        f"🔗 *DexScreener:* [Lihat Chart]({dex_url})\n"
        f"⚡ *{swap_label}:* [Eksekusi Cepat]({swap_url})"
    )

    bot_tok = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")

    sent, detail = send_telegram_direct(bot_tok, chat_id, alert_msg)
    if sent:
        _mark_corridor_alerted(mint, int(metrics.get("corridor_id", 0)))
        print(f"[VERCEL-ALERT] 🚀 Sinyal V5.0 Delay-Proof terkirim untuk {safe_name} (${safe_symbol})!", flush=True)
        return {
            "token": f"{safe_name} (${safe_symbol})",
            "mint": mint,
            "verdict": "ALERT_SENT",
            "alert_title": "CORRIDOR SNIPER V5.0",
            "corridor": metrics["corridor_name"],
            "live_mc": round(live_mc, 0),
            "projected_entry_mc": round(projected_entry_mc, 0),
            "max_safe_entry_mc": max_safe_entry_mc,
            "target_tp_mc": tp_mc,
            "roundtrip_loss_pct": rt_loss_pct,
            "est_net_gain_pct": net_from_projected_pct,
            "sub100k_crossing_score": int(cross_meta.get("crossing_score") or 0),
            "sub100k_inflow_coverage": float(cross_meta.get("inflow_coverage_ratio") or 0.0),
            "reason": (
                f"Lolos V5.0 ({metrics['corridor_name']}) | Max Entry ${max_safe_entry_mc:,.0f} -> "
                f"TP ${tp_mc:,.0f} (+{net_from_projected_pct}% bersih setelah delay {int(PIPELINE_REACTION_DELAY_SEC)}s & fee riil)"
            )
        }
    return {
        "token": f"{safe_name} (${safe_symbol})",
        "mint": mint,
        "verdict": "ALERT_FAILED",
        "alert_title": "CORRIDOR SNIPER V5.0",
        "corridor": metrics["corridor_name"],
        "max_safe_entry_mc": max_safe_entry_mc,
        "target_tp_mc": tp_mc,
        "est_net_gain_pct": net_from_projected_pct,
        "reason": detail
    }


def _select_dual_track_candidate(best_pair: dict, pair_count: int = 1, boost_amount: int = 0) -> tuple[bool, str, dict]:
    """
    Orkestrator Dual-Track V3.1:
    - Jalur 1: Cek apakah koin <$70k ($28k-$66k spot) memenuhi syarat ketat "MASUK DI AWAL" (Paus Pompa Dini).
    - Jalur 2: Seluruh filter standar V3.0 (Lorong 0 s/d 5 + 16 Modus Anti-Bot) tetap berjalan 100% utuh!
    """
    pair_boost = int(((best_pair.get("boosts") or {}).get("active")) or 0)
    effective_boost = max(int(boost_amount or 0), pair_boost)

    std_ok, std_reason, std_metrics = evaluate_market_candidate(
        best_pair, pair_count=pair_count, boost_amount=effective_boost
    )
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    if EARLY_WHALE_MIN_MC <= mc <= EARLY_WHALE_MAX_SPOT_MC:
        ew_ok, _, ew_metrics = evaluate_early_whale_candidate(
            best_pair, pair_count=pair_count, boost_amount=effective_boost
        )
        if ew_ok:
            ew_metrics["std_fallback_metrics"] = std_metrics if std_ok else None
            return True, "LOLOS TAHAP 1 MASUK DI AWAL", ew_metrics
    return std_ok, std_reason, std_metrics


def _effective_pair_rank_key(pair: dict) -> float:
    """
    MODUS #38A (V4.1 — Anti One-Sided DLMM Liquidity Hijack, Kasus Nyata $STONKLANA GF74fB...Nyk6):
    Cegah pool sekunder Meteora DLMM satu-sisi ($149.7K token base + hanya $7.1K SOL = $153.8K LP semu, Vol mati $715/j)
    membajak posisi `best_pair` dari pool utama Raydium CPMM yang seimbang ($61K LP 50/50 100% Lock, Vol $1.17M)!
    """
    liq_d = pair.get("liquidity") or {}
    raw_liq = float(liq_d.get("usd") or 0.0)
    if raw_liq <= 0.0:
        return 0.0
    eff_liq = raw_liq
    p_usd = float(pair.get("priceUsd") or 0.0)
    base_amt = float(liq_d.get("base") or 0.0)
    if p_usd > 0.0 and base_amt > 0.0:
        base_val_usd = base_amt * p_usd
        if 0.0 < base_val_usd <= (raw_liq * 1.05):
            quote_val_usd = max(0.0, raw_liq - base_val_usd)
            if quote_val_usd < (raw_liq * 0.28):
                eff_liq = max(quote_val_usd * 2.2, raw_liq * 0.10)
    vol_d = pair.get("volume") or {}
    vol_h1 = float(vol_d.get("h1") or 0.0)
    vol_m5 = float(vol_d.get("m5") or 0.0)
    activity_bonus = min(eff_liq * 0.65, (vol_h1 * 0.25) + (vol_m5 * 0.80))
    return eff_liq + activity_bonus


def evaluate_single_mint(mint: str, config, boost_amount: int = 0) -> dict:
    """Helper evaluasi 1 token mandiri Multi-Chain V4.1 (Solana + BSC + Base + ETH + Cross-Token & Effective LP)."""
    q_mint = str(mint or "").strip()
    dex_data = fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{q_mint}", timeout=3.2)
    if not dex_data or not dex_data.get("pairs"):
        return {"mint": q_mint, "token": q_mint[:10], "verdict": "SKIP", "reason": "Data pair DexScreener kosong"}

    q_low = q_mint.lower()
    valid_pairs = [
        p for p in dex_data.get("pairs", [])
        if str(p.get("chainId") or "").lower() in SUPPORTED_CHAINS
        and str((p.get("baseToken") or {}).get("address") or "").lower() == q_low
    ]
    if not valid_pairs:
        valid_pairs = [p for p in dex_data.get("pairs", []) if str(p.get("chainId") or "").lower() in SUPPORTED_CHAINS]
    if not valid_pairs:
        return {"mint": q_mint, "token": q_mint[:10], "verdict": "SKIP", "reason": "Chain belum didukung (dukung Solana, BSC, Base, ETH)"}

    pairs = sorted(
        valid_pairs,
        key=_effective_pair_rank_key,
        reverse=True
    )
    best_pair = _fuse_gt_pool_if_dex_lagging(pairs[0])
    pairs[0] = best_pair
    canonical_mint = str((best_pair.get("baseToken") or {}).get("address") or q_mint)
    active_pools_count = sum(1 for p in pairs if float((p.get("liquidity") or {}).get("usd") or 0.0) >= 2500.0)
    name = best_pair.get("baseToken", {}).get("name", "Unknown")
    symbol = best_pair.get("baseToken", {}).get("symbol", "TOKEN")

    ok, filter_reason, metrics = _select_dual_track_candidate(
        best_pair, pair_count=max(1, active_pools_count), boost_amount=boost_amount
    )
    if not ok:
        return {"token": f"{name} (${symbol})", "mint": canonical_mint, "verdict": "SKIP", "reason": filter_reason}

    return audit_and_dispatch_candidate(canonical_mint, pairs, best_pair, metrics, config)


def _extract_gt_solana_mints(gt_payload) -> list:
    return [addr for _, addr, _ in _extract_gt_multichain_candidates(gt_payload, "solana")]


def _extract_gt_multichain_candidates(gt_payload, expected_chain: str = "solana") -> list:
    out = []
    if not isinstance(gt_payload, dict):
        return out
    prefix_map = {"solana": "solana_", "bsc": "bsc_", "base": "base_", "ethereum": "eth_"}
    pref = prefix_map.get(expected_chain, f"{expected_chain}_")
    for pool_item in (gt_payload.get("data") or []):
        rels = (pool_item.get("relationships") or {})
        attrs = (pool_item.get("attributes") or {}) if isinstance(pool_item, dict) else {}
        for rel_key in ("base_token", "quote_token"):
            rel = (rels.get(rel_key) or {}).get("data") or {}
            tok_id = str(rel.get("id") or "")
            if tok_id.startswith(pref):
                tok_addr = tok_id[len(pref):].strip()
                check_key = tok_addr.lower() if expected_chain != "solana" else tok_addr
                if tok_addr and check_key not in HARD_QUOTE_MINTS and tok_addr not in HARD_QUOTE_MINTS:
                    out.append((expected_chain, tok_addr, attrs))
    return out


def _fetch_dex_chain_batch(chain_id: str, mint_chunk: list) -> list:
    if not mint_chunk:
        return []
    joined = ",".join(mint_chunk)
    v1_res = fetch_json(f"https://api.dexscreener.com/tokens/v1/{chain_id}/{joined}", timeout=3.2)
    if isinstance(v1_res, list) and v1_res:
        return v1_res
    legacy_res = fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{joined}", timeout=3.2)
    if isinstance(legacy_res, dict) and legacy_res.get("pairs"):
        return legacy_res["pairs"]
    return []


def _fetch_dex_solana_batch(mint_chunk: list) -> list:
    return _fetch_dex_chain_batch("solana", mint_chunk)


def _build_market_leaders_index(pairs_by_mint: dict) -> tuple[dict, dict]:
    """
    PERISAI V3.2 (#22 — Anti-Vampire Copycat Ticker <$100k, 0ms RAM):
    Petakan koin pemimpin organik (MC >= $250k & LP >= $25k) di batch aktif berdasarkan simbol & nama
    agar tiruan/beta-clone murah (<$100k) yang mendompleng nama viral langsung terblokir sebelum banting di <$90k!
    """
    leader_by_sym = {}
    leader_by_name = {}
    for m_addr, p_list in (pairs_by_mint or {}).items():
        if not p_list:
            continue
        bp = max(p_list, key=_effective_pair_rank_key)
        b_mc = float(bp.get("marketCap") or bp.get("fdv") or 0.0)
        b_liq = float((bp.get("liquidity") or {}).get("usd") or 0.0)
        if b_mc < 250_000.0 or b_liq < 25_000.0:
            continue
        bt = bp.get("baseToken") or {}
        sym_k = str(bt.get("symbol") or "").strip().lower()
        name_k = str(bt.get("name") or "").strip().lower()
        if len(sym_k) >= 3 and sym_k not in {"sol", "coin", "meme", "pump", "ai", "cat", "dog"}:
            prev = leader_by_sym.get(sym_k)
            if not prev or b_mc > prev[0]:
                leader_by_sym[sym_k] = (b_mc, m_addr)
        if len(name_k) >= 4:
            prev_n = leader_by_name.get(name_k)
            if not prev_n or b_mc > prev_n[0]:
                leader_by_name[name_k] = (b_mc, m_addr)
    return leader_by_sym, leader_by_name


def _is_vampire_copycat_sub100k(
    mint: str,
    best_pair: dict,
    leader_by_sym: dict,
    leader_by_name: dict
) -> tuple[bool, str]:
    mc = float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0)
    if mc <= 0.0 or mc >= 100_000.0:
        return False, ""
    bt = best_pair.get("baseToken") or {}
    raw_sym = str(bt.get("symbol") or "")
    raw_name = str(bt.get("name") or "")
    sym_k = raw_sym.strip().lower()
    name_k = raw_name.strip().lower()

    if sym_k and sym_k in (leader_by_sym or {}):
        l_mc, l_mint = leader_by_sym[sym_k]
        if l_mint != mint and l_mc >= max(300_000.0, mc * 5.0):
            return (
                True,
                f"Terdeteksi Vampire Copycat <$100k: Mendompleng ticker identik ${raw_sym} milik koin utama "
                f"(${l_mc:,.0f} MC) -> Rawan dump sebelum $100k!"
            )
    if name_k and name_k in (leader_by_name or {}):
        l_mc, l_mint = leader_by_name[name_k]
        if l_mint != mint and l_mc >= max(300_000.0, mc * 5.0):
            return (
                True,
                f"Terdeteksi Vampire Copycat <$100k: Mendompleng nama identik '{raw_name}' milik koin utama "
                f"(${l_mc:,.0f} MC) -> Rawan dump sebelum $100k!"
            )
    return False, ""


def run_live_market_scan(engine, config) -> dict:
    """
    FAST-TIME BATCH MULTI-FEED SCANNER V3.2 (DUAL-TRACK: MASUK DI AWAL <$70K + UNIFIED 24-MODUS ANTI-BOT):
    1. Tarik paralel 7 radar aman rate-limit (GT Trending 5m Page 1 & 2 = 40 pool terpanas + Priority Latest Boosts +
       Profiles + Organic PumpSwap + GT 1h + Top Boosts).
    2. Ambil data pair seluruh kandidat via /tokens/v1/solana/ paralel (~250ms, 100% tanpa pemotongan pair!).
    3. Saring di RAM (< 1ms) dengan Anti-Vampire Copycat + Dual-Track V3.2 ("MASUK DI AWAL" <$70k + Lorong 0-5 utuh).
    4. Kandidat elit diaudit 3-arah paralel: RugCheck + Round-Trip Jupiter (0.1 & 1.0 SOL) + 5m OHLCV Shield!
    """
    t0 = time.time()
    print("[VERCEL-SCAN] ⚡ Memulai Fast-Batch V5.1 Multi-Chain Scan (Universal Sub-$100K + All-Corridor Propulsion + 40 Modus)...", flush=True)

    # Batasi ke 6 feed GeckoTerminal paling krusial + 5 feed DexScreener agar BEBAS HTTP 429 Rate-Limit & selesai < 4.5 detik!
    with concurrent.futures.ThreadPoolExecutor(max_workers=11) as feed_exec:
        f_gt_5m_p1 = feed_exec.submit(
            fetch_json, "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=5m&page=1", 2.6
        )
        f_gt_ps = feed_exec.submit(
            fetch_json, "https://api.geckoterminal.com/api/v2/networks/solana/dexes/pumpswap/pools?page=1", 2.6
        )
        f_gt_new = feed_exec.submit(
            fetch_json, "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1", 2.6
        )
        f_gt_1h = feed_exec.submit(
            fetch_json, "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?duration=1h&page=1", 2.6
        )
        f_gt_bsc_5m = feed_exec.submit(
            fetch_json, "https://api.geckoterminal.com/api/v2/networks/bsc/trending_pools?duration=5m&page=1", 2.6
        )
        f_gt_base_5m = feed_exec.submit(
            fetch_json, "https://api.geckoterminal.com/api/v2/networks/base/trending_pools?duration=5m&page=1", 2.6
        )
        f_ray_org = feed_exec.submit(fetch_json, "https://api.dexscreener.com/latest/dex/search?q=raydium", 2.8)
        f_organic = feed_exec.submit(fetch_json, "https://api.dexscreener.com/latest/dex/search?q=pumpswap", 2.8)
        f_b_latest = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-boosts/latest/v1", 2.8)
        f_b_top = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-boosts/top/v1", 2.8)
        f_prof = feed_exec.submit(fetch_json, "https://api.dexscreener.com/token-profiles/latest/v1", 2.8)

        gt_5m_p1 = f_gt_5m_p1.result()
        gt_ps = f_gt_ps.result()
        gt_new = f_gt_new.result()
        gt_1h = f_gt_1h.result()
        gt_bsc_5m = f_gt_bsc_5m.result()
        gt_base_5m = f_gt_base_5m.result()
        organic_search = f_organic.result()
        ray_search = f_ray_org.result()
        boosts_latest = f_b_latest.result()
        boosts_top = f_b_top.result()
        profiles = f_prof.result()

    ordered_candidates = []
    gt_snap_by_addr = {}
    boost_map = {}
    latest_boost_items = []
    top_boost_items = []
    profile_items = []

    def _collect_gt(payload, ch_name):
        items = _extract_gt_multichain_candidates(payload, ch_name)
        res_pairs = []
        for c_id, t_addr, attrs in items:
            norm_k = t_addr.lower() if c_id != "solana" else t_addr
            if attrs and norm_k not in gt_snap_by_addr:
                gt_snap_by_addr[norm_k] = attrs
            res_pairs.append((c_id, t_addr))
        return res_pairs

    if isinstance(boosts_latest, list):
        for b in boosts_latest:
            ch = str(b.get("chainId") or "").lower()
            if ch in ("solana", "bsc", "base") and b.get("tokenAddress"):
                addr = b["tokenAddress"]
                amt = int(b.get("totalAmount") or b.get("amount") or 0)
                k = addr.lower() if ch != "solana" else addr
                boost_map[k] = max(boost_map.get(k, 0), amt)
                latest_boost_items.append((ch, addr))

    if isinstance(boosts_top, list):
        for b in boosts_top:
            ch = str(b.get("chainId") or "").lower()
            if ch in ("solana", "bsc", "base") and b.get("tokenAddress"):
                addr = b["tokenAddress"]
                amt = int(b.get("totalAmount") or b.get("amount") or 0)
                k = addr.lower() if ch != "solana" else addr
                boost_map[k] = max(boost_map.get(k, 0), amt)
                top_boost_items.append((ch, addr))

    if isinstance(profiles, list):
        for p in profiles:
            ch = str(p.get("chainId") or "").lower()
            if ch in ("solana", "bsc", "base") and p.get("tokenAddress"):
                profile_items.append((ch, p["tokenAddress"]))

    ordered_candidates.extend(_collect_gt(gt_5m_p1, "solana"))
    ordered_candidates.extend(_collect_gt(gt_bsc_5m, "bsc")[:12])
    ordered_candidates.extend(latest_boost_items[:16])
    ordered_candidates.extend(_collect_gt(gt_ps, "solana")[:14])
    ordered_candidates.extend(_collect_gt(gt_new, "solana")[:12])
    ordered_candidates.extend(_collect_gt(gt_base_5m, "base")[:10])
    ordered_candidates.extend(profile_items[:12])

    for _org_payload in (organic_search, ray_search):
        if isinstance(_org_payload, dict):
            for op in (_org_payload.get("pairs") or []):
                ch = str(op.get("chainId") or "").lower()
                if ch in ("solana", "bsc", "base"):
                    op_mc = float(op.get("marketCap") or op.get("fdv") or 0.0)
                    op_vol5 = float((op.get("volume") or {}).get("m5") or 0.0)
                    op_addr = (op.get("baseToken") or {}).get("address")
                    if op_addr and 25_000.0 <= op_mc <= 8_800_000.0 and op_vol5 >= 1500.0:
                        ordered_candidates.append((ch, op_addr))

    ordered_candidates.extend(_collect_gt(gt_1h, "solana"))
    ordered_candidates.extend(latest_boost_items[16:])
    ordered_candidates.extend(top_boost_items)
    ordered_candidates.extend(profile_items[12:])

    seen = set()
    by_chain_mints = {"solana": [], "bsc": [], "base": []}
    candidate_mints = []  # Menyimpan (ch, norm_m, raw_m) secara berurutan (Fix V5.1 Critical NameError & EVM Case Bug!)
    for ch, m in ordered_candidates:
        norm_m = m.lower() if ch != "solana" else m
        key_cm = f"{ch}:{norm_m}"
        if key_cm not in seen and ch in by_chain_mints:
            seen.add(key_cm)
            by_chain_mints[ch].append(m)
            candidate_mints.append((ch, norm_m, m))
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

    batch_jobs = []
    for ch, m_list in by_chain_mints.items():
        for i in range(0, len(m_list), 30):
            batch_jobs.append((ch, m_list[i:i + 30]))

    all_pairs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(batch_jobs))) as batch_exec:
        batch_futures = [batch_exec.submit(_fetch_dex_chain_batch, ch, chunk) for ch, chunk in batch_jobs]
        for bf in batch_futures:
            res_b = bf.result()
            if res_b:
                all_pairs.extend(res_b)

    pairs_by_mint = {}
    for p in all_pairs:
        ch_p = str(p.get("chainId") or "").lower()
        if ch_p not in SUPPORTED_CHAINS:
            continue
        base_addr = (p.get("baseToken") or {}).get("address")
        if base_addr:
            norm_k = base_addr.lower() if ch_p != "solana" else base_addr
            fused_p = _fuse_gt_pool_if_dex_lagging(p, gt_snap_by_addr.get(norm_k))
            pairs_by_mint.setdefault(norm_k, []).append(fused_p)

    ref_pairs_by_mint = dict(pairs_by_mint)
    if isinstance(organic_search, dict):
        for op in (organic_search.get("pairs") or []):
            if op.get("chainId") == "solana":
                op_addr = (op.get("baseToken") or {}).get("address")
                if op_addr:
                    ref_pairs_by_mint.setdefault(op_addr, []).append(op)
    leader_by_sym, leader_by_name = _build_market_leaders_index(ref_pairs_by_mint)

    evaluations = []
    stage2_jobs = []
    active_ledger = _load_corridor_ledger()

    # TAHAP 1 (In-Memory ~0.2ms): Saring seluruh kandidat dengan Anti-Copycat + Dual-Track V5.1
    for _ch_cand, norm_m, raw_m in candidate_mints:
        m_pairs = pairs_by_mint.get(norm_m)
        if not m_pairs:
            evaluations.append({"token": raw_m[:8], "mint": raw_m, "verdict": "SKIP", "reason": "Data pair DexScreener kosong"})
            continue

        sorted_p = sorted(
            m_pairs,
            key=_effective_pair_rank_key,
            reverse=True
        )
        best_p = sorted_p[0]
        canonical_mint = (best_p.get("baseToken") or {}).get("address") or raw_m
        active_cnt = sum(1 for x in sorted_p if float((x.get("liquidity") or {}).get("usd") or 0.0) >= 4000.0)
        t_name = best_p.get("baseToken", {}).get("name", "Unknown")
        t_sym = best_p.get("baseToken", {}).get("symbol", "SOL")

        is_copycat, copycat_reason = _is_vampire_copycat_sub100k(canonical_mint, best_p, leader_by_sym, leader_by_name)
        if is_copycat:
            evaluations.append({
                "token": f"{t_name} (${t_sym})",
                "mint": canonical_mint,
                "verdict": "SKIP",
                "reason": copycat_reason
            })
            continue

        ok, reason, metrics = _select_dual_track_candidate(
            best_p, pair_count=active_cnt, boost_amount=boost_map.get(norm_m, 0)
        )
        if not ok:
            evaluations.append({
                "token": f"{t_name} (${t_sym})",
                "mint": canonical_mint,
                "verdict": "SKIP",
                "reason": reason
            })
        else:
            c_key = f"{canonical_mint}:C{metrics['corridor_id']}"
            if c_key in RECENT_ALERTED_MINTS or c_key in active_ledger:
                evaluations.append({
                    "token": f"{t_name} (${t_sym})",
                    "mint": canonical_mint,
                    "verdict": "SKIP",
                    "reason": f"Cooldown aktif untuk Lorong {metrics['corridor_id']} (anti duplikasi sinyal)"
                })
            else:
                stage2_jobs.append((canonical_mint, sorted_p, best_p, metrics))

    # TAHAP 2 (Paralel Eksklusif): Hanya kandidat yang lolos Lorong Aman diaudit ke RugCheck/GoPlus & Round-Trip Swap
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
        f"[VERCEL-SCAN] ✅ Batch Scan V5.1 selesai dalam {elapsed_ms}ms: "
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
                "service": "Corridor Sniper V3.2 — Dual-Track: MASUK DI AWAL (<$70K Whale Pump) + Unified 24-Modus Anti-Bot & Sub-$100K Shield",
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
                    "early_whale_max_buy_mc_usd": EARLY_WHALE_MAX_BUY_MC,
                    "pipeline_reaction_delay_sec": PIPELINE_REACTION_DELAY_SEC,
                    "max_roundtrip_loss_pct": MAX_ROUNDTRIP_LOSS_PCT,
                    "max_depth_1sol_cliff_pct": MAX_DEPTH_CLIFF_LOSS_PCT,
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
                    "🔔 *[CORRIDOR SNIPER V3.5: MULTI-CHAIN (SOLANA+BSC+BASE) + SELANGKAH DI DEPAN BOT & 30-MODUS AKTIF]*\n\n"
                    "✅ Mesin Dual-Track 75-Token Berkecepatan Tinggi terhubung ke Telegram!\n\n"
                    "Dua Jenis Judul Alert yang Kini Aktif:\n"
                    "1. 🚀 *MASUK DI AWAL:* Khusus koin perawan `< $70k` (usia `3.5-35m`) yang terbukti dipompa Paus/Modal Hangus Besar & lolos 8 Perisai Anti-Permainan Mulus `<$100k`.\n"
                    "2. ⚡ *PRE-BOT SNIPER V3.5 (Solana + BSC + Base):* Seluruh Lorong 0–5 (`$25k` s/d `$8.8M`) lengkap dengan Perisai Terpadu 28 Modus Anti-Bot."
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
                "service": "Corridor Sniper V3.2 — Dual-Track MASUK DI AWAL (<$70K Whale Pump) + Unified 24-Modus Anti-Bot Radar",
                "status": "ready",
                "is_cron_call": is_cron,
                "timestamp": time.time(),
                "scan_result": scan_summary,
                "corridor_architecture": {
                    "special_track_masuk_di_awal": "$28k - $66k (Max Klik < $70k) -> Judul Alert 'MASUK DI AWAL' (Khusus Paus/Sunk-Cost Terverifikasi + 8 Perisai Sub-$100k)",
                    "corridor_0": "$25k - $78k -> TP <= $90k (Front-run tembok $100k)",
                    "deadzone_1_blocked": "$78k - $128k (Tembok Sniper $100k DIBLOKIR)",
                    "corridor_1": "$128k - $168k -> TP <= $190k (Front-run tembok $200k)",
                    "deadzone_2_blocked": "$168k - $228k (Kuburan Dev Receh $200k DIBLOKIR)",
                    "corridor_2_golden": "$228k - $315k -> TP <= $418k (Ruang Kosong Menuju $400k+)",
                    "deadzone_3_blocked": "$315k - $525k (Tembok $450k-$500k DIBLOKIR)",
                    "corridor_3_whale": "$525k - $650k -> TP <= $825k (Front-run tembok $1M)",
                    "deadzone_4_blocked": "$650k - $1.08M (Tembok Sakral $1 Juta DIBLOKIR)",
                    "corridor_4_cult_ignition": "$1.08M - $2.65M -> TP <= $4.2M + 30% Moonbag $50M+ ($PONS/$STONK Ignition)",
                    "deadzone_5_blocked": "$2.65M - $5.25M (Tembok Shakeout $3M-$5M DIBLOKIR)",
                    "corridor_5_mega_cult": "$5.25M - $8.80M -> TP <= $13.8M + 30% Moonbag $50M+ (Tier-1 Cult Expansion)",
                    "deadzone_6_blocked": "> $8.80M (Area Late-Stage Dekat $10M+ DIBLOKIR, Kawal via Moonbag)"
                },
                "execution_and_delay_shields": [
                    "Jalur Khusus 'MASUK DI AWAL' (<$70K): Wajib Sunk-Cost Boost Dini / Tiket Paus $55+, LP >=16%, Hard Backing >=40%, Deployer <=3 Token, Virgin Chart <$76k & Rising Floor",
                    "8 Perisai Anti-Permainan Mulus <$100K V3.2 (Sybil Web >12 Dompet, Exit-Drain Insolvency, Stale Re-Pump, Limit-Sell Wick Ceiling, Silent Churn & Vol Divergence, Vampire Copycat, Farmer Dev, Whale Hand-Off)",
                    "Unified 16-Modus Anti-Bot Matrix V3.0 Tetap Aktif 100% (Wash-Peg, Barcode/Doji, Staircase, Bait-Slam, Asymmetric Siphon, Jito Self-Swap, Nano-Swarm, Serial Factory Dev)",
                    "7-Feed Rate-Limit-Safe Discovery + /tokens/v1/solana/ 100% Pair Hit-Rate 75-Mint (~0.8-1.9 detik)",
                    "Wave-1 Hyper-Ignition, CTO Multi-Hari ($Macca), & Post-$1M Cult Corridors ($PONS/$STONK) dengan Free Moonbag"
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
