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
    assert _user_version(db) == 2


def test_reconstruct_is_idempotent_version(tmp_path):
    db = tmp_path / "ledger.db"
    ReceiptLedger(db)
    ReceiptLedger(db)
    assert _user_version(db) == 2


def test_indexes_created_for_common_queries(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1"))
    names = _index_names(tmp_path / "ledger.db")
    assert "idx_receipts_user_id" in names
    assert "idx_receipts_request_id" in names


def test_future_migration_applies_once(tmp_path, monkeypatch):
    from app.services import ledger_service as mod

    db = tmp_path / "ledger.db"
    ReceiptLedger(db)  # applies current version
    # Simulate a future release bumping the schema.
    monkeypatch.setattr(mod, "_SCHEMA_VERSION", 3)
    monkeypatch.setattr(
        mod,
        "_MIGRATIONS",
        {1: mod._MIGRATIONS[1], 2: mod._MIGRATIONS[2], 3: "ALTER TABLE receipts ADD COLUMN region TEXT;"},
    )
    ReceiptLedger(db)  # fresh instance must apply v3
    assert _user_version(db) == 3
    ReceiptLedger(db)  # and must not re-apply it
    assert _user_version(db) == 3


def test_insert_failure_records_status_and_reason(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert_failure("f1", "AI error", request_id="r1", user_id=1)
    row = lg.all()[0]
    assert row["status"] == "failed"
    assert row["file_id"] == "f1"
    assert row["failure_reason"] == "AI error"
    assert row["total"] is None  # no known total for a failed receipt


def test_failure_row_does_not_collide_with_accepted(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1"))
    lg.insert_failure("f2", "boom", request_id="r1", user_id=1)
    assert lg.count() == 2
    statuses = {r["status"] for r in lg.all()}
    assert statuses == {"accepted", "failed"}


def test_by_user_returns_only_that_users_rows(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1", user_id=1))
    lg.insert(_entry("f2", user_id=2))
    assert len(lg.by_user(1)) == 1
    assert lg.by_user(1)[0]["file_id"] == "f1"
    assert lg.by_user(99) == []


def test_ledger_write_waits_under_contention(tmp_path):
    # A ledger write against a held SQLite lock must wait (busy_timeout), not
    # fail immediately with "database is locked".
    import sqlite3
    import threading
    import time

    db = tmp_path / "ledger.db"
    lg = ReceiptLedger(db)
    blocker = sqlite3.connect(str(db))
    blocker.execute("BEGIN IMMEDIATE")
    errors = []

    def do_insert():
        try:
            lg.insert(_entry("f1"))
        except sqlite3.OperationalError as exc:
            errors.append(str(exc))

    t = threading.Thread(target=do_insert)
    t.start()
    time.sleep(0.05)  # let the insert hit the lock
    blocker.rollback()  # release the write lock -> waiting writer proceeds
    blocker.close()
    t.join(timeout=5)
    assert errors == []
    assert lg.count() == 1


def test_mark_delivered_sets_delivered_at(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1", request_id="r1"))
    assert lg.by_user(1)[0]["delivered_at"] is None
    lg.mark_delivered("r1")
    assert lg.by_user(1)[0]["delivered_at"] is not None


def test_mark_delivered_skips_failed_rows(tmp_path):
    # A failed receipt must never be recorded as delivered.
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1", request_id="r1"))  # accepted
    lg.insert_failure("f2", "bad", request_id="r1", user_id=1)
    lg.mark_delivered("r1")
    by_status = {r["status"]: r["delivered_at"] for r in lg.all()}
    assert by_status["accepted"] is not None
    assert by_status["failed"] is None


def test_summary_counts(tmp_path):
    lg = ReceiptLedger(tmp_path / "ledger.db")
    lg.insert(_entry("f1", user_id=1))
    lg.insert(_entry("f2", user_id=1))
    lg.insert_failure("f3", "bad", request_id="r9", user_id=1)
    lg.insert(_entry("f4", user_id=1, request_id="r2"))
    lg.mark_delivered("r2")
    s = lg.summary()
    assert s["accepted"] == 3
    assert s["failed"] == 1
    assert s["delivered"] == 1
    assert s["total"] == 4
