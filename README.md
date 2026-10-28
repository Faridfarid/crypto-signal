# crypto-signal

Paper trading strategi MACD 8/21/9 + SMA50 (chart harian) untuk SOL, ETH, BTC. Tidak ada transaksi nyata.

- **Beli**: MACD(8,21) di atas Signal(9) dan close di atas SMA50.
- **Jual**: MACD memotong ke bawah Signal.
- Candle harian tutup 07:00 WIB. GitHub Actions menghitung sinyal pukul ~07:05 WIB (`.github/workflows/daily-signal.yml`).
- Paper trading: modal virtual $100 per koin, eksekusi di harga open 07:00 WIB, biaya 0,15% per transaksi. Posisi lama tidak dikejar; hanya sinyal BELI baru setelah mulai yang dieksekusi.
- Data: Coinbase Exchange API publik, cadangan Kraken.

File hasil:
- `signals/latest.json`: sinyal dan status hari ini.
- `state/paper.json`: posisi paper trading.
- `state/trades.csv`: riwayat transaksi paper.

Aturan mekanis hasil backtest, bukan saran investasi.
