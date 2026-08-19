"""
bootstrap_test.py — Uji signifikansi statistik hasil backtest.

Masalah yang dijawab: dengan sample kecil (misal 17 trade resolved),
Profit Factor 1.30 KELIHATAN bagus — tapi apa itu beneran edge, atau
cuma kebetulan dari urutan menang/kalah yang untung-untungan?

Cara kerja (bootstrap resampling, teknik statistik standar untuk
sample kecil):
1. Ambil semua r_multiple dari trade yang RESOLVED (win/loss saja,
   unresolved di-exclude karena tidak mempengaruhi PF)
2. Resample dengan penggantian (bootstrap) ribuan kali — tiap kali,
   ambil N trade secara acak (boleh duplikat) dari data yang sama
3. Hitung PF & Total R untuk tiap hasil resample
4. Lihat distribusinya: berapa % dari resample yang PF-nya <= 1.0
   (breakeven atau lebih buruk)?

Interpretasi:
- Kalau >20-30% resample hasilnya breakeven/rugi → hasil backtest
  asli KEMUNGKINAN BESAR belum signifikan, sample terlalu kecil buat
  disimpulkan strategi ini punya edge asli
- Kalau <5-10% resample yang breakeven/rugi → lebih meyakinkan bahwa
  edge ini bukan kebetulan murni (meski tetap bukan garansi mutlak)

PENTING: ini bukan uji "apakah strategi ini akan profit di masa
depan" — ini cuma uji "seberapa yakin kita bahwa sample yang kita
punya BUKAN kebetulan". Overfitting ke periode backtest tetap risiko
terpisah yang tidak dijawab oleh bootstrap ini.

Cara pakai:
    # 1. Export hasil backtest ke CSV dulu
    python backtest.py --months 24 --symbols BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP \\
        --htf 4H --direction bearish --export-csv hasil.csv

    # 2. Jalankan bootstrap test terhadap CSV itu
    python bootstrap_test.py hasil.csv
"""

import csv
import random
import sys


N_RESAMPLES = 10000


def load_resolved_r_multiples(csv_path: str) -> list:
    """Ambil r_multiple (net, setelah fee) dari trade yang resolved saja."""
    r_values = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["outcome"] in ("win", "loss"):
                r_values.append(float(row["r_multiple"]))
    return r_values


def profit_factor(r_values: list) -> float:
    gross_profit = sum(r for r in r_values if r > 0)
    gross_loss = abs(sum(r for r in r_values if r < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def bootstrap(r_values: list, n_resamples: int = N_RESAMPLES) -> list:
    """Resample dengan penggantian, hitung PF tiap resample."""
    n = len(r_values)
    pf_results = []
    for _ in range(n_resamples):
        sample = [random.choice(r_values) for _ in range(n)]
        pf_results.append(profit_factor(sample))
    return pf_results


def main():
    if len(sys.argv) < 2:
        print("Cara pakai: python bootstrap_test.py <path_ke_csv>")
        sys.exit(1)

    csv_path = sys.argv[1]
    r_values = load_resolved_r_multiples(csv_path)

    if len(r_values) < 5:
        print(
            f"Cuma {len(r_values)} trade resolved di CSV ini — "
            "terlalu sedikit untuk bootstrap test yang berarti. "
            "Perpanjang periode backtest dulu."
        )
        sys.exit(1)

    actual_pf = profit_factor(r_values)
    actual_total_r = sum(r_values)
    wins = sum(1 for r in r_values if r > 0)
    losses = sum(1 for r in r_values if r < 0)

    print("=" * 60)
    print("BOOTSTRAP SIGNIFICANCE TEST")
    print("=" * 60)
    print(f"File               : {csv_path}")
    print(f"Trade resolved     : {len(r_values)} ({wins}W / {losses}L)")
    print(f"Profit Factor asli : {actual_pf:.3f}")
    print(f"Total R asli       : {actual_total_r:.2f}R")
    print(f"Jumlah resample    : {N_RESAMPLES:,}")
    print()

    pf_results = bootstrap(r_values, N_RESAMPLES)
    pf_results.sort()

    pct_breakeven_or_worse = sum(1 for pf in pf_results if pf <= 1.0) / len(pf_results) * 100
    pct_negative_total_r = sum(
        1 for pf in pf_results if pf < 1.0
    ) / len(pf_results) * 100

    ci_5 = pf_results[int(0.05 * len(pf_results))]
    ci_50 = pf_results[int(0.50 * len(pf_results))]
    ci_95 = pf_results[int(0.95 * len(pf_results))]

    print("--- HASIL ---")
    print(f"90% Confidence Interval PF : [{ci_5:.3f}  —  {ci_95:.3f}]")
    print(f"Median PF hasil resample   : {ci_50:.3f}")
    print(
        f"% resample dengan PF <= 1.0 (breakeven/rugi) : "
        f"{pct_breakeven_or_worse:.1f}%"
    )
    print()

    print("--- INTERPRETASI ---")
    if ci_5 > 1.0:
        print(
            "✅ CUKUP MEYAKINKAN: batas bawah 90% CI masih di atas 1.0.\n"
            "   Kecil kemungkinan hasil positif ini murni kebetulan sample."
        )
    elif pct_breakeven_or_worse < 15:
        print(
            "🟡 BORDERLINE: mayoritas resample masih positif, tapi batas\n"
            "   bawah CI turun ke bawah 1.0. Edge kemungkinan ada tapi tipis\n"
            "   dan/atau sample masih kurang besar untuk yakin penuh."
        )
    else:
        print(
            f"⚠️  BELUM MEYAKINKAN: {pct_breakeven_or_worse:.0f}% dari skenario\n"
            "   resample menghasilkan breakeven atau rugi. Dengan sample\n"
            "   sekecil ini, PF asli yang kelihatan bagus BISA JADI cuma\n"
            "   kebetulan urutan menang/kalah, bukan edge asli.\n"
            "   Jangan jadikan basis keputusan live trading tanpa data lebih banyak."
        )

    print("=" * 60)


if __name__ == "__main__":
    main()
