"""Sinyal harian MACD 8/21/9 + SMA50 untuk SOL, ETH, BTC + buku paper trading.

Aturan (hasil backtest):
  BELI  : MACD(8,21) > Signal(9) DAN close > SMA50   (dicek di close harian, 00:00 UTC = 07:00 WIB)
  JUAL  : MACD memotong ke bawah Signal
  Eksekusi paper: harga OPEN candle harian berikutnya (07:00 WIB), biaya 0,15% per sisi.
  Porsi: satu dompet kas bersama. Saat BELI, beli jatah target (SOL 50%, BTC 25%, ETH 25%) dari total nilai
  portofolio, maksimal sebesar kas yang ada. Saat JUAL, seluruh posisi koin itu dijual dan kembali ke kas.
Dijalankan GitHub Actions setiap hari ~07:05 WIB. Hanya membaca data publik; tidak ada transaksi nyata.
"""
import json, os, sys, time, datetime as dt, urllib.request

COINS = {"SOL": ("SOL-USD", "SOLUSD"), "ETH": ("ETH-USD", "ETHUSD"), "BTC": ("BTC-USD", "XBTUSD")}
COST = 0.0015
TOTAL_CASH = 300.0                                   # modal virtual total
WEIGHTS = {"SOL": 0.50, "BTC": 0.25, "ETH": 0.25}    # target porsi saat sinyal BELI (dari total nilai portofolio)
STATE = "state/paper.json"
TRADES = "state/trades.csv"
DAY = 86400

def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-signal/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def fetch_coinbase(prod, days=320):
    now = int(time.time()); out = {}
    end = now
    while len(out) < days:
        start = end - 299*DAY
        u = (f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=86400"
             f"&start={dt.datetime.utcfromtimestamp(start).isoformat()}Z&end={dt.datetime.utcfromtimestamp(end).isoformat()}Z")
        rows = http_json(u)
        if not rows: break
        for t, lo, hi, op, cl, v in rows: out[t] = (op, hi, lo, cl)
        end = start - DAY
    return sorted((t, *v) for t, v in out.items())

def fetch_kraken(pair):
    j = http_json(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440")
    if j.get("error"): raise RuntimeError(j["error"])
    rows = next(v for k, v in j["result"].items() if k != "last")
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]

def fetch(coin):
    prod, kpair = COINS[coin]
    for name, fn in (("Coinbase", lambda: fetch_coinbase(prod)), ("Kraken", lambda: fetch_kraken(kpair))):
        for attempt in range(3):
            try:
                bars = fn()
                if len(bars) >= 120: return bars, name
            except Exception as e:
                print(f"{coin} {name} gagal (percobaan {attempt+1}): {e}", file=sys.stderr); time.sleep(5)
    raise RuntimeError(f"Data {coin} tidak bisa diambil dari Coinbase maupun Kraken")

def ema(x, n):
    a = 2/(n+1); o = [x[0]]
    for v in x[1:]: o.append(a*v + (1-a)*o[-1])
    return o

def positions(closes):
    m = [a-b for a, b in zip(ema(closes, 8), ema(closes, 21))]; sg = ema(m, 9)
    sma = [None]*len(closes)
    for i in range(49, len(closes)): sma[i] = sum(closes[i-49:i+1])/50
    pos = [0]*len(closes); cur = 0
    for i in range(30, len(closes)):
        if cur == 0 and m[i] > sg[i] and sma[i] is not None and closes[i] > sma[i]: cur = 1
        elif cur == 1 and m[i] < sg[i]: cur = 0
        pos[i] = cur
    return pos, m, sg, sma

def day(t): return dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")

def migrate(st):
    """State versi 1 (modal $100 per koin) -> versi 2 (dompet kas bersama, target 50/25/25)."""
    if st.get("version") == 2: return st
    old = st.get("coins", {})
    new = {"version": 2, "start": st.get("start"), "start_cash": TOTAL_CASH, "weights": WEIGHTS, "last_day": None,
           "cash": round(sum(c.get("cash", 0.0) for c in old.values()), 6) if old else TOTAL_CASH, "coins": {}}
    days = [c.get("last_day") for c in old.values() if c.get("last_day")]
    new["last_day"] = min(days) if days else None
    for coin in COINS:
        c = old.get(coin, {}); px0 = c.get("bh_start_px")
        new["coins"][coin] = {"units": c.get("units", 0.0), "entry": c.get("entry"), "bh_start_px": px0,
                              "bh_units": TOTAL_CASH*WEIGHTS[coin]*(1-COST)/px0 if px0 else None}
    return new

def main():
    os.makedirs("state", exist_ok=True); os.makedirs("signals", exist_ok=True)
    today0 = int(time.time()) // DAY * DAY            # 00:00 UTC hari ini = 07:00 WIB
    st = migrate(json.load(open(STATE))) if os.path.exists(STATE) else migrate({})
    new_trades = []; report = {"generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "weights": WEIGHTS, "coins": {}, "errors": []}
    D = {}
    for coin in COINS:
        try:
            bars, src = fetch(coin)
        except Exception as e:
            report["errors"].append(str(e)); continue
        done = [b for b in bars if b[0] < today0]                  # candle yang sudah tutup
        closes = [b[4] for b in done]; pos, m, sg, sma = positions(closes)
        D[coin] = {"src": src, "done": done, "closes": closes, "pos": pos, "m": m, "sg": sg, "sma": sma,
                   "idx": {day(b[0]): i for i, b in enumerate(done)}, "opens": {b[0]: b[1] for b in bars},
                   "cur": next((b for b in bars if b[0] == today0), None)}
    # buku paper trading hanya diperbarui bila data ketiga koin lengkap (agar tidak ada sinyal yang terlewat)
    if len(D) == len(COINS):
        last_common = min(day(d["done"][-1][0]) for d in D.values())
        if st["start"] is None: st["start"] = last_common
        all_days = sorted(set.intersection(*[set(d["idx"]) for d in D.values()]))
        todo = [x for x in all_days if (st["last_day"] is None and x == last_common) or (st["last_day"] is not None and st["last_day"] < x <= last_common)]
        for dd in todo:
            exec_t = int(dt.datetime.strptime(dd, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) + DAY
            px = {c: D[c]["opens"].get(exec_t) for c in COINS}
            if any(v is None for v in px.values()): break
            for c in COINS:
                k = st["coins"][c]
                if k["bh_units"] is None: k["bh_start_px"] = px[c]; k["bh_units"] = TOTAL_CASH*WEIGHTS[c]*(1-COST)/px[c]
            sig = {c: (D[c]["pos"][D[c]["idx"][dd]], D[c]["pos"][D[c]["idx"][dd]-1]) for c in COINS}
            for c in COINS:                                         # JUAL dulu: seluruh posisi, uang kembali ke kas bersama
                k = st["coins"][c]
                if sig[c][0] == 0 and k["units"] > 0:
                    st["cash"] += k["units"]*px[c]*(1-COST)
                    new_trades.append([day(exec_t), c, "JUAL", round(px[c], 4), round(k["units"], 6), round(k["units"]*px[c]*(1-COST), 2)])
                    k["units"] = 0.0; k["entry"] = None
            equity = st["cash"] + sum(st["coins"][c]["units"]*px[c] for c in COINS)
            for c in sorted(COINS, key=lambda x: -WEIGHTS[x]):     # BELI: jatah target dari total nilai portofolio
                k = st["coins"][c]
                if sig[c][0] == 1 and sig[c][1] == 0 and k["units"] == 0 and st["cash"] > 0.01:
                    amt = min(WEIGHTS[c]*equity, st["cash"]); k["units"] = amt*(1-COST)/px[c]; k["entry"] = px[c]; st["cash"] -= amt
                    new_trades.append([day(exec_t), c, "BELI", round(px[c], 4), round(k["units"], 6), round(amt, 2)])
            st["last_day"] = dd
    total = st["cash"]; bh_total = 0.0; bh_ok = True
    for coin, d in D.items():
        pos, m, sg, sma, closes, done = d["pos"], d["m"], d["sg"], d["sma"], d["closes"], d["done"]; n = len(done)-1
        k = st["coins"][coin]; mark = d["cur"][1] if d["cur"] else closes[n]
        action = "BELI" if pos[n] == 1 and pos[n-1] == 0 else "JUAL" if pos[n] == 0 and pos[n-1] == 1 else ("TAHAN" if pos[n] == 1 else "FLAT")
        gap = m[n]-sg[n]; dist = closes[n]/sma[n]-1
        # siaga: masih FLAT tapi harga <3% di bawah (atau sudah di atas) SMA50 dan MACD sudah di atas signal / sedang menyempit
        siaga = pos[n] == 0 and dist > -0.03 and (gap > 0 or gap > m[n-1]-sg[n-1])
        last_flip = n
        while last_flip > 0 and pos[last_flip-1] == pos[n]: last_flip -= 1
        val = k["units"]*mark; total += val
        if k["bh_units"]: bh_total += k["bh_units"]*mark
        else: bh_ok = False
        report["coins"][coin] = {
            "source": d["src"], "candle_closed": day(done[n][0]), "action": action, "position": "LONG" if pos[n] else "FLAT",
            "state_since": day(done[last_flip][0]), "close": closes[n], "exec_open_today": d["cur"][1] if d["cur"] else None,
            "macd": round(m[n], 6), "signal": round(sg[n], 6), "macd_gap": round(gap, 6), "macd_gap_prev": round(m[n-1]-sg[n-1], 6),
            "sma50": round(sma[n], 6), "dist_sma50_pct": round(dist*100, 2), "siaga": bool(siaga), "target_weight": WEIGHTS[coin],
            "paper_holding": k["units"] > 0, "paper_value": round(val, 2), "paper_units": k["units"], "paper_entry": k["entry"]}
    st["updated"] = report["generated_utc"]
    report["paper_start"] = st["start"]; report["paper_cash"] = round(st["cash"], 2)
    report["paper_total"] = round(total, 2) if len(D) == len(COINS) else None
    report["buyhold_total"] = round(bh_total, 2) if bh_ok and len(D) == len(COINS) else None
    report["new_trades"] = new_trades
    json.dump(st, open(STATE, "w"), indent=1)
    if new_trades:
        hdr = not os.path.exists(TRADES)
        with open(TRADES, "a") as f:
            if hdr: f.write("tanggal_eksekusi_utc,koin,aksi,harga_usd,unit,nilai_usd\n")
            for t in new_trades: f.write(",".join(map(str, t)) + "\n")
    json.dump(report, open("signals/latest.json", "w"), indent=1)
    print(json.dumps(report, indent=1))
    if report["errors"] and not report["coins"]: sys.exit(1)

if __name__ == "__main__":
    main()
