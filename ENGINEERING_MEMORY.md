# ENGINEERING MEMORY & FORENSIC LEDGER — PumpAlpha Corridor Sniper V2.5

Dokumen ini adalah **memori permanen anti-regresi** untuk seluruh hasil investigasi on-chain, bedah forensik koin nyata, kalibrasi kecepatan eksekusi, dan evolusi arsitektur **Corridor Sniper (`/api/fomo.py`)**.

> **ATURAN WAJIB UNTUK SETIAP PEMBARUAN KE DEPAN:**
> Setiap kali menambah filter untuk modus dev baru, **DILARANG KERAS** merusak kalibrasi koin organik yang sudah pernah dibedah dan disahkan di bawah ini. Seluruh aturan wajib lolos uji regresi `npm run test:py` (`tests/test_corridor_sniper_v2.py`).

---

## 1. Kronologi Bedah Kasus Koin & Keputusan Arsitektur

### Kasus #1 & #2 — Token Jaringan Non-Solana (`0x...`)
- **Token yang Diperiksa:**
  1. `0xb11384a6ac2b3708e3d25a834c8a88687accbcd1` (`$CREORA` — Robinhood Chain / Uniswap EVM)
  2. `0x94641b97010608c3827fb058074889f19868ff33` (`$VRAX` — Robinhood Chain / Uniswap EVM)
- **Temuan Forensik:**
  Kedua kontrak berawalan `0x` (42 karakter heksadesimal EVM) dan diperdagangkan di jaringan EVM/Robinhood (`uniswap`), bukan Solana SPL/Token-2022.
- **Keputusan Permanen:**
  Corridor Sniper dikhususkan secara eksklusif untuk ekosistem **Solana (`chainId == "solana"`)** karena mengandalkan simulasi rute swap real-time **Jupiter V1 Lite (`So111...`)** dan audit kontrak **RugCheck Solana**. Token `0x...` memang sengaja tidak dipindai oleh radar Solana.

---

### Kasus #3 — `$CHOO` (`EJAxERvKU4kEVXn3NarjvRtgaMaJyP9ZuMeKA7JJpump`)
- **Konteks Masalah:**
  1. Mengapa koin PumpSwap modern sempat tertahan oleh audit kontrak/pool lama?
  2. Mengapa tidak terdeteksi pada tanggal 12 padahal terbang tinggi pada tanggal 15?
- **Temuan Forensik & Solusi Permanen:**
  1. **Standar Kontrak Token-2022 PumpSwap Modern:** Koin kelulusan Pump.fun / PumpSwap modern menggunakan program `Token-2022` di mana RugCheck mengembalikan objek `token_extensions` berisi kunci dengan nilai `null` atau `false` (`permanentDelegate: null`, `transferHook: null`, `nonTransferable: false`). Filter versi awal sempat terlalu kaku membaca keberadaan key.
     - **Solusi:** `analyze_holders_and_lp_lock()` kini membedakan field `null/false` (AMAN & WAJIB LOLOS) vs ekstensi berbahaya yang benar-benar aktif (`PermanentDelegate`, `TransferHook`, `NonTransferable`, atau `transferFee > 0%` -> BLOKIR MUTLAK).
  2. **Identifikasi Vault Pool (`owner`) & Pasangan Aset Keras (`HARD_QUOTE_MINTS`):** Pada PumpSwap/Meteora, akun token terbesar sering berupa Token Vault milik kontrak Pair (`h.owner == pair_address`), dan beberapa pool berpasangan dengan aset keras resmi seperti **WBTC (`3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh`), cbBTC, USD1, USDC, atau USDT**.
     - **Solusi:** `owner` vault dipisahkan otomatis sebagai `pool_holdings` (bukan paus tunggal), dan `HARD_QUOTE_MINTS` mencakup SOL, USDC, USDT, WBTC, cbBTC, serta USD1.
  3. **Logika Jendela Waktu (Tanggal 12 vs Tanggal 15):** Corridor Sniper adalah **Real-Time Momentum Breakout Engine**, bukan penyimpan koin tidur berhari-hari (membeli koin datar tanpa volume di tanggal 12 akan mengunci modal pada ribuan koin mati). Namun saat koin matang organik mulai bangun kembali dengan likuiditas kuat, batas usia aktif diperluas hingga **36 jam (`2160 menit`)** apabila `cum_tx_24h >= 5.000` dan `liquidity >= $30.000`.

---

### Kasus #4 — `$REMUS` (`CA8q2mkYWZnUNqYmf6pxyxAPZsmSe2EG9P7hHMW5pump`)
- **Konteks Masalah:**
  Koin komunitas ritel organik (`6.678` holders, likuiditas tebal `>$64k`, naik sehat menembus Lorong 3 `$531k -> $700k+`) sempat tertahan oleh detektor Cabal menengah dan rasio M5 yang ketat.
- **Temuan Forensik & Solusi Permanen:**
  1. **Kurva Pareto Ritel Organik vs Dompet Kloningan Cabal:** Pada koin matang dengan ribuan holder seperti `$REMUS`, kepemilikan Top 10 pure holders menurun alami secara rapat (`1.75%, 1.71%, 1.71%, 1.69%, 1.67%, 1.66%, 1.59%, 1.48%...`). Toleransi kluster lama yang terlalu lebar mengira barisan alami ini sebagai dompet split dev.
     - **Solusi:** Detektor Cabal menengah dikalibrasi presisi (`>= 4` dompet kembar identik dengan selisih `<= 0.015%`), sedangkan `Anti-Randomized Bundler` dikunci khusus pada koin muda (`age < 35 menit` & `totalHolders < 450`). Hasilnya: **`$REMUS` 100% LOLOS**, sementara kloningan Cabal dev tetap tertangkap!
  2. **Kalibrasi `is_proven_climber` & Bot Arbitrase MEV di Lorong Besar:** Pada MC `>$500k` dengan ribuan transaksi, setiap candle hijau selalu diiringi transaksi jual-beli bot arbitrase MEV sehingga rasio M5 berkisar `1.4x–1.6x` (mis. `139 Buy / 94 Sell = 1.48x`, `M5 +8.5%`, `turnover_h1 ~0.11x`).
     - **Solusi:** Jalur `is_proven_climber` (`age >= 45m`, `cum_tx_24h >= max(2500, 2 * min_cum_tx)`, `H1 >= +15%`, `H6 >= +20%`) + `high_tx_organic_flow` menyesuaikan lantai `min_bs_m5 = 1.35x`, `min_cvd = 15%`, dan `mature_turnover_h1` per lorong.

---

### Kasus #5 — Verifikasi Repo GitHub & Keandalan Pengiriman Alert Telegram
- **Konteks Masalah:**
  Memastikan repo `https://github.com/fzndrt/smart-money-fomo-radar/tree/main` benar-benar mengirim sinyal ke Telegram tanpa gagal silent.
- **Solusi Permanen:**
  1. **Sanitasi Markdown `_esc_md()` + Auto-Fallback Plain-Text:** Karakter spesial pada nama/simbol token (`_`, `*`, `` ` ``, `[`, `]`) dibersihkan sebelum dikirim ke `sendMessage`, ditambah retry otomatis tanpa `parse_mode` bila API Telegram mengembalikan HTTP 400 `"can't parse entities"`.
  2. **Per-Corridor Cooldown Ledger (`/tmp/corridor_alerted_ledger.json`):** Kunci dedup disimpan dalam format `mint:C{corridor_id}` (TTL 45 menit). Koin yang sudah memberi sinyal di Lorong 0 (`$50k`) tidak akan spam di lorong yang sama, tetapi **tetap bisa memberi sinyal baru** jika berhasil breakout sehat ke Lorong 2 (`$250k`).

---

### Kasus #6 — `$Leonida` (`3WUfWQADbhWwogJk7Y37o5PUUVEy7KK1xsxB3YTvpump`) -> Upgrade Corridor Sniper V2.4
- **Konteks Masalah:**
  `$Leonida` sempat masuk alert karena tampak "bersih" di metrik klasik (`Dev 0%`, `Top 10 Pure Holders` sangat kecil, rasio M5 bagus), tetapi kemudian **mati total ke MC `$2k`** akibat rug distribusi massal.
- **Temuan Forensik (Modus "Nano-Swarm + H1 Bump Botnet"):**
  1. **Celah Buta Top-20 Klasik:** Pool AMM hanya memegang `11.55%` suplai, artinya **`88.45%` suplai beredar di luar pool**. Namun `Top 10 Pure Holders` masing-masing hanya memegang `< 0.22%`! Dev mencacah suplai ke **`3.395` dompet bot nano** (`0.02%–0.22%` per dompet) agar lolos dari batas `Top 10 <= 19.5%`.
  2. **Manipulasi Jendela Sempit M5 vs Jejak H1:** Dev menyuntik volume 5 menit terakhir (`$25.5k / 800 tx = ~$31/tx`) agar lolos filter tiket M5, padahal data 1 jam membocorkan **`13.468` transaksi spam botnet** dengan rata-rata tiket receh **`$14.6/tx`**!
- **7 Perisai Baru Corridor Sniper V2.4 (Kecepatan Tetap Utama — `0ms` Penalti Latensi!):**
  1. **Hukum Konservasi Pareto (Anti-Nano-Swarm Floor):** Jika `outside_pool_pct >= 65%` namun `top10_pure_pct < 5.2%` atau `max_single_pure < 0.65%`, blokir mutlak! Secara matematis mustahil koin organik menyebarkan `>65%` suplai tanpa satu pun holder mencapai `0.65%`.
  2. **Anti-Decoy Whale Slope Check (Rank-#5 Floor):** Menutup akal-akalan dev berikutnya jika mereka sengaja memasang 1–3 dompet umpan (`~2%`) di peringkat atas namun mencacah sisanya ke ribuan bot: bila `outside_pool_pct >= 70%` dan `Holder #5 < 0.45%` (atau `Top 10 < 6.0%`), langsung ditolak sebagai `Decoy Nano-Swarm`.
  3. **Pembunuh Botnet H1 Micro-Ticket & Hyper-Frequency:** Memeriksa jejak 1 jam (`total_h1 >= 250` wajib `avg_ticket_h1 >= max($16, 0.78 * effective_min_ticket)`, serta blokir frekuensi spam tidak manusiawi `total_h1 >= 1200` dengan `> 220 tx per $1k LP` dan tiket `< $35`).
  4. **Stress-Test Kedalaman Jual Paralel `0.1 SOL` + `1.0 SOL` (`0ms` Tambahan Waktu Tunggu):** `simulate_jupiter_roundtrip()` menembak kuotasi jual `0.1 SOL` dan `1.0 SOL` **secara paralel** di `ThreadPoolExecutor(max_workers=2)`. Membongkar jebakan *Hollow Bin* Meteora DLMM/CLMM (`0.1 SOL` lancar tapi pencairan `1.0 SOL` amblas `> 13.5%` atau gagal).
  5. **Anti-Siphon 1 Jam & Anti-Churn Sideways:** Memblokir distribusi senyap ketika klik Buy H1 timpang (`>= 2.20x` namun `p_h1 < +12%`) atau volume 1 jam berputar gila-gilaan (`turnover_h1 >= 1.65x MC` namun harga tertahan `< +14%`).
  6. **Komitmen Modal Berbayar (Hapus Diskon Sosmed Gratis):** Diskon ambang tiket `0.85x` hanya diberikan untuk komitmen berbiaya nyata (`DexScreener Boost >= 100`, multi-pool likuid `>= $35k`, atau `is_proven_climber`), bukan sekadar menempel link X/Telegram gratis.
  7. **Floor Jumlah Partisipan Nyata (`totalHolders`):** Menolak pump semu segelintir dompet (`< 45` holders di Lorong 0, `< 90` di Lorong 1, `< 150` di Lorong 2 & 3).

---

### Kasus #7 — `$Macca` (`AKotzk1dhcNangAJBtvNKtT9bhbSVZc2mMhRFgkFpump`) pada 21 Oktober -> Upgrade Corridor Sniper V2.5
- **Konteks Masalah:**
  Mengapa `Macca The Gator ($Macca)` tidak tertangkap pada **21 Oktober 2026** saat breakout dari konsolidasi `$340k–$412k` menembus **Lorong 3 (`$551k -> $595k`)** pada pukul `23:00–00:59 WIB` (`16:00–17:59 UTC`) sebelum meledak 3,1x ke **`$1,88 Juta` MC** (dengan `12.131` holders, `0%` saldo dev, `100%` LP locked, RugCheck Score `1`)?
- **4 Akar Penyebab Forensik On-Chain:**
  1. **Blind Spot Radar Discovery Koin Organik Tanpa Boost (`boosts = None`):** `$Macca` sama sekali tidak membeli iklan DexScreener Boost (`boosts = None`). Karena dibuat pada **15 Oktober 2026 (20:40 WIB)** dan berpasangan unik `Macca/TTWO` (saham RWA Take-Two Interactive / penerbit GTA VI di PumpSwap/Printr) + `Macca/SOL` di Meteora, pada 21 Oktober ia tidak muncul di `token-boosts`, tidak muncul di `token-profiles/latest` (hanya untuk profil baru), dan luput dari pencarian teks sempit `search?q=pumpswap`.
  2. **Tembok Usia Kaku `> 24j / 36j` pada Breakout Konsolidasi Multi-Hari (CTO):** Saat menembus Lorong 3 (`$551k` MC, volume `$100.990/jam`) pada 21 Okt 23:00 WIB, usia `$Macca` adalah **`146,8 jam` (`6,1 hari`)**. V2.4 memukul rata semua koin `> 36 jam` sebagai *"Koin lama: Rawan jebakan re-pump koin mati"*, padahal di Lorong 2 & 3 banyak *giant runner* lahir dari akumulasi sehat 3–10 hari.
  3. **Hukum Penyusutan Kurva AMM `x * y = k` pada `Top 10 Pure Holders` (`23,55% > 19,5%`) & Vault `knownAccounts`:** Di MC tinggi, token keluar dari pool ke masyarakat sehingga pool AMM `$Macca` hanya menyisakan `4,40%` (+ `3,54%` burned = **`92,06%` suplai beredar di luar pool**). Sepuluh holder murni teratas (`3,68%, 3,03%, 2,83%, 2,33%, 2,12%...1,68%`) menjumlah `23,55%` (hanya `25,5%` dari suplai beredar, tanpa paus `>3,7%`, `0` insider, `12.131` holders), namun terganjal angka statis `19,5%`. Selain itu RugCheck menyertakan `knownAccounts` (`AMM`, `LOCKER` seperti `Printr Stake Pool`, `CLOB`) yang wajib dipisahkan dari dompet ritel.
  4. **Proteksi Burst HTTP 429 pada `lite-api.jup.ag`:** Kuotasi paralel `0.1 SOL` + `1.0 SOL` saat banyak kandidat lolos bersamaan berpotensi memicu rate-limit sesaat (`HTTP 429`) yang bila tidak di-retry progresif bisa keliru dianggap gagal jual.
- **4 Kalibrasi Permanen Corridor Sniper V2.5 (`0ms` Tambahan Waktu Tunggu!):**
  1. **6-Feed Parallel Discovery (`GeckoTerminal Trending 5m & 1h` + `50` Kandidat Paralel):** Menarik `trending_pools?duration=5m` dan `duration=1h` (~65–85ms) secara paralel bersama feed DexScreener, lalu mem-batch hingga `50` mint unik dalam 2 chunk paralel (`~250ms` total). Koin organik murni tanpa Boost seperti `$Macca` langsung masuk radar!
  2. **Jalur Khusus Multi-Day CTO Breakout di Lorong 1, 2, & 3 (`> 1 Hari s/d 45 Hari` dengan Bukti Likuiditas & Komunitas):** Lorong 0 (`$25k–$78k`) **tetap dikunci ketat 24j–36j** (anti zombie micro-cap). Namun untuk koin berumur **di atas 1 hari hingga 45 hari** di **Lorong 1, 2, & 3 (`MC >= $128k`)** dengan `LP >= $28k/$40k/$48k`, `Vol H1 >= $25k–$35k`, rolling `tx24h` adaptif pasca-konsolidasi, `creator_pct <= 1.0%`, dan **`totalHolders >= 600` (Lorong 1) / `>= 800` (Lorong 2 & 3)**, koin otomatis dievaluasi penuh dengan perlakuan *mature supply rotation*.
  3. **Pool-Depletion-Aware Pareto Ceiling (`max 25.0%`) & `knownAccounts` Vault Filter:** Akun bertipe `AMM / LOCKER / CLOB / BURN / VAULT` pada `knownAccounts` otomatis dipisahkan sebagai pool/locker. Bila `outside_pool_pct >= 80%`, `totalHolders >= 1.000`, `creator_pct <= 1.0%`, `insider == 0`, dan `max_single_pure <= 4.0%`, plafon Top-10 disesuaikan proporsional `min(25.0%, outside_pool_pct * 0.275)`.
  4. **Adaptive Anti-429 Micro-Retry di `_fetch_jup_single_quote`:** Retry progresif hanya saat terjadi `HTTP 429`, menjaga latensi normal `0ms` sekaligus mencegah false-positive Honeypot.

---

## 2. Kontrak Invarian Anti-Regresi (Wajib Dijaga Selamanya)

Setiap modifikasi di masa depan **WAJIB** memastikan 8 invarian berikut tetap terpenuhi di `tests/test_corridor_sniper_v2.py`:

1. **Invarian Kecepatan Eksekusi (`< 1.5s – 2.5s` Batch Scan):** Dilarang menambah panggilan HTTP sekuensial lambat per kandidat. Stage 1 wajib 100% in-memory `O(1)` math; Stage 2 (RugCheck + Jupiter `0.1 SOL` & `1.0 SOL`) wajib dieksekusi paralel menggunakan `ThreadPoolExecutor`.
2. **Invarian Zona Merah Anti-Pucuk & Kompensasi Delay `22 Detik`:** Zona Merah (`$78k–$128k`, `$168k–$228k`, `$315k–$525k`, `>$650k`) dan batas `MAX SAFE ENTRY MC` pasca-delay 22 detik tidak boleh dihapus agar pengguna tidak pernah membeli di tembok take-profit bandar.
3. **Invarian PumpSwap Token-2022 & Hard Quote Assets (`$CHOO` Guard):** Field `token_extensions` bernilai `null/False`, vault pool via `owner`, dan pool quote `WBTC / cbBTC / USD1 / USDC / USDT` tidak boleh terkena false-positive blokir.
4. **Invarian Kurva Pareto Organik & Steady Climber (`$REMUS` Guard):** Koin komunitas matang dengan kurva holder menurun alami (`1.75%, 1.71%, 1.69%...`) dan profil `is_proven_climber` di Lorong 3 harus tetap lolos.
5. **Invarian Anti-Nano-Swarm & Anti-Decoy (`$Leonida` Guard):** Pencacahan suplai ke ratusan/ribuan dompet bot mikro (`< 0.25%`) baik murni maupun dengan 1–3 dompet umpan wajib selalu terblokir oleh Hukum Konservasi Pareto & Rank-#5 Floor.
6. **Invarian Stress-Test Likuiditas Dua Ukuran (`0.1 SOL` + `1.0 SOL`):** Koin yang tidak bisa dijual kembali (Honeypot) atau jeblok `> 13.5%` saat pencairan `1.0 SOL` (Hollow-Bin DLMM) wajib ditolak sebelum sinyal dikirim.
7. **Invarian Keandalan Telegram:** Sanitasi `_esc_md()` dan fallback plain-text pada `send_telegram_direct()` harus selalu aktif.
8. **Invarian Multi-Day CTO Breakout & Anti-Zombie (`$Macca` Guard):** Koin akumulasi komunitas di atas 1 hari (`> 24 jam s/d 45 hari`, `>= 600–800` holders, `dev <= 1%`, `LP >= $28k/$40k/$48k`, `Vol H1 >= $25k–$35k`) di Lorong 1–3 wajib lolos beserta skala deplesi pool `Top 10 <= 25%`, sementara zombie pump koin lama di Lorong 0 (`<$78k`) atau berkomunitas sempit (`< 600–800` holders) wajib tetap diblokir.
