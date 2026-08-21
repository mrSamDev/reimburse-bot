"""Tests for the durable receipt audit ledger."""

import sqlite3
from decimal import Decimal

from app.services.ledger_service import ReceiptLedger


def _user_version(db) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _index_names(db) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("PRAGMA index_list('receipts')").fetchall()
        return {r[1] for r in rows}
    finally:
        conn.close()


def _entry(file_id="f1", **kw):
    e = dict(
        user_id=1,
        file_id=file_id,
        merchant_name="Ride with Sazzad",
        transaction_date="Jun 23, 2026",
        currency="AED",
        total=Decimal("23.50"),
        review_required=False,
        status="accepted",
        request_id="abc123",
    )
    e.update(kw)
    return e


def test_insert_and_count(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    assert lg.insert(_entry()) is True
    assert lg.count() == 1


def test_duplicate_file_id_is_ignored(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    assert lg.insert(_entry("f1")) is True
    assert lg.insert(_entry("f1")) is False
    assert lg.count() == 1


def test_empty_file_id_allows_multiple(tmp_path):
    # SQLite UNIQUE treats empty string as equal, so empty file_id must be NULL.
    lg = ReceiptLedger(tmp_path / "ledger.db")
    assert lg.insert(_entry("")) is True
    assert lg.insert(_entry("")) is True
    assert lg.count() == 2


def test_decimal_roundtrip(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1", total=Decimal("53.50")))
    rows = lg.all()
    assert rows[0]["total"] == Decimal("53.50")
    assert isinstance(rows[0]["total"], Decimal)


def test_review_required_and_status_columns(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1", review_required=True))
    row = lg.all()[0]
    assert row["review_required"] == 1
    assert row["status"] == "accepted"


def test_schema_init_idempotent(tmp_path):
    db = tmp_path / "ledger.db"
    ReceiptLedger(db)
    ReceiptLedger(db)  # must not error on repeat construction
    assert db.exists()


def test_all_orders_by_id(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1", total=Decimal("10")))
    lg.insert(_entry("f2", total=Decimal("20")))
    assert [r["total"] for r in lg.all()] == [Decimal("10"), Decimal("20")]


def test_ledger_created_in_nonexistent_dir(tmp_path):
    db = tmp_path / "nested" / "deep" / "ledger.db"
    lg = ReceiptLedger(db)
    lg.insert(_entry("f1"))
    assert lg.count() == 1


def test_schema_version_starts_at_1(tmp_path):
    db = tmp_path / "ledger.db"
    ReceiptLedger(db)
    assert _user_version(db) == 1


def test_reconstruct_is_idempotent_version(tmp_path):
    db = tmp_path / "ledger.db"
    ReceiptLedger(db)
    ReceiptLedger(db)
    assert _user_version(db) == 1


def test_indexes_created_for_common_queries(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1"))
    names = _index_names(tmp_path / "ledger.db")
    assert "idx_receipts_user_id" in names
    assert "idx_receipts_request_id" in names


def test_future_migration_applies_once(tmp_path, monkeypatch):
    from app.services import ledger_service as mod

    db = tmp_path / "ledger.db"
    ReceiptLedger(db)  # applies v1
    # Simulate a future release bumping the schema to v2 (adds a column).
    monkeypatch.setattr(mod, "_SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        mod,
        "_MIGRATIONS",
        {1: mod._MIGRATIONS[1], 2: "ALTER TABLE receipts ADD COLUMN region TEXT;"},
    )
    ReceiptLedger(db)  # fresh instance must apply v2
    assert _user_version(db) == 2
    ReceiptLedger(db)  # and must not re-apply it
    assert _user_version(db) == 2
