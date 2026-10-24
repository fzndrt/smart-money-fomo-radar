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

        # Micro-Split CEX Syndicate (5 dompet kloningan identik ~0.88% selisih <= 0.03%) ditolak
        sybil_rug = _build_clean_rugcheck()
        sybil_rug["topHolders"].extend(
            [{"address": f"SybilWallet{i}11111111111111111111111111", "pct": 0.880 + (i * 0.005), "insider": False} for i in range(5)]
        )
        ok_syb, reason_syb, _ = analyze_holders_and_lp_lock(sybil_rug)
        self.assertFalse(ok_syb)
        self.assertIn("Micro-Split CEX Syndicate", reason_syb)

        # Token-2022 PumpSwap modern dengan field null/false + pool WBTC + vault owner WAJIB LOLOS (tidak boleh false-positive!)
        pumpswap_rug = _build_clean_rugcheck()
        pumpswap_rug["token_extensions"] = {
            "nonTransferable": False,
            "transferFeeConfig": None,
            "permanentDelegate": None,
            "transferHook": None,
            "pausableConfig": None,
        }
        pumpswap_rug["markets"][0]["lp"]["quoteMint"] = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"  # WBTC Wormhole
        pumpswap_rug["topHolders"][0] = {
            "address": "VaultTokenAccount11111111111111111111111111",
            "owner": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1",
            "pct": 14.5,
            "insider": False,
        }
        ok_ps, reason_ps, _ = analyze_holders_and_lp_lock(pumpswap_rug, "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1")
        self.assertTrue(ok_ps, msg=reason_ps)

        # Tetapi jika PermanentDelegate benar-benar aktif, WAJIB DIBLOKIR!
        malicious_t22 = _build_clean_rugcheck()
        malicious_t22["token_extensions"] = {
            "nonTransferable": False,
            "permanentDelegate": {"delegate": "ScamDev111111111111111111111111111111111"},
            "transferHook": None,
        }
        ok_mal, reason_mal, _ = analyze_holders_and_lp_lock(malicious_t22)
        self.assertFalse(ok_mal)
        self.assertIn("PermanentDelegate", reason_mal)

        # Kurva Pareto ritel organik ($REMUS: 1.75%, 1.71%, 1.69%, 1.67%, 1.66%, 1.59%, 1.48%...) WAJIB LOLOS!
        remus_rug = _build_clean_rugcheck(creator_pct=0.0, sol_usd=32_000.0, token_usd=32_000.0)
        remus_rug["totalHolders"] = 6678
        remus_pcts = [1.7524, 1.7108, 1.7103, 1.6943, 1.6767, 1.6609, 1.5920, 1.4867, 1.4740, 1.4270]
        remus_rug["topHolders"] = [
            {"address": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1", "pct": 6.27, "insider": False},
            *[
                {"address": f"OrganicRetail{i:02d}1111111111111111111111111", "pct": p, "insider": False}
                for i, p in enumerate(remus_pcts)
            ],
        ]
        ok_remus, reason_remus, _ = analyze_holders_and_lp_lock(
            remus_rug,
            "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1",
            mc_usd=531_000.0,
            age_minutes=870.0,
        )
        self.assertTrue(ok_remus, msg=reason_remus)

        # Sebaliknya, Cloned Mid-Tier Cabal (4 dompet identik ~1.800% selisih <= 0.015%) WAJIB DIBLOKIR!
        cloned_mid_rug = _build_clean_rugcheck()
        cloned_mid_rug["topHolders"] = [
            {"address": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1", "pct": 18.0, "insider": False},
            *[
                {"address": f"ClonedMid{i}1111111111111111111111111111", "pct": 1.800 + (i * 0.004), "insider": False}
                for i in range(4)
            ],
        ]
        ok_cm, reason_cm, _ = analyze_holders_and_lp_lock(cloned_mid_rug, "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1")
        self.assertFalse(ok_cm)
        self.assertIn("Cabal Cluster", reason_cm)

    def test_v24_leonida_nano_swarm_and_h1_botnet_blocked(self):
        # 1. Forensik $Leonida (3WUf...pump) Stage 2: Pool 11.55%, 88.45% suplai dipecah ke >3.300 bot nano (<0.22%)
        leonida_rug = _build_clean_rugcheck(creator_pct=0.0, sol_usd=17_200.0, token_usd=17_200.0)
        leonida_rug["totalHolders"] = 3395
        leonida_rug["topHolders"] = [
            {"address": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1", "pct": 11.55, "insider": False},
            *[
                {"address": f"NanoBot{i:02d}11111111111111111111111111111", "pct": round(0.22 - (i * 0.006), 4), "insider": False}
                for i in range(19)
            ],
        ]
        ok_nano, reason_nano, _ = analyze_holders_and_lp_lock(
            leonida_rug,
            "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1",
            mc_usd=148_000.0,
            age_minutes=55.0,
        )
        self.assertFalse(ok_nano)
        self.assertIn("Nano-Swarm", reason_nano)

        # 2. Anti-Decoy Nano-Swarm: Dev pasang 2 dompet umpan (2.4% & 1.9%) tapi Holder #5 ke bawah jeblok <0.45%
        decoy_rug = _build_clean_rugcheck(creator_pct=0.0, sol_usd=17_200.0, token_usd=17_200.0)
        decoy_pcts = [2.40, 1.95, 1.30, 0.85, 0.28, 0.25, 0.22, 0.20, 0.19, 0.18, 0.17, 0.16]
        decoy_rug["topHolders"] = [
            {"address": "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1", "pct": 12.0, "insider": False},
            *[
                {"address": f"DecoyOrNano{i:02d}111111111111111111111111", "pct": p, "insider": False}
                for i, p in enumerate(decoy_pcts)
            ],
        ]
        ok_decoy, reason_decoy, _ = analyze_holders_and_lp_lock(
            decoy_rug,
            "5q544fkrfoe6tsebd7s8emxgtjyakttvhaw5q5pge4j1",
            mc_usd=150_000.0,
            age_minutes=50.0,
        )
        self.assertFalse(ok_decoy)
        self.assertIn("Decoy Nano-Swarm", reason_decoy)

        # 3. Forensik $Leonida Stage 1: M5 disuntik tampak rapi ($25k / 800 tx = $31), tapi H1 bocor 13.468 tx receh ($14.6 tiket)!
        pair_leonida = _build_healthy_pair(mc=148_000.0, liq=34_000.0, p_m5=12.0, age_mins=55.0)
        pair_leonida["txns"] = {
            "m5": {"buys": 560, "sells": 240},
            "h1": {"buys": 8688, "sells": 4780},
            "h24": {"buys": 16787, "sells": 4782},
        }
        pair_leonida["volume"] = {"m5": 25_500.0, "h1": 197_000.0}
        ok_l1, reason_l1, _ = evaluate_market_candidate(pair_leonida, pair_count=1, boost_amount=30)
        self.assertFalse(ok_l1)
        self.assertIn("Botnet", reason_l1)

        # 4. Anti-Churn Distribution Trap: Volume 1h raksasa (2.1x MC) tapi harga 1 jam jalan di tempat (+9.5%)
        pair_churn = _build_healthy_pair(mc=150_000.0, liq=32_000.0, p_m5=10.5, age_mins=48.0)
        pair_churn["priceChange"] = {"m5": 10.5, "h1": 9.5, "h6": 65.0}
        pair_churn["volume"] = {"m5": 32_000.0, "h1": 315_000.0}
        ok_ch, reason_ch, _ = evaluate_market_candidate(pair_churn, pair_count=1, boost_amount=100)
        self.assertFalse(ok_ch)
        self.assertIn("Churn Bandar", reason_ch)

    def test_proven_steady_climber_passes_corridor_3(self):
        # Simulasi profil organik $REMUS saat breakout Lorong 3 ($531k MC, usia 14.5 jam, 41k tx, 139B/94S, M5 +8.5%)
        pair_remus = _build_healthy_pair(mc=531_000.0, liq=64_400.0, p_m5=8.5, age_mins=870.0)
        pair_remus["priceChange"] = {"m5": 8.5, "h1": 37.6, "h6": 120.0}
        pair_remus["txns"] = {
            "m5": {"buys": 139, "sells": 94},
            "h1": {"buys": 1081, "sells": 792},
            "h24": {"buys": 27737, "sells": 13470},
        }
        pair_remus["volume"] = {"m5": 6_643.0, "h1": 58_831.0}
        ok_sc, reason_sc, metrics_sc = evaluate_market_candidate(pair_remus, pair_count=1, boost_amount=100)
        self.assertTrue(ok_sc, msg=reason_sc)
        self.assertEqual(metrics_sc["corridor_id"], 3)

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
