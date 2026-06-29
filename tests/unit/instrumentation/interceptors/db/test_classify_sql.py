"""Tests for _classify_sql helper."""

from __future__ import annotations

import pytest

from openbox.instrumentation.interceptors import db as db_gov


class TestClassifySql:
    def test_select(self):
        assert db_gov._classify_sql("SELECT * FROM users") == "SELECT"

    def test_insert(self):
        assert db_gov._classify_sql("INSERT INTO users VALUES (1)") == "INSERT"

    def test_update(self):
        assert db_gov._classify_sql("UPDATE users SET name='x'") == "UPDATE"

    def test_delete(self):
        assert db_gov._classify_sql("DELETE FROM users") == "DELETE"

    def test_create(self):
        assert db_gov._classify_sql("CREATE TABLE x (id INT)") == "CREATE"

    def test_drop(self):
        assert db_gov._classify_sql("DROP TABLE x") == "DROP"

    def test_unknown_verb(self):
        assert db_gov._classify_sql("LOCK TABLE foo") == "UNKNOWN"

    def test_empty(self):
        assert db_gov._classify_sql("") == "UNKNOWN"

    def test_none(self):
        assert db_gov._classify_sql(None) == "UNKNOWN"

    def test_case_insensitive(self):
        assert db_gov._classify_sql("select * from t") == "SELECT"

    def test_leading_whitespace(self):
        assert db_gov._classify_sql("  \n  SELECT 1") == "SELECT"

    @pytest.mark.parametrize(
        "sql,expected",
        [
            ("WITH cte AS (SELECT 1) INSERT INTO t SELECT * FROM cte", "INSERT"),
            ("WITH a AS (SELECT 1) UPDATE t SET x = 1 FROM a", "UPDATE"),
            ("/* migration 0042 */ CREATE TABLE x (id INT)", "CREATE"),
            ("/* leading */ /* second */ DROP TABLE x", "DROP"),
            ("-- comment\nDELETE FROM users", "DELETE"),
            ("CALL my_sp()", "CALL"),
            ("DO $$ BEGIN INSERT INTO t VALUES (1); END $$", "DO"),
            ("MERGE INTO t USING s ON (t.id = s.id)", "MERGE"),
            ("COPY t TO '/tmp/f.csv'", "COPY"),
            ("COPY t FROM STDIN", "COPY"),
        ],
    )
    def test_extended_classifier_cases(self, sql, expected):
        assert db_gov._classify_sql(sql) == expected
