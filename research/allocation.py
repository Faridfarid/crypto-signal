"""Perbandingan alokasi BTC/ETH/SOL dengan parameter TERKUNCI (MACD 8/21/9 + SMA50, long-only, chart harian).
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


S = {}; B = {}
for prod in ["BTC-USD", "ETH-USD", "SOL-USD"]:
    df = fetch(prod); R = run(df, target(df)); S[prod[:3]] = R.s; B[prod[:3]] = R.b
s = pd.DataFrame(S).dropna(); b = pd.DataFrame(B).dropna()
vol = pd.DataFrame(B).rolling(90, min_periods=30).std()   # volatilitas harga 90 hari, dihitung sebelum dipotong (tanpa melihat masa depan)
st0 = pd.Timestamp("2021-08-16"); s = s[s.index >= st0]; b = b[b.index >= st0]

def fixed(w): return lambda d: w
def invvol(d):
    v = vol.loc[:d]
    v = v.iloc[-1] if len(v) else None
    if v is None or v.isna().any(): return {"BTC": 1/3, "ETH": 1/3, "SOL": 1/3}
    iv = 1/v; iv = iv/iv.sum(); return iv.to_dict()
ALLOC = {"SOL saja": fixed({"BTC": 0, "ETH": 0, "SOL": 1}), "Sama rata 1/3": fixed({"BTC": 1/3, "ETH": 1/3, "SOL": 1/3}),
         "SOL 50 / BTC 25 / ETH 25": fixed({"BTC": .25, "ETH": .25, "SOL": .5}), "Berdasarkan risiko (1/volatilitas)": invvol,
         "BTC saja": fixed({"BTC": 1, "ETH": 0, "SOL": 0})}

def portfolio(rets, wfun, cost=COST):
    """Rebalance antar-koin tiap awal bulan; di dalam bulan bobot mengambang. Biaya rebalance 0,15% atas perpindahan."""
    out = []; val = None; wlog = []
    for m, g in rets.groupby([rets.index.year, rets.index.month]):
        w = pd.Series(wfun(g.index[0] - pd.Timedelta(days=1)))[rets.columns]
        sleeves = w.copy(); first = True
        if val is not None:
            drift = val/val.sum(); turn = (w - drift).abs().sum()/2
        else: turn = 0
        tot_prev = 1.0
        for t, r in g.iterrows():
            if first: sleeves = sleeves*(1 - turn*cost); first = False
            sleeves = sleeves*(1+r); tot = sleeves.sum(); out.append((t, tot/tot_prev-1)); tot_prev = tot
        val = sleeves; wlog.append(w.round(3).to_dict())
    return pd.Series(dict(out)), wlog

def stats(r):
    if len(r) < 30: return None
    eq = (1+r).cumprod(); dd = (eq/eq.cummax()-1)
    yr = r.groupby(r.index.year).apply(lambda x: (1+x).prod()-1); mo = r.groupby([r.index.year, r.index.month]).apply(lambda x: (1+x).prod()-1)
    return {"cagr": round(float(eq.iloc[-1]**(365/len(r))-1), 4), "total": round(float(eq.iloc[-1]-1), 4), "sharpe": round(float(r.mean()/r.std()*np.sqrt(365)), 3),
            "maxdd": round(float(dd.min()), 4), "vol": round(float(r.std()*np.sqrt(365)), 3), "worst_month": round(float(mo.min()), 4), "worst_year": round(float(yr.min()), 4),
            "yearly": {int(k): round(float(v), 4) for k, v in yr.items()}}

OUT = {"from": str(s.index[0].date()), "to": str(s.index[-1].date()), "alloc": {}}
P = {}
for name, wf in ALLOC.items():
    r, wl = portfolio(s, wf); P[name] = r
    OUT["alloc"][name] = {"all": stats(r), "unseen_2021_2023": stats(r[r.index < CUT]), "seen_2023_2026": stats(r[r.index >= CUT])}
    if "risiko" in name: OUT["alloc"][name]["avg_weights"] = {k: round(float(np.mean([w[k] for w in wl])), 3) for k in ["BTC", "ETH", "SOL"]}
rb, _ = portfolio(b, fixed({"BTC": 1/3, "ETH": 1/3, "SOL": 1/3}), 0); OUT["bh_equal"] = {"all": stats(rb)}
rbs, _ = portfolio(b, fixed({"BTC": 0, "ETH": 0, "SOL": 1}), 0); OUT["bh_sol"] = {"all": stats(rbs)}

# Monte Carlo: block bootstrap berpasangan (indeks hari sama utk semua alokasi), horizon 1 tahun
rng = np.random.default_rng(1); Pm = pd.DataFrame(P)
def mc(frame, N=5000, H=365, mb=20):
    A = frame.dropna().to_numpy(); n = len(A)
    if n < 60: return None
    res = {k: [] for k in frame.columns}
    for _ in range(N):
        idx = np.empty(H, int); i = rng.integers(n)
        for k in range(H):
            idx[k] = i; i = rng.integers(n) if rng.random() < 1/mb else (i+1) % n
        X = A[idx]; eq = np.cumprod(1+X, axis=0); dd = (eq/np.maximum.accumulate(eq, axis=0)-1).min(axis=0)
        for j, c in enumerate(frame.columns): res[c].append((eq[-1, j]-1, dd[j]))
    out = {}
    for c, v in res.items():
        v = np.array(v); out[c] = {"p5": round(float(np.percentile(v[:, 0], 5)), 4), "p50": round(float(np.percentile(v[:, 0], 50)), 4), "p95": round(float(np.percentile(v[:, 0], 95)), 4),
                                   "p_loss": round(float((v[:, 0] < 0).mean()), 3), "dd_median": round(float(np.median(v[:, 1])), 4), "p_dd_worse_40": round(float((v[:, 1] < -0.4).mean()), 3)}
    return out
OUT["mc_full_period"] = mc(Pm)
OUT["mc_unseen_period_only"] = mc(Pm[Pm.index < CUT])
json.dump(OUT, open("research/allocation_results.json", "w"), indent=1)
print(json.dumps({k: v["all"] for k, v in OUT["alloc"].items()}, indent=1))
