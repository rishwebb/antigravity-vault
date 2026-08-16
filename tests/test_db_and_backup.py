"""
Unit tests for Database Operations, SQLite PRAGMA Integrity Checks, SHA256 Backups, and Restore Validation (Issues 32–35).
"""

import unittest
import tempfile
import os
import json
from pathlib import Path

from db import (
    init_db,
    verify_db_integrity,
    insert_token_log,
    record_auth_audit_event,
    get_recent_auth_audits,
    record_system_error_to_db,
    get_recent_db_errors,
)
from backup_engine import (
    create_sqlite_snapshot,
    verify_backup_integrity,
    test_restore_dry_run,
    export_all_time_archive_json,
    compute_file_sha256,
)


class TestDbAndBackup(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_antigravity.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_integrity(self):
        self.assertTrue(verify_db_integrity(self.db_path))

    def test_auth_audit_logging(self):
        record_auth_audit_event("192.168.1.55", "/api/summary", "login_success", "Valid PIN", db_path=self.db_path)
        audits = get_recent_auth_audits(10, db_path=self.db_path)
        self.assertGreaterEqual(len(audits), 1)
        self.assertEqual(audits[0]["client_ip"], "192.168.1.55")
        self.assertEqual(audits[0]["status"], "login_success")

    def test_system_error_logging(self):
        record_system_error_to_db("parser", "Failed to parse json line", "ValueError", "Traceback here...", db_path=self.db_path)
        errors = get_recent_db_errors(10, db_path=self.db_path)
        self.assertGreaterEqual(len(errors), 1)
        self.assertEqual(errors[0]["module"], "parser")
        self.assertEqual(errors[0]["error_type"], "ValueError")

    def test_backup_and_integrity_verification(self):
        # Insert test data
        insert_token_log({
            "session_id": "test_backup_sess",
            "account_id": "acc_1",
            "timestamp": "2026-08-16T10:00:00Z",
            "model_name": "gemini-3.6-flash",
            "thinking_level": "High",
            "prompt_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cost_usd": 0.001,
            "cost_inr": 0.087,
        }, db_path=self.db_path)

        # Snapshot
        snap = create_sqlite_snapshot(self.db_path)
        self.assertIsNotNone(snap)
        self.assertTrue(snap["integrity_verified"])
        self.assertIsNotNone(snap["checksum_sha256"])

        # Test dry-run restore validation
        restore_res = test_restore_dry_run(Path(snap["file_path"]))
        self.assertTrue(restore_res["restorable"])
        self.assertGreaterEqual(restore_res["counts"]["token_logs"], 1)

    def test_json_archive_export(self):
        arch = export_all_time_archive_json(self.db_path)
        self.assertIsNotNone(arch)
        self.assertGreaterEqual(arch["records"], 0)
        self.assertTrue(os.path.exists(arch["path"]))

        # Verify JSON validity
        with open(arch["path"], "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["version"], "2.0")
            self.assertIn("accounts", data)


if __name__ == "__main__":
    unittest.main()
