import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_connection():
    """Buat koneksi baru ke PostgreSQL. DATABASE_URL otomatis di-inject Railway
    saat addon PostgreSQL ditambahkan dan di-link ke service ini."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL tidak ditemukan. Pastikan PostgreSQL addon sudah ditambahkan "
            "dan ter-link ke service ini di Railway."
        )
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Buat tabel alerts kalau belum ada. Aman dipanggil berulang kali (idempotent)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    zone_type TEXT NOT NULL,           -- 'bullish' atau 'bearish'
                    htf TEXT NOT NULL,
                    ltf TEXT NOT NULL,
                    entry_price DOUBLE PRECISION NOT NULL,
                    zone_top DOUBLE PRECISION NOT NULL,
                    zone_bottom DOUBLE PRECISION NOT NULL,
                    invalidation DOUBLE PRECISION NOT NULL,
                    target DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    status TEXT NOT NULL DEFAULT 'open',  -- 'open', 'hit_target', 'invalidated'
                    resolved_at TIMESTAMPTZ
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol);")
        conn.commit()
        logger.info("Tabel alerts siap (sudah ada atau baru dibuat).")
    finally:
        conn.close()


def record_alert(symbol: str, zone_type: str, htf: str, ltf: str, entry_price: float,
                  zone_top: float, zone_bottom: float, invalidation: float, target):
    """Simpan 1 alert baru ke database, status awal selalu 'open'."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alerts
                    (symbol, zone_type, htf, ltf, entry_price, zone_top, zone_bottom, invalidation, target)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (symbol, zone_type, htf, ltf, entry_price, zone_top, zone_bottom, invalidation, target))
            alert_id = cur.fetchone()[0]
        conn.commit()
        return alert_id
    finally:
        conn.close()


def get_open_alerts():
    """Ambil semua alert yang masih berstatus 'open' (belum resolved), untuk dicek ulang."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM alerts WHERE status = 'open';")
            return cur.fetchall()
    finally:
        conn.close()


def resolve_alert(alert_id: int, status: str):
    """Tandai alert sebagai selesai: 'hit_target' atau 'invalidated'."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE alerts SET status = %s, resolved_at = now() WHERE id = %s;
            """, (status, alert_id))
        conn.commit()
    finally:
        conn.close()


def get_stats():
    """Hitung ringkasan statistik: total alert, win rate, breakdown status."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    status,
                    COUNT(*) as count
                FROM alerts
                GROUP BY status;
            """)
            rows = cur.fetchall()

            cur.execute("SELECT COUNT(*) as total FROM alerts;")
            total = cur.fetchone()["total"]

            cur.execute("""
                SELECT symbol, COUNT(*) as count
                FROM alerts
                GROUP BY symbol
                ORDER BY count DESC
                LIMIT 5;
            """)
            top_pairs = cur.fetchall()

        status_counts = {row["status"]: row["count"] for row in rows}
        hit = status_counts.get("hit_target", 0)
        invalid = status_counts.get("invalidated", 0)
        open_count = status_counts.get("open", 0)
        resolved = hit + invalid
        win_rate = (hit / resolved * 100) if resolved > 0 else None

        return {
            "total": total,
            "open": open_count,
            "hit_target": hit,
            "invalidated": invalid,
            "win_rate": win_rate,
            "top_pairs": top_pairs,
        }
    finally:
        conn.close()


def get_daily_stats():
    """Hitung ringkasan statistik untuk alert yang dibuat dalam 24 jam terakhir (rolling)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT status, COUNT(*) as count
                FROM alerts
                WHERE created_at >= now() - INTERVAL '24 hours'
                GROUP BY status;
            """)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) as total
                FROM alerts
                WHERE created_at >= now() - INTERVAL '24 hours';
            """)
            total = cur.fetchone()["total"]

            cur.execute("""
                SELECT symbol, COUNT(*) as count
                FROM alerts
                WHERE created_at >= now() - INTERVAL '24 hours'
                GROUP BY symbol
                ORDER BY count DESC
                LIMIT 5;
            """)
            top_pairs = cur.fetchall()

        status_counts = {row["status"]: row["count"] for row in rows}
        hit = status_counts.get("hit_target", 0)
        invalid = status_counts.get("invalidated", 0)
        open_count = status_counts.get("open", 0)
        resolved = hit + invalid
        win_rate = (hit / resolved * 100) if resolved > 0 else None

        return {
            "total": total,
            "open": open_count,
            "hit_target": hit,
            "invalidated": invalid,
            "win_rate": win_rate,
            "top_pairs": top_pairs,
        }
    finally:
        conn.close()
