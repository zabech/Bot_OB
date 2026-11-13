"""
analyze_sl.py — Bedah trade yang sudah selesai (TP / SL / breakeven):
seberapa jauh harga sempat bergerak ke arah profit sebelum berbalik (MFE
dalam satuan R), searah/kebalikan trend MA50 saat entry, dan apakah SL
kena oleh wick sesaat atau breakdown beneran (close di luar SL + volume).

Pakai data candle 1H historis OKX (bukan harga sekarang).

Cara pakai (di VPS):
    cd /root/Bot_OB
    source venv/bin/activate
    python3 analyze_sl.py                 # semua trade selesai di database
    python3 analyze_sl.py 2026-11-01      # hanya sinyal dibuat sejak tanggal ini
"""

import sys
import time
from datetime import timezone

# config HARUS diimpor sebelum db (config.py yang memanggil load_dotenv)
from config import MA_PERIOD, RISK_REWARD_RATIO
import db
from market_utils import calculate_ma
from backtest import fetch_full_history_raw

H = 3600 * 1000
BAR_MS = {"15m": 15 * 60 * 1000, "1H": H, "4H": 4 * H, "1D": 24 * H}


def to_ms(d) -> int:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def load_alerts(since):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            q = """
                SELECT symbol, zone_type, htf, entry_price, zone_top, zone_bottom,
                       invalidation, target, status, pnl_pct, created_at, resolved_at
                FROM alerts
                WHERE status != 'open'
            """
            params = []
            if since:
                q += " AND created_at >= %s"
                params.append(since)
            q += " ORDER BY created_at;"
            cur.execute(q, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x, nd=2, suffix=""):
    return "-" if x is None else f"{x:.{nd}f}{suffix}"


def analyze(a):
    sym, side, htf = a["symbol"], a["zone_type"], a["htf"]
    entry = float(a["entry_price"])
    sl = float(a["invalidation"])
    tp = float(a["target"]) if a["target"] is not None else None
    created = to_ms(a["created_at"])
    resolved = to_ms(a["resolved_at"]) if a["resolved_at"] else int(time.time() * 1000)

    # 1R dalam satuan harga: dari jarak TP / R:R (SL di DB bisa sudah digeser ke entry)
    risk = abs(tp - entry) / RISK_REWARD_RATIO if tp is not None else abs(entry - sl)
    if risk <= 0:
        return None
    risk_pct = risk / entry * 100

    # Trend MA50 HTF pada saat entry
    bar = BAR_MS.get(htf, BAR_MS["4H"])
    hc = fetch_full_history_raw(sym, htf, created - bar * (MA_PERIOD + 20), created)
    hc = [c for c in hc if c["ts"] <= created]
    ma = calculate_ma(hc, MA_PERIOD)
    aligned = None if ma is None else ((entry > ma) if side == "bullish" else (entry < ma))

    # Candle 1H sepanjang hidup trade (+25 jam sebelum entry untuk rata-rata volume)
    lc = fetch_full_history_raw(sym, "1H", created - 25 * H, resolved + 2 * H)
    win = [c for c in lc if created <= c["ts"] <= resolved]

    mfe = None
    if win:
        if side == "bullish":
            mfe = (max(c["high"] for c in win) - entry) / risk
        else:
            mfe = (entry - min(c["low"] for c in win)) / risk
        mfe = max(mfe, 0.0)

    hours = (resolved - created) / H
    zone_w = None
    if a["zone_top"] is not None and a["zone_bottom"] is not None:
        zone_w = (float(a["zone_top"]) - float(a["zone_bottom"])) / entry * 100

    # Jenis penembusan SL (khusus trade invalidated)
    sl_type, vol_ratio = None, None
    if a["status"] == "invalidated":
        for i, c in enumerate(lc):
            if c["ts"] < created:
                continue
            hit = (c["low"] <= sl) if side == "bullish" else (c["high"] >= sl)
            if hit:
                prev = [x["vol"] for x in lc[max(0, i - 20):i]]
                vol_ratio = c["vol"] / (sum(prev) / len(prev)) if prev and sum(prev) > 0 else None
                closed_through = (c["close"] < sl) if side == "bullish" else (c["close"] > sl)
                if closed_through and vol_ratio is not None and vol_ratio >= 1.5:
                    sl_type = "breakdown+volume"
                elif closed_through:
                    sl_type = "breakdown biasa"
                else:
                    sl_type = "wick saja"
                break

    return {
        "symbol": sym, "side": side, "status": a["status"], "aligned": aligned,
        "risk_pct": risk_pct, "mfe": mfe, "hours": hours, "zone_w": zone_w,
        "sl_type": sl_type, "vol_ratio": vol_ratio,
    }


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else None
    alerts = load_alerts(since)
    print(f"Trade selesai dianalisa: {len(alerts)}" + (f" (sejak {since})" if since else ""))
    if not alerts:
        return

    rows = []
    print(f"\n{'Pair':<16}{'Arah':<8}{'Status':<12}{'Trend':<8}{'Risk%':<7}{'MFE(R)':<8}{'Jam':<7}{'Zona%':<7}{'Tipe SL'}")
    print("-" * 95)
    for a in alerts:
        try:
            r = analyze(a)
        except Exception as e:
            print(f"{a['symbol']:<16}GAGAL: {e}")
            continue
        if not r:
            continue
        rows.append(r)
        trend = "-" if r["aligned"] is None else ("searah" if r["aligned"] else "lawan")
        t = r["sl_type"] or "-"
        if r["vol_ratio"] is not None:
            t += f" (vol x{r['vol_ratio']:.1f})"
        print(f"{r['symbol']:<16}{r['side']:<8}{r['status']:<12}{trend:<8}"
              f"{fmt(r['risk_pct'],1):<7}{fmt(r['mfe']):<8}{fmt(r['hours'],0):<7}{fmt(r['zone_w'],1):<7}{t}")
        time.sleep(0.2)

    print("\n" + "=" * 95)
    print("RINGKASAN")
    print("=" * 95)
    for st in ("hit_target", "breakeven", "invalidated"):
        g = [r for r in rows if r["status"] == st]
        if not g:
            continue
        print(f"\n[{st}] {len(g)} trade")
        print(f"  Rata-rata MFE        : {fmt(avg([r['mfe'] for r in g]))} R")
        print(f"  Rata-rata durasi     : {fmt(avg([r['hours'] for r in g]),0)} jam")
        print(f"  Rata-rata jarak SL   : {fmt(avg([r['risk_pct'] for r in g]),1)} %")
        print(f"  Bullish / Bearish    : {sum(r['side']=='bullish' for r in g)} / {sum(r['side']=='bearish' for r in g)}")
        print(f"  Searah / lawan trend : {sum(r['aligned'] is True for r in g)} / {sum(r['aligned'] is False for r in g)}")

    sl = [r for r in rows if r["status"] == "invalidated"]
    if sl:
        reached_1r = [r for r in sl if r["mfe"] is not None and r["mfe"] >= 1.0]
        print(f"\nTrade SL yang SEMPAT mencapai +1R sebelum kena SL: {len(reached_1r)} dari {len(sl)}")
        print("  (seharusnya trailing stop mengamankannya ke breakeven — kalau >0, cek log)")
        for k in ("breakdown+volume", "breakdown biasa", "wick saja"):
            print(f"  SL tipe '{k}': {sum(r['sl_type']==k for r in sl)}")


if __name__ == "__main__":
    main()
