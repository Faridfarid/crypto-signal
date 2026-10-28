"""Sinyal harian MACD 8/21/9 + SMA50 untuk SOL, ETH, BTC + buku paper trading.

Aturan (hasil backtest):
  BELI  : MACD(8,21) > Signal(9) DAN close > SMA50   (dicek di close harian, 00:00 UTC = 07:00 WIB)
  JUAL  : MACD memotong ke bawah Signal
  Eksekusi paper: harga OPEN candle harian berikutnya (07:00 WIB), biaya 0,15% per sisi.
Dijalankan GitHub Actions setiap hari ~07:05 WIB. Hanya membaca data publik; tidak ada transaksi nyata.
"""
import json, os, sys, time, datetime as dt, urllib.request

COINS = {"SOL": ("SOL-USD", "SOLUSD"), "ETH": ("ETH-USD", "ETHUSD"), "BTC": ("BTC-USD", "XBTUSD")}
COST = 0.0015
START_CASH = 100.0
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

def main():
    os.makedirs("state", exist_ok=True); os.makedirs("signals", exist_ok=True)
    today0 = int(time.time()) // DAY * DAY            # 00:00 UTC hari ini = 07:00 WIB
    st = json.load(open(STATE)) if os.path.exists(STATE) else {"start": None, "coins": {}}
    new_trades = []; report = {"generated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "coins": {}, "errors": []}
    for coin in COINS:
        try:
            bars, src = fetch(coin)
        except Exception as e:
            report["errors"].append(str(e)); continue
        done = [b for b in bars if b[0] < today0]                 # candle yang sudah tutup
        cur_bar = next((b for b in bars if b[0] == today0), None)  # candle hari ini (untuk harga open eksekusi)
        closes = [b[4] for b in done]
        pos, m, sg, sma = positions(closes)
        n = len(done)-1
        last_close_day = day(done[n][0])                          # tanggal candle yang baru tutup (UTC)
        c = st["coins"].setdefault(coin, {"cash": START_CASH, "units": 0.0, "last_day": None, "bh_units": None, "entry": None})
        if st["start"] is None: st["start"] = last_close_day
        # proses setiap hari yang belum diproses (jika ada run yang terlewat)
        idx = {day(b[0]): i for i, b in enumerate(done)}
        # run pertama: mulai dari kas, hanya sinyal BELI baru yang dieksekusi (posisi lama tidak dikejar)
        start_i = idx[c["last_day"]]+1 if c["last_day"] in idx else n
        opens = {b[0]: b[1] for b in bars}
        for i in range(start_i, n+1):
            exec_t = done[i][0] + DAY; px = opens.get(exec_t)
            if not px: continue
            if c["bh_units"] is None: c["bh_units"] = START_CASH/px*(1-COST); c["bh_start_px"] = px
            if pos[i] == 1 and pos[i-1] == 0 and c["units"] == 0:
                c["units"] = c["cash"]*(1-COST)/px; c["cash"] = 0.0; c["entry"] = px
                new_trades.append([day(exec_t), coin, "BELI", round(px, 4), round(c["units"], 6)])
            elif pos[i] == 0 and c["units"] > 0:
                c["cash"] = c["units"]*px*(1-COST); new_trades.append([day(exec_t), coin, "JUAL", round(px, 4), round(c["units"], 6)])
                c["units"] = 0.0; c["entry"] = None
            c["last_day"] = day(done[i][0])
        action = "BELI" if pos[n] == 1 and pos[n-1] == 0 else "JUAL" if pos[n] == 0 and pos[n-1] == 1 else ("TAHAN" if pos[n] == 1 else "FLAT")
        mark = cur_bar[1] if cur_bar else closes[n]
        equity = c["cash"] + c["units"]*mark
        gap = m[n]-sg[n]; dist = closes[n]/sma[n]-1
        # siaga: masih FLAT tapi harga <3% di bawah (atau sudah di atas) SMA50 dan MACD sudah di atas signal / sedang menyempit
        siaga = pos[n] == 0 and dist > -0.03 and (gap > 0 or gap > m[n-1]-sg[n-1])
        last_flip = n
        while last_flip > 0 and pos[last_flip-1] == pos[n]: last_flip -= 1
        report["coins"][coin] = {
            "source": src, "candle_closed": last_close_day, "action": action, "position": "LONG" if pos[n] else "FLAT",
            "state_since": day(done[last_flip][0]), "close": closes[n], "exec_open_today": cur_bar[1] if cur_bar else None,
            "macd": round(m[n], 6), "signal": round(sg[n], 6), "macd_gap": round(gap, 6), "macd_gap_prev": round(m[n-1]-sg[n-1], 6),
            "sma50": round(sma[n], 6), "dist_sma50_pct": round(dist*100, 2), "siaga": bool(siaga),
            "paper_equity": round(equity, 2), "paper_units": c["units"], "paper_entry": c["entry"],
            "buyhold_equity": round(c["bh_units"]*mark, 2) if c["bh_units"] else None}
    st["updated"] = report["generated_utc"]
    tot = [v["paper_equity"] for v in report["coins"].values()]; bh = [v["buyhold_equity"] for v in report["coins"].values() if v["buyhold_equity"]]
    report["paper_start"] = st["start"]; report["paper_total"] = round(sum(tot), 2) if tot else None
    report["buyhold_total"] = round(sum(bh), 2) if len(bh) == len(COINS) else None
    report["new_trades"] = new_trades
    json.dump(st, open(STATE, "w"), indent=1)
    if new_trades:
        hdr = not os.path.exists(TRADES)
        with open(TRADES, "a") as f:
            if hdr: f.write("tanggal_eksekusi_utc,koin,aksi,harga_usd,unit\n")
            for t in new_trades: f.write(",".join(map(str, t)) + "\n")
    json.dump(report, open("signals/latest.json", "w"), indent=1)
    print(json.dumps(report, indent=1))
    if report["errors"] and not report["coins"]: sys.exit(1)

if __name__ == "__main__":
    main()
