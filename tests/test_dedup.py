"""
Unit tests for SHA256 Turn Deduplication and Database Idempotency (Issues 20, 25).
"""

import unittest
import tempfile
import os
from pathlib import Path
from db import init_db, insert_token_log, compute_turn_hash, get_db_connection


class TestDeduplication(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db_path = self.temp_db.name
        init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def test_compute_turn_hash_consistency(self):
        h1 = compute_turn_hash("sess_1", "2026-08-16T10:00:00Z", 100, 50, "gemini-3.6-flash", 1, "hello")
        h2 = compute_turn_hash("sess_1", "2026-08-16T10:00:00Z", 100, 50, "gemini-3.6-flash", 1, "hello")
        h3 = compute_turn_hash("sess_1", "2026-08-16T10:00:01Z", 100, 50, "gemini-3.6-flash", 1, "hello")

        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)
        self.assertEqual(len(h1), 64)  # SHA256 hex length

    def test_database_insert_idempotency(self):
        log_entry = {
            "session_id": "test_sess_001",
            "account_id": "acc_1",
            "timestamp": "2026-08-16T12:00:00Z",
            "model_name": "gemini-3.6-flash",
            "thinking_level": "High",
            "prompt_tokens": 500,
            "cached_tokens": 100,
            "reasoning_thinking_tokens": 200,
            "output_tokens": 300,
            "total_tokens": 1100,
            "cost_usd": 0.005,
            "cost_inr": 0.435,
            "step_index": 2,
            "prompt_preview": "Refactor authentication flow",
            "metadata": {"test": True},
        }

        # Insert 5 times
        for _ in range(5):
            insert_token_log(log_entry, db_path=self.db_path)

        conn = get_db_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM token_logs")
        total_count = cur.fetchone()[0]
        conn.close()

        # Must have exactly 1 record, never 5
        self.assertEqual(total_count, 1)


if __name__ == "__main__":
    unittest.main()
