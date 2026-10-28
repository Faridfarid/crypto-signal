"""Return bulanan, backtest histori panjang dengan parameter TERKUNCI (MACD 8/21/9 + SMA50, long-only, chart harian).
Parameter dipilih memakai data Jan 2024 - Jun 2025 (SOL). Semua data sebelum Okt 2023 belum pernah dipakai,
sehingga periode itu menjadi uji out-of-sample yang bersih.
Eksekusi: sinyal di close t, transaksi di open t+1, biaya 0,15% per sisi. Sumber: Coinbase Exchange API publik.
"""
import json, time, datetime as dt, urllib.request, itertools
import numpy as np, pandas as pd

COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "LTC-USD", "LINK-USD", "ADA-USD", "DOGE-USD", "AVAX-USD", "XRP-USD"]
COST = 0.0015; DAY = 86400
END = int(dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc).timestamp())
CUT = "2023-10-01"   # sebelum tanggal ini = data yang belum pernah dilihat

def get(url):
    for k in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research"}), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            time.sleep(2 + 2*k); err = e
    raise err

def fetch(prod):
    rows = {}; end = END; empty = 0
    while empty < 2:
        start = end - 299*DAY
        u = (f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=86400"
             f"&start={dt.datetime.fromtimestamp(start, dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}&end={dt.datetime.fromtimestamp(end, dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
        r = get(u)
        if not r: empty += 1
        else:
            empty = 0
            for t, lo, hi, op, cl, v in r: rows[t] = (op, hi, lo, cl)
        end = start - DAY; time.sleep(0.25)
    d = pd.DataFrame.from_dict(rows, orient="index", columns=["open", "high", "low", "close"]).sort_index()
    d.index = pd.to_datetime(d.index, unit="s"); d = d[d.index < pd.Timestamp("2026-10-28")]
    return d

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def target(df, f=8, s=21, g=9, trend=50, zero=False):
    c = df.close.to_numpy(); m = (ema(df.close, f)-ema(df.close, s)).to_numpy(); sg = ema(pd.Series(m), g).to_numpy()
    tm = df.close.rolling(trend).mean().to_numpy() if trend else None
    pos = np.zeros(len(c)); cur = 0
    for i in range(s+g, len(c)):
        ent = m[i] > sg[i] and (not zero or m[i] > 0) and (tm is None or (not np.isnan(tm[i]) and c[i] > tm[i]))
        if cur == 0 and ent: cur = 1
        elif cur == 1 and m[i] < sg[i]: cur = 0
        pos[i] = cur
    return pd.Series(pos, df.index)

def run(df, tgt):
    o = df.open; r = (o.shift(-1)/o-1)
    pos = tgt.shift(1).fillna(0); turn = pos.diff().abs().fillna(pos.abs())
    return pd.DataFrame({"s": pos*r - turn*COST, "b": r, "pos": pos, "turn": turn}).dropna()

def st(r):
    r = r.dropna()
    if len(r) < 30: return None
    eq = (1+r).cumprod(); yrs = len(r)/365; dd = (eq/eq.cummax()-1).min()
    return {"ret": round(eq.iloc[-1]-1, 4), "cagr": round(eq.iloc[-1]**(1/yrs)-1, 4), "sharpe": round(r.mean()/r.std()*np.sqrt(365), 3) if r.std() > 0 else 0,
            "maxdd": round(dd, 4), "days": len(r)}

def trades(df, res):
    o = df.open.reindex(res.index); out = []; cur = 0; e = None
    for t, v in res.pos.items():
        if v != cur:
            if cur and e is not None: out.append(o[t]/o[e]-1-2*COST)
            e = t if v else None; cur = v
    return np.array(out)


OUT = {}
for prod in ["BTC-USD", "ETH-USD", "SOL-USD"]:
    df = fetch(prod); t0 = df.index[60]
    R = run(df, target(df)); R = R[R.index >= t0]
    g = [R.index.year, R.index.month]
    m = R[["s", "b"]].groupby(g).agg(lambda x: (1+x).prod()-1); ex = R["pos"].groupby(g).mean()
    OUT[prod] = {"months": [[int(y), int(mo), round(float(a["s"]), 4), round(float(a["b"]), 4), round(float(ex[(y, mo)]), 3)] for (y, mo), a in m.iterrows()]}
    eq = (1+R.s).cumprod(); OUT[prod]["equity_end"] = float(eq.iloc[-1])
    print(prod, len(OUT[prod]["months"]), flush=True)
json.dump(OUT, open("research/monthly_results.json", "w"), indent=1)
