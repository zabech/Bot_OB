"""
webapp.py — Dashboard web sederhana untuk memantau statistik & trade aktif.

Berdiri SENDIRI, terpisah dari proses bot utama (main.py) — baca langsung
dari database PostgreSQL yang sama (via db.py) dan fetch harga terkini
langsung dari OKX (via market_utils.py). Tidak perlu bot utama jalan
untuk dashboard ini bisa diakses, dan tidak mengganggu proses bot sama
sekali (read-only, tidak pernah menulis ke DB).

Cara jalanin:
    python webapp.py

Lalu buka: http://<ip-vps>:5000  (atau port sesuai DASHBOARD_PORT)

⚠️ KEAMANAN: kalau VPS kamu punya IP publik, endpoint ini defaultnya
BISA DIAKSES SIAPA SAJA yang tau IP+port kamu, tanpa login. Set
DASHBOARD_USERNAME dan DASHBOARD_PASSWORD di .env untuk mengaktifkan
basic auth. Untuk produksi, sebaiknya juga taruh di belakang firewall
(hanya buka port ke IP kamu sendiri) atau reverse proxy dengan HTTPS.
"""

from datetime import datetime, timezone

from flask import Flask, Response, request

import db
from market_utils import get_current_price
from config import (
    DASHBOARD_PORT,
    DASHBOARD_USERNAME,
    DASHBOARD_PASSWORD,
    DASHBOARD_REFRESH_SECONDS,
    RISK_REWARD_RATIO,
)

app = Flask(__name__)


# ============================================================
# AUTH (opsional — aktif kalau DASHBOARD_USERNAME/PASSWORD di-set)
# ============================================================

def _check_auth():
    if not DASHBOARD_USERNAME and not DASHBOARD_PASSWORD:
        return True  # auth tidak diaktifkan
    auth = request.authorization
    return (
        auth
        and auth.username == DASHBOARD_USERNAME
        and auth.password == DASHBOARD_PASSWORD
    )


@app.before_request
def require_auth():
    if not _check_auth():
        return Response(
            "Login diperlukan.",
            401,
            {"WWW-Authenticate": 'Basic realm="Bot_OB Dashboard"'},
        )


# ============================================================
# HELPERS
# ============================================================

def _fmt(value, decimals=4):
    if value is None:
        return "-"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value, decimals=2):
    if value is None:
        return "-"
    try:
        return f"{float(value):+.{decimals}f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_duration(entry_time) -> str:
    if not entry_time:
        return "-"
    try:
        if isinstance(entry_time, datetime):
            dt = entry_time
        else:
            dt = datetime.fromisoformat(str(entry_time))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        total_seconds = delta.total_seconds()
        days = int(total_seconds // 86400)
        hours = int((total_seconds % 86400) // 3600)
        if days > 0:
            return f"{days}d {hours}h"
        minutes = int((total_seconds % 3600) // 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "-"


def _pnl_color_class(value) -> str:
    if value is None:
        return ""
    try:
        return "pos" if float(value) >= 0 else "neg"
    except (TypeError, ValueError):
        return ""


# ============================================================
# DATA GATHERING
# ============================================================

def get_dashboard_data() -> dict:
    open_alerts = db.get_open_alerts()

    active_trades = []
    for alert in open_alerts:
        entry = alert.get("entry_price")
        price = None
        pnl_pct = None

        try:
            price = get_current_price(alert["symbol"])
        except Exception:
            price = None

        if price is not None and entry:
            entry = float(entry)
            if alert["zone_type"] == "bullish":
                pnl_pct = (price - entry) / entry * 100
            else:
                pnl_pct = (entry - price) / entry * 100

        active_trades.append({
            **alert,
            "current_price": price,
            "pnl_pct": pnl_pct,
            "duration": _fmt_duration(alert.get("entry_time")),
        })

    stats = db.get_stats()
    daily_stats = db.get_daily_stats()
    pnl_summary = db.get_pnl_summary()

    return {
        "active_trades": active_trades,
        "stats": stats,
        "daily_stats": daily_stats,
        "pnl_summary": pnl_summary,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# ============================================================
# HTML TEMPLATE (inline, tanpa file terpisah — biar tetap "satu file")
# ============================================================

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>Bot_OB Dashboard</title>
<style>
    :root {{
        --bg: #0d1117;
        --card-bg: #161b22;
        --border: #30363d;
        --text: #c9d1d9;
        --text-dim: #8b949e;
        --green: #3fb950;
        --red: #f85149;
        --blue: #58a6ff;
        --yellow: #d29922;
    }}
    * {{ box-sizing: border-box; }}
    body {{
        background: var(--bg);
        color: var(--text);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        margin: 0;
        padding: 16px;
        max-width: 1100px;
        margin-left: auto;
        margin-right: auto;
    }}
    h1 {{ font-size: 20px; margin-bottom: 4px; }}
    .subtitle {{ color: var(--text-dim); font-size: 13px; margin-bottom: 20px; }}
    .cards {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 10px;
        margin-bottom: 24px;
    }}
    .card {{
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 12px 14px;
    }}
    .card .label {{ color: var(--text-dim); font-size: 12px; margin-bottom: 4px; }}
    .card .value {{ font-size: 20px; font-weight: 600; }}
    section {{ margin-bottom: 28px; }}
    section h2 {{
        font-size: 15px;
        color: var(--text-dim);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 10px;
        border-bottom: 1px solid var(--border);
        padding-bottom: 6px;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{
        text-align: left;
        color: var(--text-dim);
        font-weight: 500;
        padding: 8px 10px;
        border-bottom: 1px solid var(--border);
        white-space: nowrap;
    }}
    td {{
        padding: 8px 10px;
        border-bottom: 1px solid var(--border);
        white-space: nowrap;
    }}
    tr:hover td {{ background: var(--card-bg); }}
    .pos {{ color: var(--green); }}
    .neg {{ color: var(--red); }}
    .dir-bullish {{ color: var(--green); }}
    .dir-bearish {{ color: var(--red); }}
    .empty {{ color: var(--text-dim); padding: 16px; text-align: center; font-style: italic; }}
    .table-wrap {{ overflow-x: auto; }}
    .footer {{ color: var(--text-dim); font-size: 12px; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>
    <h1>📊 Bot_OB Dashboard</h1>
    <div class="subtitle">Auto-refresh tiap {refresh_seconds} detik &middot; Update terakhir: {generated_at}</div>

    <div class="cards">
        <div class="card">
            <div class="label">Total Alert</div>
            <div class="value">{stats_total}</div>
        </div>
        <div class="card">
            <div class="label">Trade Aktif</div>
            <div class="value">{stats_open}</div>
        </div>
        <div class="card">
            <div class="label">Win Rate</div>
            <div class="value">{stats_winrate}</div>
        </div>
        <div class="card">
            <div class="label">Hit Target</div>
            <div class="value pos">{stats_hit}</div>
        </div>
        <div class="card">
            <div class="label">Invalidated</div>
            <div class="value neg">{stats_invalid}</div>
        </div>
        <div class="card">
            <div class="label">Total PnL</div>
            <div class="value {pnl_total_class}">{pnl_total}</div>
        </div>
    </div>

    <section>
        <h2>💼 Trade Aktif ({trade_count})</h2>
        <div class="table-wrap">
        {active_trades_table}
        </div>
    </section>

    <section>
        <h2>📈 Statistik 24 Jam Terakhir</h2>
        <div class="cards">
            <div class="card">
                <div class="label">Sinyal (24j)</div>
                <div class="value">{daily_total}</div>
            </div>
            <div class="card">
                <div class="label">Win Rate (24j)</div>
                <div class="value">{daily_winrate}</div>
            </div>
        </div>
    </section>

    <section>
        <h2>🏆 Pair Paling Sering Muncul</h2>
        <div class="table-wrap">
        {top_pairs_table}
        </div>
    </section>

    <div class="footer">Bot_OB Dashboard &middot; read-only, tidak mempengaruhi bot live</div>
</body>
</html>
"""


def render_active_trades_table(trades: list) -> str:
    if not trades:
        return '<div class="empty">Tidak ada trade aktif saat ini.</div>'

    rows = []
    for t in trades:
        direction_class = "dir-bullish" if t["zone_type"] == "bullish" else "dir-bearish"
        direction_label = "🟢 Bullish" if t["zone_type"] == "bullish" else "🔴 Bearish"
        pnl_class = _pnl_color_class(t["pnl_pct"])

        rows.append(
            "<tr>"
            f"<td>{t['symbol']}</td>"
            f"<td class='{direction_class}'>{direction_label}</td>"
            f"<td>{t['htf']}</td>"
            f"<td>{_fmt(t['entry_price'], 6)}</td>"
            f"<td>{_fmt(t['current_price'], 6)}</td>"
            f"<td class='{pnl_class}'>{_fmt_pct(t['pnl_pct'])}</td>"
            f"<td>{_fmt(t['invalidation'], 6)}</td>"
            f"<td>{_fmt(t['target'], 6)}</td>"
            f"<td>{t['duration']}</td>"
            "</tr>"
        )

    return (
        "<table><thead><tr>"
        "<th>Pair</th><th>Arah</th><th>HTF</th><th>Entry</th>"
        "<th>Harga Sekarang</th><th>PnL</th><th>SL</th><th>TP</th><th>Durasi</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_top_pairs_table(top_pairs) -> str:
    if not top_pairs:
        return '<div class="empty">Belum ada data.</div>'

    rows = []
    for row in top_pairs:
        symbol = row["symbol"] if isinstance(row, dict) else row[0]
        count = row["count"] if isinstance(row, dict) else row[1]
        rows.append(f"<tr><td>{symbol}</td><td>{count} sinyal</td></tr>")

    return (
        "<table><thead><tr><th>Pair</th><th>Jumlah Sinyal</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def dashboard():
    data = get_dashboard_data()
    stats = data["stats"]
    daily = data["daily_stats"]
    pnl_summary = data["pnl_summary"] or {}

    win_rate = stats.get("win_rate")
    win_rate_str = f"{win_rate:.1f}%" if win_rate is not None else "-"

    daily_win_rate = daily.get("win_rate")
    daily_win_rate_str = f"{daily_win_rate:.1f}%" if daily_win_rate is not None else "-"

    total_pnl = pnl_summary.get("total_pnl")
    total_pnl_str = _fmt_pct(total_pnl) if total_pnl is not None else "-"
    total_pnl_class = _pnl_color_class(total_pnl)

    html = PAGE_TEMPLATE.format(
        refresh_seconds=DASHBOARD_REFRESH_SECONDS,
        generated_at=data["generated_at"],
        stats_total=stats.get("total", 0),
        stats_open=stats.get("open", 0),
        stats_winrate=win_rate_str,
        stats_hit=stats.get("hit_target", 0),
        stats_invalid=stats.get("invalidated", 0),
        pnl_total=total_pnl_str,
        pnl_total_class=total_pnl_class,
        trade_count=len(data["active_trades"]),
        active_trades_table=render_active_trades_table(data["active_trades"]),
        daily_total=daily.get("total", 0),
        daily_winrate=daily_win_rate_str,
        top_pairs_table=render_top_pairs_table(stats.get("top_pairs", [])),
    )

    return html


@app.route("/health")
def health():
    """Endpoint sederhana buat cek dashboard hidup (tidak butuh auth khusus)."""
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    if not DASHBOARD_USERNAME or not DASHBOARD_PASSWORD:
        print(
            "⚠️  PERINGATAN: DASHBOARD_USERNAME/DASHBOARD_PASSWORD belum di-set "
            "di .env — dashboard ini BISA DIAKSES TANPA LOGIN oleh siapa saja "
            "yang tau IP+port VPS kamu. Set kedua env var itu untuk mengaktifkan "
            "basic auth."
        )
    print(f"Dashboard jalan di http://0.0.0.0:{DASHBOARD_PORT}")
    app.run(host="0.0.0.0", port=DASHBOARD_PORT)
