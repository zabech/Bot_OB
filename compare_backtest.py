"""
compare_backtest.py — Jalankan beberapa kombinasi config backtest.py sekaligus,
lalu rangkum hasilnya jadi satu tabel perbandingan.

Kenapa lewat subprocess (bukan import langsung)?
config.py membaca semua parameter dari environment variable SEKALI saat
modul di-import, lalu backtest.py meng-copy nilainya ke module-level
constant (mis. RISK_REWARD_RATIO). Karena constant itu sudah "beku" saat
import, satu-satunya cara aman untuk uji banyak kombinasi tanpa reload
trick yang rawan bug adalah: jalankan backtest.py sebagai proses baru per
kombinasi, dengan environment variable yang berbeda tiap kali.

Cara pakai:
    python compare_backtest.py

Edit SCENARIOS di bawah untuk menambah/mengubah kombinasi yang mau diuji.
Symbol/HTF/months per skenario BISA berbeda (mis. bandingkan universe pair
yang beda), tapi supaya adil, tetap 1 variabel yang diubah tiap baris.
"""

import os
import re
import subprocess
import sys

PYTHON = sys.executable

# ============================================================
# DEFINISIKAN SKENARIO DI SINI
# ============================================================
# "env": override environment variable (str -> str), dibaca oleh config.py
# "args": argumen CLI persis seperti yang biasa kamu ketik ke backtest.py
#
# Baseline: BTC+ETH+SOL, 4H saja, 3 bulan, config default.

SCENARIOS = [
    {
        "name": "Baseline (RR 2.0, FVG off, trend off)",
        "env": {
            "RISK_REWARD_RATIO": "2.0",
            "REQUIRE_FVG": "false",
            "USE_TREND_FILTER": "false",
        },
        "args": [
            "--months", "3",
            "--symbols", "BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP",
            "--htf", "4H",
        ],
    },
    {
        "name": "RR 2.5",
        "env": {
            "RISK_REWARD_RATIO": "2.5",
            "REQUIRE_FVG": "false",
            "USE_TREND_FILTER": "false",
        },
        "args": [
            "--months", "3",
            "--symbols", "BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP",
            "--htf", "4H",
        ],
    },
    {
        "name": "FVG wajib (RR 2.0)",
        "env": {
            "RISK_REWARD_RATIO": "2.0",
            "REQUIRE_FVG": "true",
            "USE_TREND_FILTER": "false",
        },
        "args": [
            "--months", "3",
            "--symbols", "BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP",
            "--htf", "4H",
        ],
    },
    {
        "name": "Trend filter ON (RR 2.0)",
        "env": {
            "RISK_REWARD_RATIO": "2.0",
            "REQUIRE_FVG": "false",
            "USE_TREND_FILTER": "true",
        },
        "args": [
            "--months", "3",
            "--symbols", "BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP",
            "--htf", "4H",
        ],
    },
    {
        "name": "FVG + Trend filter (RR 2.0)",
        "env": {
            "RISK_REWARD_RATIO": "2.0",
            "REQUIRE_FVG": "true",
            "USE_TREND_FILTER": "true",
        },
        "args": [
            "--months", "3",
            "--symbols", "BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP",
            "--htf", "4H",
        ],
    },
]

# ============================================================
# PARSING OUTPUT backtest.py
# ============================================================

PATTERNS = {
    "total_sinyal": r"Total sinyal\s*:\s*(\d+)",
    "win": r"^Win\s*:\s*(\d+)",
    "loss": r"^Loss\s*:\s*(\d+)",
    "unresolved": r"Unresolved\s*:\s*(\d+)",
    "win_rate": r"Win rate\s*:\s*([\d.]+)%",
    "total_r": r"Total R\s*:\s*(-?[\d.]+)R",
    "profit_factor": r"Profit Factor\s*:\s*(INF|-?[\d.]+)",
    "max_dd": r"Max Drawdown\s*:\s*(-?[\d.]+)R",
    "avg_r": r"Avg R / signal\s*:\s*(-?[\d.]+)R",
}


def parse_output(text: str) -> dict:
    result = {}
    for key, pattern in PATTERNS.items():
        m = re.search(pattern, text, re.MULTILINE)
        result[key] = m.group(1) if m else "-"
    return result


def run_scenario(scenario: dict) -> dict:
    env = os.environ.copy()
    env.update(scenario["env"])

    cmd = [PYTHON, "backtest.py"] + scenario["args"]

    print(f"\n{'=' * 65}")
    print(f"Menjalankan: {scenario['name']}")
    print(f"Command: {' '.join(cmd)}")
    print(f"Env override: {scenario['env']}")
    print("=" * 65)

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 menit safety timeout per skenario
        )
    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] Skenario '{scenario['name']}' melebihi 30 menit, di-skip.")
        return {"name": scenario["name"], "error": "timeout"}

    output = proc.stdout + "\n" + proc.stderr

    # Tampilkan output mentah supaya kamu bisa lihat progress/log tiap skenario
    print(output[-3000:])  # potong biar tidak membanjiri terminal

    if proc.returncode != 0:
        print(f"[ERROR] Skenario '{scenario['name']}' exit code {proc.returncode}")
        return {"name": scenario["name"], "error": f"exit {proc.returncode}"}

    parsed = parse_output(output)
    parsed["name"] = scenario["name"]
    return parsed


def print_comparison_table(results: list):
    print("\n\n" + "=" * 100)
    print("RINGKASAN PERBANDINGAN")
    print("=" * 100)

    header = (
        f"{'Skenario':<32} | {'Sinyal':>6} | {'WR%':>7} | "
        f"{'Total R':>8} | {'PF':>6} | {'MaxDD':>7} | {'Avg R':>7}"
    )
    print(header)
    print("-" * len(header))

    for r in results:
        if r.get("error"):
            print(f"{r['name']:<32} | GAGAL ({r['error']})")
            continue

        print(
            f"{r['name']:<32} | "
            f"{r['total_sinyal']:>6} | "
            f"{r['win_rate']:>6}% | "
            f"{r['total_r']:>7}R | "
            f"{r['profit_factor']:>6} | "
            f"{r['max_dd']:>6}R | "
            f"{r['avg_r']:>6}R"
        )

    print("=" * 100)
    print(
        "\nCatatan: bandingkan PF (Profit Factor) dan Total R sebagai prioritas utama,\n"
        "bukan cuma WR% — winrate tinggi dengan RR kecil bisa tetap rugi, dan\n"
        "sebaliknya. Perhatikan juga jumlah sinyal: skenario dengan sinyal sangat\n"
        "sedikit (<20) hasilnya belum bisa dipercaya penuh, walau angkanya kelihatan\n"
        "bagus di atas kertas."
    )


def main():
    all_results = []

    for scenario in SCENARIOS:
        result = run_scenario(scenario)
        all_results.append(result)

    print_comparison_table(all_results)


if __name__ == "__main__":
    main()
