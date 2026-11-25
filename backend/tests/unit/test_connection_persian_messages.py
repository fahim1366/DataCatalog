from __future__ import annotations

import pytest

from backend.src.api.v1.connections import format_connection_error_persian, sanitize_uri_credentials


def test_format_connection_error_persian_timeout():
    msg_disc = format_connection_error_persian("Database discovery probe timed out after 5.0 seconds.", "discovery")
    assert "مهلت زمانی" in msg_disc
    assert "تست اتصال" in msg_disc

    msg_test = format_connection_error_persian("Connection probe timed out after 5.0 seconds.", "test")
    assert "مهلت زمانی" in msg_test
    assert "تست اتصال" in msg_test


def test_format_connection_error_persian_authentication():
    msg_disc = format_connection_error_persian("password authentication failed for user postgres", "discovery")
    assert "خطا در احراز هویت" in msg_disc
    assert "تست اتصال" in msg_disc

    msg_test = format_connection_error_persian("invalid username or password", "test")
    assert "خطا در احراز هویت" in msg_test
    assert "تست اتصال" in msg_test


def test_format_connection_error_persian_connection_refused():
    msg_disc = format_connection_error_persian("Connection refused by peer (errno 111)", "discovery")
    assert "ارتباط با سرور برقرار نشد" in msg_disc or "ارتباط برقرار نشد" in msg_disc
    assert "تست اتصال" in msg_disc

    msg_test = format_connection_error_persian("could not connect to server: Connection refused", "test")
    assert "ارتباط با سرور برقرار نشد" in msg_test
    assert "تست اتصال" in msg_test


def test_format_connection_error_persian_unreachable_network():
    msg_disc = format_connection_error_persian("getaddrinfo failed: Name or service not known", "discovery")
    assert "آدرس سرور" in msg_disc
    assert "تست اتصال" in msg_disc

    msg_test = format_connection_error_persian("Network is unreachable", "test")
    assert "آدرس سرور" in msg_test
    assert "تست اتصال" in msg_test


def test_format_connection_error_persian_unsupported_engine():
    msg_disc = format_connection_error_persian("Database auto-discovery not yet supported for engine CLICKHOUSE.", "discovery")
    assert "پشتیبانی نمی‌شود" in msg_disc
    assert "تست اتصال" in msg_disc


def test_format_connection_error_persian_database_not_found():
    msg_test = format_connection_error_persian("database 'wrong_db' does not exist", "test")
    assert "پایگاه‌داده" in msg_test
    assert "تست اتصال" in msg_test


def test_format_connection_error_persian_fallback():
    msg_disc = format_connection_error_persian("Unknown dialect error xyz", "discovery")
    assert "خطا در شناسایی پایگاه‌های داده" in msg_disc
    assert "تست اتصال" in msg_disc

    msg_test = format_connection_error_persian("Unknown dialect error xyz", "test")
    assert "خطا در برقراری اتصال به پایگاه‌داده" in msg_test
    assert "تست اتصال" in msg_test


def test_sanitize_uri_credentials():
    mongo_uri = "Failed connecting to mongodb://superadmin:MySecretP@ss123@mongo.prod.corp:27017/admin"
    sanitized = sanitize_uri_credentials(mongo_uri)
    assert "MySecretP@ss123" not in sanitized
    assert "mongodb://superadmin:***@mongo.prod.corp:27017/admin" in sanitized

    pg_uri = "postgresql+asyncpg://app_user:ultra_secure_password@10.0.1.20:5432/finance_db"
    assert "ultra_secure_password" not in sanitize_uri_credentials(pg_uri)
    assert "postgresql+asyncpg://app_user:***@10.0.1.20:5432/finance_db" in sanitize_uri_credentials(pg_uri)


def test_format_connection_error_persian_sanitizes_uri():
    raw = "پکیج درایور مربوط به mongodb://admin:verySecretPassword@host:27017 نصب نیست."
    res = format_connection_error_persian(raw, "discovery")
    assert "verySecretPassword" not in res
    assert "mongodb://admin:***@host:27017" in res

