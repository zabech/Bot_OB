import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Database connection
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_connection():
    """Buat koneksi ke PostgreSQL."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Inisialisasi database dan buat tabel jika belum ada."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(50) NOT NULL,
            zone_type VARCHAR(10) NOT NULL,
            htf VARCHAR(10) NOT NULL,
            ltf VARCHAR(10) NOT NULL,
            entry_price DECIMAL(20,10) NOT NULL,
            zone_top DECIMAL(20,10) NOT NULL,
            zone_bottom DECIMAL(20,10) NOT NULL,
            invalidation DECIMAL(20,10) NOT NULL,
            target DECIMAL(20,10),
            entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) DEFAULT 'open',
            pnl_pct DECIMAL(10,2),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()
    logger.info("Database initialized successfully")

def migrate_db():
    """Tambahkan kolom entry_time jika belum ada (untuk database existing)."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            ALTER TABLE alerts ADD COLUMN entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """)
        conn.commit()
        logger.info("✅ Kolom entry_time berhasil ditambahkan")
    except Exception as e:
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            logger.info("ℹ️ Kolom entry_time sudah ada")
        else:
            logger.warning(f"⚠️ Error migrasi: {e}")
    
    cursor.close()
    conn.close()

def record_alert(symbol, zone_type, htf, ltf, entry_price, zone_top, zone_bottom, invalidation, target, entry_time=None):
    """Catat alert baru ke database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Jika entry_time tidak diberikan, gunakan waktu sekarang
    if entry_time is None:
        from datetime import datetime, timezone
        entry_time = datetime.now(timezone.utc).isoformat()
    
    cursor.execute("""
        INSERT INTO alerts 
        (symbol, zone_type, htf, ltf, entry_price, zone_top, zone_bottom, invalidation, target, entry_time, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
        RETURNING id
    """, (symbol, zone_type, htf, ltf, entry_price, zone_top, zone_bottom, invalidation, target, entry_time))
    
    alert_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    return alert_id

def get_open_alerts():
    """Ambil semua alert yang masih open."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, symbol, zone_type, htf, ltf, entry_price, zone_top, zone_bottom, 
               invalidation, target, entry_time, status, pnl_pct, created_at
        FROM alerts 
        WHERE status = 'open'
        ORDER BY created_at DESC
    """)
    
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    
    alerts = []
    for row in rows:
        alerts.append({
            "id": row[0],
            "symbol": row[1],
            "zone_type": row[2],
            "htf": row[3],
            "ltf": row[4],
            "entry_price": row[5],
            "zone_top": row[6],
            "zone_bottom": row[7],
            "invalidation": row[8],
            "target": row[9],
            "entry_time": row[10],  # <-- PASTIKAN INI ADA
            "status": row[11],
            "pnl_pct": row[12],
            "created_at": row[13],
        })
    
    return alerts

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


def resolve_alert_by_symbol(symbol: str, status: str, pnl_pct: float = None):
    """Tandai alert open terbaru untuk symbol ini sebagai selesai, simpan PnL."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE alerts SET status = %s, resolved_at = now(), pnl_pct = %s
                WHERE symbol = %s AND status = 'open'
                ORDER BY created_at DESC
                LIMIT 1;
            """, (status, pnl_pct, symbol))
        conn.commit()
    finally:
        conn.close()


def get_pnl_summary():
    """Hitung total dan rata-rata PnL dari semua trade yang sudah close."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total_closed,
                    SUM(CASE WHEN status = 'hit_target' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN status = 'invalidated' THEN 1 ELSE 0 END) as losses,
                    AVG(pnl_pct) as avg_pnl,
                    SUM(pnl_pct) as total_pnl,
                    MAX(pnl_pct) as best_trade,
                    MIN(pnl_pct) as worst_trade
                FROM alerts
                WHERE status IN ('hit_target', 'invalidated')
                AND pnl_pct IS NOT NULL;
            """)
            return cur.fetchone()
    finally:
        conn.close()


def update_alert_target(alert_id: int, target: float):
    """Update nilai target (TP) untuk alert yang sebelumnya NULL."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE alerts SET target = %s WHERE id = %s AND target IS NULL;
            """, (target, alert_id))
        conn.commit()
    finally:
        conn.close()


def update_alert_sl(symbol: str, new_sl: float):
    """Update SL (invalidation) untuk alert open terbaru — dipakai saat trailing stop."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE alerts SET invalidation = %s
                WHERE symbol = %s AND status = 'open'
                ORDER BY created_at DESC
                LIMIT 1;
            """, (new_sl, symbol))
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
