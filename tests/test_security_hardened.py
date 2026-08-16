"""
Unit tests for PBKDF2 PIN Hashing, Persistent SQLite Rate Limiting, Origin Validation, and Auth Hardening.
"""

import unittest
import tempfile
import time
import os
from pathlib import Path

from auth import (
    hash_pin_pbkdf2,
    make_auth_token,
    verify_auth_token,
    verify_pin_login,
    PIN_SALT,
    PIN_HASH,
)
from services import AuthService
from db import (
    init_db,
    record_failed_auth_attempt_db,
    check_ip_lockout_db,
    clear_ip_rate_limit_db,
    get_db_connection,
)


class TestSecurityHardened(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_sec.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pbkdf2_pin_hashing(self):
        salt = "0123456789abcdef0123456789abcdef"
        h1 = hash_pin_pbkdf2("123456", salt)
        h2 = hash_pin_pbkdf2("123456", salt)
        h3 = hash_pin_pbkdf2("654321", salt)

        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)
        self.assertEqual(len(h1), 64)  # 256 bits = 64 hex chars

    def test_persistent_sqlite_rate_limiting(self):
        test_ip = "192.168.1.150"

        # Initially not locked
        locked, _ = check_ip_lockout_db(test_ip, db_path=self.db_path)
        self.assertFalse(locked)

        # 4 failed attempts
        for i in range(1, 5):
            count, is_locked, _ = record_failed_auth_attempt_db(test_ip, max_attempts=5, db_path=self.db_path)
            self.assertEqual(count, i)
            self.assertFalse(is_locked)

        # 5th failed attempt triggers lockout
        count, is_locked, rem = record_failed_auth_attempt_db(test_ip, max_attempts=5, lockout_seconds=300, db_path=self.db_path)
        self.assertEqual(count, 5)
        self.assertTrue(is_locked)
        self.assertGreater(rem, 0)

        # Confirm locked status from database
        locked_now, rem_now = check_ip_lockout_db(test_ip, db_path=self.db_path)
        self.assertTrue(locked_now)
        self.assertGreater(rem_now, 0)

        # Clear rate limit upon successful authentication
        clear_ip_rate_limit_db(test_ip, db_path=self.db_path)
        locked_after, _ = check_ip_lockout_db(test_ip, db_path=self.db_path)
        self.assertFalse(locked_after)

    def test_origin_validation(self):
        # Localhost origins allowed
        self.assertTrue(AuthService.validate_origin("http://localhost:4848", "localhost:4848"))
        self.assertTrue(AuthService.validate_origin("http://127.0.0.1:4848", "127.0.0.1:4848"))
        self.assertTrue(AuthService.validate_origin(None, "127.0.0.1:4848"))

        # Malicious cross-origins rejected
        self.assertFalse(AuthService.validate_origin("http://malicious-site.com", "127.0.0.1:4848"))
        self.assertFalse(AuthService.validate_origin("http://attacker.local", "localhost:4848"))

    def test_token_lifecycle(self):
        token = make_auth_token("hash_123", expiry_seconds=3600)
        self.assertTrue(verify_auth_token(token))

        # Tampered
        tampered = token[:-2] + "00"
        self.assertFalse(verify_auth_token(tampered))


if __name__ == "__main__":
    unittest.main()
