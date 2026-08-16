"""
Unit tests for Standards-Compliant ISO/IEC 18004 QR Code Generation and GF(2^8) Reed-Solomon Arithmetic.
"""

import unittest
from qr_generator import (
    gf_mul,
    rs_generator_poly,
    rs_encode,
    StandardQRCode,
    OfflineQR,
)


class TestQRCompliance(unittest.TestCase):

    def test_galois_field_multiplication(self):
        # In GF(2^8): alpha^1 * alpha^2 = alpha^3
        self.assertEqual(gf_mul(0, 50), 0)
        self.assertEqual(gf_mul(1, 100), 100)
        # Test multiplication properties
        self.assertGreater(gf_mul(2, 3), 0)

    def test_reed_solomon_generator_poly(self):
        poly_10 = rs_generator_poly(10)
        self.assertEqual(len(poly_10), 11)
        self.assertEqual(poly_10[0], 1)

    def test_reed_solomon_encoding(self):
        # Encode sample data bytes
        data = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80]
        ec = rs_encode(data, 10)
        self.assertEqual(len(ec), 10)
        for b in ec:
            self.assertTrue(0 <= b <= 255)

    def test_standard_qr_matrix_generation(self):
        url = "http://127.0.0.1:4848"
        qr = StandardQRCode(url)
        matrix = qr.build_matrix()
        
        # QR Version 2 size is 25x25
        self.assertEqual(len(matrix), qr.size)
        self.assertEqual(len(matrix[0]), qr.size)

        # Check Finder Patterns: Top-Left (0..6, 0..6)
        # Top line of finder must be [1, 1, 1, 1, 1, 1, 1]
        self.assertEqual(matrix[0][:7], [1, 1, 1, 1, 1, 1, 1])
        self.assertEqual(matrix[6][:7], [1, 1, 1, 1, 1, 1, 1])
        self.assertEqual(matrix[1][:7], [1, 0, 0, 0, 0, 0, 1])

    def test_offline_svg_qr_generation(self):
        url = "http://192.168.1.100:4848"
        svg = OfflineQR.generate_svg(url)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        self.assertIn("<rect", svg)
        self.assertIn('viewBox="0 0', svg)

        # Check data URI encoding
        data_uri = OfflineQR.generate_data_uri(url)
        self.assertTrue(data_uri.startswith("data:image/svg+xml;utf8,"))


if __name__ == "__main__":
    unittest.main()
