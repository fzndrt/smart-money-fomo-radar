import os
import sys
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from api.fomo import (
    _esc_md,
    analyze_holders_and_lp_lock,
    classify_entry_corridor,
    evaluate_market_candidate,
    SOL_MINT,
)
from engine import SmartMoneyEngine, TradeEvent, TOP_100_WALLETS
from config import FomoConfig


def _build_healthy_pair(mc: float = 250_000.0, liq: float = 36_000.0, p_m5: float = 16.0, age_mins: float = 35.0) -> dict:
    return {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1",
        "baseToken": {"address": "DemoMint1111111111111111111111111111111111", "name": "Alpha_Coin*[V2]", "symbol": "ALP_V2"},
        "quoteToken": {"address": SOL_MINT, "symbol": "SOL"},
        "priceNative": "0.0000125",
        "marketCap": mc,
        "fdv": mc,
        "liquidity": {"usd": liq},
        "pairCreatedAt": int((time.time() - (age_mins * 60.0)) * 1000),
        "priceChange": {"m5": p_m5, "h1": 42.0, "h6": 68.0},
        "txns": {
            "m5": {"buys": 390, "sells": 120},
            "h1": {"buys": 980, "sells": 520},
            "h24": {"buys": 1150, "sells": 620},
        },
        "volume": {"m5": 52_000.0, "h1": 340_000.0},
        "info": {"socials": [{"type": "twitter"}]},
    }


def _build_clean_rugcheck(creator_pct: float = 0.5, sol_usd: float = 18_000.0, token_usd: float = 18_000.0) -> dict:
    supply = 1_000_000_000.0
    return {
        "rugged": False,
        "score": 100,
        "token": {
            "decimals": 6,
            "supply": supply,
            "mintAuthority": None,
            "freezeAuthority": None,
        },
        "transferFee": {"pct": 0.0},
        "token_extensions": [],
        "creatorBalance": supply * (creator_pct / 100.0),
        "markets": [
            {
                "pubkey": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1",
                "marketType": "raydium_amm",
                "lp": {
                    "baseMint": "DemoMint1111111111111111111111111111111111",
                    "quoteMint": SOL_MINT,
                    "baseUSD": token_usd,
                    "quoteUSD": sol_usd,
                    "lpLockedPct": 99.5,
                    "lpLockedUSD": (sol_usd + token_usd) * 0.995,
                    "lpMint": "11111111111111111111111111111111",
                },
            }
        ],
        "topHolders": [
            {"address": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1", "pct": 19.0, "insider": False},
            {"address": "HolderA111111111111111111111111111111111111", "pct": 2.4, "insider": False},
            {"address": "HolderB111111111111111111111111111111111111", "pct": 1.9, "insider": False},
            {"address": "HolderC111111111111111111111111111111111111", "pct": 1.4, "insider": False},
            {"address": "HolderD111111111111111111111111111111111111", "pct": 1.1, "insider": False},
        ],
    }


class CorridorSniperV2TestSuite(unittest.TestCase):
    def test_corridors_and_red_zone_ceilings(self):
        # Lorong 0 ($52k) lolos
        ok0, _, cfg0 = classify_entry_corridor(52_000.0)
        self.assertTrue(ok0)
        self.assertEqual(cfg0["corridor_id"], 0)

        # Zona Merah $100k ($95k) diblokir
        ok_z1, msg_z1, _ = classify_entry_corridor(95_000.0)
        self.assertFalse(ok_z1)
        self.assertIn("ZONA MERAH PLAFON $100K", msg_z1)

        # Zona Merah $200k ($195k) diblokir
        ok_z2, msg_z2, _ = classify_entry_corridor(195_000.0)
        self.assertFalse(ok_z2)
        self.assertIn("ZONA MERAH PLAFON $200K", msg_z2)

        # Lorong 2 Golden Corridor ($250k) lolos
        ok2, _, cfg2 = classify_entry_corridor(250_000.0)
        self.assertTrue(ok2)
        self.assertEqual(cfg2["corridor_id"], 2)

        # Zona Merah $450k-$500k ($460k) diblokir
        ok_z3, _, _ = classify_entry_corridor(460_000.0)
        self.assertFalse(ok_z3)

        # Zona Pucuk > $650k ($820k) diblokir
        ok_z4, _, _ = classify_entry_corridor(820_000.0)
        self.assertFalse(ok_z4)

    def test_delay_projection_blocks_ceiling_collision(self):
        # Koin bagus di $250k lolos dengan proyeksi delay 22s aman
        pair_ok = _build_healthy_pair(mc=250_000.0, liq=36_000.0, p_m5=15.0)
        ok, _, metrics = evaluate_market_candidate(pair_ok, pair_count=1, boost_amount=100)
        self.assertTrue(ok)
        self.assertGreater(metrics["est_delay_drift_pct"], 0.0)

        # Koin mepet plafon Lorong 2 ($311k dengan M5 kencang +38%) ditolak karena saat 22s sudah masuk Zona Merah > $315k
        pair_late = _build_healthy_pair(mc=311_500.0, liq=45_000.0, p_m5=38.0)
        pair_late["volume"] = {"m5": 70_000.0, "h1": 440_000.0}
        ok_late, reason_late, _ = evaluate_market_candidate(pair_late, pair_count=1, boost_amount=100)
        self.assertFalse(ok_late)
        self.assertTrue("ANTI-TELAT" in reason_late or "pasca-delay" in reason_late)

    def test_anti_pucuk_candle_and_siphon_guards(self):
        # Parabolic candle M5 > +45% diblokir
        pair_parabolic = _build_healthy_pair(mc=245_000.0, p_m5=54.0)
        ok_p, reason_p, _ = evaluate_market_candidate(pair_parabolic)
        self.assertFalse(ok_p)
        self.assertIn("ANTI-PUCUK", reason_p)

        # Siphon Bot: rasio klik tinggi (>= 2.8x), volume besar, tapi M5 tertahan (< +8.5%)
        pair_siphon = _build_healthy_pair(mc=245_000.0, p_m5=7.2)
        pair_siphon["txns"]["m5"] = {"buys": 95, "sells": 20}
        pair_siphon["volume"]["m5"] = 8_500.0
        ok_s, reason_s, _ = evaluate_market_candidate(pair_siphon)
        self.assertFalse(ok_s)
        self.assertIn("Siphon Bot", reason_s)

    def test_onchain_contract_dev_balance_and_cabal_guards(self):
        # Kontrak bersih lolos
        clean_rug = _build_clean_rugcheck()
        ok, _, meta = analyze_holders_and_lp_lock(clean_rug, "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1")
        self.assertTrue(ok)
        self.assertEqual(meta["sol_backing_ratio"], 50.0)

        # Dev memegang > 2.5% ditolak
        dev_heavy = _build_clean_rugcheck(creator_pct=4.2)
        ok_dev, reason_dev, _ = analyze_holders_and_lp_lock(dev_heavy)
        self.assertFalse(ok_dev)
        self.assertIn("Dev masih memegang", reason_dev)

        # Single-sided LP palsu (SOL asli < 32%) ditolak
        fake_lp = _build_clean_rugcheck(sol_usd=4_000.0, token_usd=26_000.0)
        ok_lp, reason_lp, _ = analyze_holders_and_lp_lock(fake_lp)
        self.assertFalse(ok_lp)
        self.assertIn("Single-Sided LP Palsu", reason_lp)

        # Micro-Split CEX Syndicate (5 dompet identik ~0.88%) ditolak
        sybil_rug = _build_clean_rugcheck()
        sybil_rug["topHolders"].extend(
            [{"address": f"SybilWallet{i}11111111111111111111111111", "pct": 0.88 + (i * 0.01), "insider": False} for i in range(5)]
        )
        ok_syb, reason_syb, _ = analyze_holders_and_lp_lock(sybil_rug)
        self.assertFalse(ok_syb)
        self.assertIn("Micro-Split CEX Syndicate", reason_syb)

    def test_markdown_sanitizer_and_smart_money_engine(self):
        self.assertEqual(_esc_md("Pepe_2.0*[AI]`Test`"), "Pepe2.0AITest")

        test_cfg = FomoConfig(
            JUPITER_ONLY=False,
            ENFORCE_ONCHAIN_SAFETY=False,
            DATABASE_PATH="/tmp/test_corridor_unit_ledger.db",
        )
        eng = SmartMoneyEngine(test_cfg)
        top_wallet = next(iter(TOP_100_WALLETS.keys()))
        ev_runner = TradeEvent(
            wallet=top_wallet,
            token_mint="RunnerMint111111111111111111111111111111111",
            token_symbol="PEAK",
            amount_usd=3500.0,
            token_amount=100000.0,
            entry_price_usd=0.05,
            current_price_usd=0.05,
            market_cap_usd=5_000_000.0,
            price_drift_pct=2.0,
            signature=f"unit-sig-{int(time.time()*1000)}",
        )
        eligible, reason, _ = eng.evaluate_trade(ev_runner)
        self.assertFalse(eligible)
        self.assertIn("RUNNER", reason.upper())


if __name__ == "__main__":
    unittest.main()
