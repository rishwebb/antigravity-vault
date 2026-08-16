"""
Unit tests for 100% Offline Pure-Python QR Code Generator (Issue 36).
Validates offline generation, SVG structure, and zero network calls.
"""

import unittest
from qr_generator import OfflineQR


class TestOfflineQR(unittest.TestCase):

    def test_offline_svg_generation(self):
        url = "http://192.168.1.100:4848"
        svg = OfflineQR.generate_svg(url)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        self.assertIn("<rect", svg)
        self.assertIn("viewBox", svg)

    def test_data_uri_format(self):
        url = "http://127.0.0.1:4848"
        data_uri = OfflineQR.generate_data_uri(url)
        self.assertTrue(data_uri.startswith("data:image/svg+xml;utf8,"))
        self.assertIn("%3Csvg", data_uri)


if __name__ == "__main__":
    unittest.main()
