"""
Unit tests for Pricing Engine and Token Estimator (Issues 8, 9, 10, 24).
Validates model normalization, tiered Pro pricing, thinking budget math, and cached discounts.
"""

import unittest
from pricing_engine import (
    normalize_model_name,
    parse_thinking_level,
    calculate_turn_cost,
    estimate_tokens,
    MODEL_PRICING,
)


class TestPricingEngine(unittest.TestCase):

    def test_model_name_normalization(self):
        self.assertEqual(normalize_model_name("Gemini 3.7 Flash (High)"), "gemini-3.7-flash")
        self.assertEqual(normalize_model_name("Gemini 3.6 Flash (Low)"), "gemini-3.6-flash")
        self.assertEqual(normalize_model_name("Gemini 3.5 Pro (Medium)"), "gemini-3.5-pro")
        self.assertEqual(normalize_model_name("Unknown-Model-xyz"), "gemini-3.6-flash")
        self.assertEqual(normalize_model_name(""), "gemini-3.6-flash")

    def test_thinking_level_parsing(self):
        self.assertEqual(parse_thinking_level("Model (High)"), "High")
        self.assertEqual(parse_thinking_level("Model (Medium)"), "Medium")
        self.assertEqual(parse_thinking_level("Model (Low)"), "Low")
        self.assertEqual(parse_thinking_level("Model without budget"), "None")
        self.assertEqual(parse_thinking_level(""), "None")

    def test_token_estimation(self):
        # Empty string
        toks, conf = estimate_tokens("")
        self.assertEqual(toks, 0)
        self.assertEqual(conf, "heuristic_char")

        # 38 chars should be ~10 tokens
        sample = "a" * 38
        toks, conf = estimate_tokens(sample)
        self.assertEqual(toks, 10)
        self.assertEqual(conf, "heuristic_char")

    def test_gemini_37_flash_pricing(self):
        # 1M prompt ($0.75), 1M output ($3.75), 1M thinking ($3.75), 1M cached ($0.075)
        # Total output = 2M => $7.50
        # Total prompt = 1M => $0.75
        # Total cached = 1M => $0.075
        # Total USD = 0.75 + 7.50 + 0.075 = $8.325
        t_tot, t_out, c_usd, c_inr = calculate_turn_cost(
            model_name="gemini-3.7-flash",
            prompt_tokens=1_000_000,
            cached_tokens=1_000_000,
            output_tokens=1_000_000,
            reasoning_thinking_tokens=1_000_000,
            usd_to_inr=87.0,
        )
        self.assertEqual(t_tot, 4_000_000)
        self.assertEqual(t_out, 2_000_000)
        self.assertAlmostEqual(c_usd, 8.325, places=3)
        self.assertAlmostEqual(c_inr, 8.325 * 87.0, places=2)

    def test_gemini_35_pro_tiered_pricing(self):
        # Test standard tier (<= 200k tokens)
        # 100k prompt ($2.00/1M = $0.20), 10k output ($12.00/1M = $0.12)
        t_tot, t_out, c_usd, _ = calculate_turn_cost(
            model_name="gemini-3.5-pro",
            prompt_tokens=100_000,
            cached_tokens=0,
            output_tokens=10_000,
            reasoning_thinking_tokens=0,
        )
        self.assertAlmostEqual(c_usd, 0.32, places=3)

        # Test large tier (> 200k tokens)
        # 300k prompt ($4.00/1M = $1.20), 10k output ($18.00/1M = $0.18)
        t_tot, t_out, c_usd, _ = calculate_turn_cost(
            model_name="gemini-3.5-pro",
            prompt_tokens=300_000,
            cached_tokens=0,
            output_tokens=10_000,
            reasoning_thinking_tokens=0,
        )
        self.assertAlmostEqual(c_usd, 1.38, places=3)


if __name__ == "__main__":
    unittest.main()
