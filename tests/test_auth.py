"""
Unit tests for Authentication, HMAC Token Signing, Rate Limiting, and Authorization Checks (Issues 1, 2, 3, 5, 27).
"""

import unittest
import time
from auth import (
    make_auth_token,
    verify_auth_token,
    verify_pin_login,
    is_ip_locked_out,
    clear_failed_attempts,
    ACTIVE_PIN,
    is_authorized_request,
)


class MockHandler:
    """Mock HTTP request handler for testing authorization middleware."""
    def __init__(self, client_ip: str, headers: dict = None):
        self.client_address = (client_ip, 12345)
        self.headers = headers or {}


class TestAuth(unittest.TestCase):

    def setUp(self):
        clear_failed_attempts("192.168.1.50")
        clear_failed_attempts("10.0.0.99")

    def test_token_creation_and_verification(self):
        token = make_auth_token(ACTIVE_PIN, expiry_seconds=3600)
        self.assertTrue(verify_auth_token(token))

        # Tampered token
        tampered = token[:-4] + "abcd"
        self.assertFalse(verify_auth_token(tampered))

        # Expired token
        expired_token = make_auth_token(ACTIVE_PIN, expiry_seconds=-10)
        self.assertFalse(verify_auth_token(expired_token))

    def test_pin_verification_success(self):
        valid, msg, token = verify_pin_login(ACTIVE_PIN, "192.168.1.50")
        self.assertTrue(valid)
        self.assertIsNotNone(token)
        self.assertTrue(verify_auth_token(token))

    def test_brute_force_rate_limiting(self):
        ip = "10.0.0.99"
        # 5 wrong attempts
        for _ in range(5):
            valid, msg, token = verify_pin_login("000000", ip)
            self.assertFalse(valid)
            self.assertIsNone(token)

        # 6th attempt should be locked out even if correct PIN is provided
        locked, rem = is_ip_locked_out(ip)
        self.assertTrue(locked)
        self.assertGreater(rem, 0)

        valid, msg, token = verify_pin_login(ACTIVE_PIN, ip)
        self.assertFalse(valid)
        self.assertIn("Locked out", msg)

        # Clear and retry
        clear_failed_attempts(ip)
        valid, msg, token = verify_pin_login(ACTIVE_PIN, ip)
        self.assertTrue(valid)

    def test_is_authorized_request(self):
        # 1. Localhost automatically authorized
        h_local = MockHandler("127.0.0.1")
        self.assertTrue(is_authorized_request(h_local))

        h_local6 = MockHandler("::1")
        self.assertTrue(is_authorized_request(h_local6))

        # 2. Remote without token rejected
        h_remote_unauth = MockHandler("192.168.1.100")
        self.assertFalse(is_authorized_request(h_remote_unauth))

        # 3. Remote with valid header token accepted
        token = make_auth_token(ACTIVE_PIN)
        h_remote_header = MockHandler("192.168.1.100", headers={"X-Access-Token": token})
        self.assertTrue(is_authorized_request(h_remote_header))

        # 4. Remote with valid cookie accepted
        h_remote_cookie = MockHandler("192.168.1.100", headers={"Cookie": f"other=123; antigravity_token={token}; path=/"})
        self.assertTrue(is_authorized_request(h_remote_cookie))


if __name__ == "__main__":
    unittest.main()
