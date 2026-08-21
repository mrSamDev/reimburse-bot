"""Tests for durable SQLite backup + retention pruning."""

import sqlite3

from app.services.backup_service import backup_database

# Old, chronologically-ordered backup filenames (ISO `%Y%m%d_%H%M%S`).
_OLD_TS = ("20260101_000001", "20260101_000002", "20260101_000003")


def _make_db(path, table="t"):
    conn = sqlite3.connect(str(path))
    conn.execute(f"CREATE TABLE {table} (x)")
    conn.commit()
    conn.close()


def _seed_backups(target, prefix, timestamps=_OLD_TS):
    target.mkdir(parents=True, exist_ok=True)
    for ts in timestamps:
        (target / f"{prefix}_{ts}.db").write_bytes(b"x")


def test_backup_writes_consistent_copy(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    dst = backup_database(src, tmp_path / "backups", label="db")
    assert dst.exists()
    conn = sqlite3.connect(str(dst))
    try:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    finally:
        conn.close()


def test_backup_no_retention_keeps_all(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    target = tmp_path / "backups"
    _seed_backups(target, "src_db")
    backup_database(str(src), target, label="db")  # no retention -> no pruning
    assert len(list(target.glob("src_db_*.db"))) == len(_OLD_TS) + 1


def test_backup_prunes_oldest_beyond_retention(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    target = tmp_path / "backups"
    _seed_backups(target, "src_db")
    fresh = backup_database(str(src), target, label="db", retention=2)
    remaining = sorted(target.glob("src_db_*.db"))
    assert len(remaining) == 2
    assert fresh in remaining
    # The oldest staged backup is pruned.
    assert not (target / "src_db_20260101_000001.db").exists()


def test_backup_prune_keeps_unrelated_backups(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    target = tmp_path / "backups"
    _seed_backups(target, "src_db")
    unrelated = target / "other_other_00000100.db"
    unrelated.write_bytes(b"x")
    backup_database(str(src), target, label="db", retention=1)
    # Only backups matching this db's prefix are pruned; unrelated files survive.
    assert unrelated.exists()


def test_backup_retention_one_keeps_single_latest(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    target = tmp_path / "backups"
    _seed_backups(target, "src_db")
    fresh = backup_database(str(src), target, label="db", retention=1)
    remaining = list(target.glob("src_db_*.db"))
    assert remaining == [fresh]
