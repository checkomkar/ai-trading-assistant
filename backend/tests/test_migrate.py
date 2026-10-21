import sqlite3

from trader.db.migrate import upgrade_db


def test_legacy_create_all_db_is_stamped_and_upgraded(tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    # Minimal pre-Alembic schema as produced by the M1 create_all (candles without `oi`).
    con.executescript("""
        CREATE TABLE instruments (id INTEGER PRIMARY KEY, token VARCHAR(64) UNIQUE NOT NULL);
        CREATE TABLE candles (instrument_id INTEGER, timeframe VARCHAR(8), ts DATETIME,
                              open FLOAT, high FLOAT, low FLOAT, close FLOAT, volume BIGINT,
                              PRIMARY KEY (instrument_id, timeframe, ts));
        INSERT INTO instruments (id, token) VALUES (1, 'SIM:NIFTY');
        INSERT INTO candles VALUES (1, '1m', '2026-10-20 04:00:00', 1, 2, 0.5, 1.5, 0);
    """)
    con.close()

    upgrade_db(f"sqlite+aiosqlite:///{db.as_posix()}")

    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(candles)")]
    version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    kept = con.execute("SELECT count(*) FROM candles").fetchone()[0]
    con.close()
    assert "oi" in cols and version == "c5a1e8d94b27" and kept == 1


def test_fresh_db_upgrades_to_head(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    upgrade_db(url)
    upgrade_db(url)  # idempotent
    con = sqlite3.connect(tmp_path / "fresh.db")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"instruments", "market_ticks", "candles", "option_snapshots", "events",
            "chat_conversations", "chat_messages", "knowledge_docs", "knowledge_chunks", "memory_notes"} <= tables
