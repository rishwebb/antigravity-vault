"""
Pricing and Token Estimation Engine for Antigravity & Gemini 3.x series.
Provides model rate tiers, character-length heuristic estimators with confidence levels,
and tiered billing calculations for Flash & Pro models.
"""

from typing import Tuple, Dict, Any

# Dynamic Currency & Forex default
DEFAULT_USD_TO_INR = 87.00

# Pricing Engine: Official rates per 1,000,000 tokens (1M tokens)
# Note: Thinking / Reasoning tokens are billed as output tokens.
MODEL_PRICING = {
    # Gemini 3.7 Flash
    "gemini-3.7-flash": {
        "name": "Gemini 3.7 Flash",
        "family": "flash",
        "input_per_million": 0.75,
        "output_per_million": 3.75,
        "cached_per_million": 0.075,
    },
    # Gemini 3.6 Flash (primary workhorse)
    "gemini-3.6-flash": {
        "name": "Gemini 3.6 Flash",
        "family": "flash",
        "input_per_million": 0.75,
        "output_per_million": 3.75,
        "cached_per_million": 0.075,
    },
    # Gemini 3.5 Pro (tiered pricing <= 200k vs > 200k)
    "gemini-3.5-pro": {
        "name": "Gemini 3.5 Pro",
        "family": "pro",
        "input_per_million_standard": 2.00,
        "input_per_million_large": 4.00,
        "output_per_million_standard": 12.00,
        "output_per_million_large": 18.00,
        "cached_per_million": 0.20,
    },
    # Fallback Flash tier
    "fallback-flash": {
        "name": "Gemini Flash (Standard)",
        "family": "flash",
        "input_per_million": 0.75,
        "output_per_million": 3.75,
        "cached_per_million": 0.075,
    },
    # Fallback Pro tier
    "fallback-pro": {
        "name": "Gemini Pro (Standard)",
        "family": "pro",
        "input_per_million_standard": 2.00,
        "input_per_million_large": 4.00,
        "output_per_million_standard": 12.00,
        "output_per_million_large": 18.00,
        "cached_per_million": 0.20,
    },
}

# Thinking Token Budget Heuristics (Fallback budgets when tags are not present)
THINKING_BUDGET_ESTIMATES = {
    "None": 0,
    "Low": 1000,
    "Medium": 3000,
    "High": 8000,
}


def estimate_tokens(text: str) -> Tuple[int, str]:
    """
    Token estimator with estimation method indicator.
    Returns: (token_count, estimation_confidence)
    Estimation confidence: 'exact' (if tokenizer available) or 'heuristic_char' (~3.8 chars/tok).
    """
    if not text:
        return (0, "heuristic_char")

    # Fast character-length heuristic (~3.8 chars per token for code/text)
    tok = max(1, int(len(text) / 3.8))
    return (tok, "heuristic_char")


def normalize_model_name(raw_name: str) -> str:
    """Normalize raw model string from telemetry to standard identifier."""
    if not raw_name:
        return "gemini-3.6-flash"
    raw_lower = str(raw_name).lower().strip()

    if "3.7" in raw_lower:
        if "pro" in raw_lower:
            return "gemini-3.5-pro"
        return "gemini-3.7-flash"
    elif "3.6" in raw_lower:
        if "pro" in raw_lower:
            return "gemini-3.5-pro"
        return "gemini-3.6-flash"
    elif "3.5" in raw_lower:
        if "flash" in raw_lower:
            return "gemini-3.6-flash"
        return "gemini-3.5-pro"
    elif "pro" in raw_lower:
        return "gemini-3.5-pro"
    elif "flash" in raw_lower:
        return "gemini-3.6-flash"
    return "gemini-3.6-flash"


def parse_thinking_level(raw_string: str) -> str:
    """Extract Low, Medium, High, or None from settings change or model string."""
    if not raw_string:
        return "None"
    s = str(raw_string).lower()
    if "(high)" in s or "high" in s:
        return "High"
    if "(medium)" in s or "medium" in s or "med" in s:
        return "Medium"
    if "(low)" in s or "low" in s:
        return "Low"
    return "None"


def calculate_turn_cost(
    model_name: str,
    prompt_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    reasoning_thinking_tokens: int,
    usd_to_inr: float = DEFAULT_USD_TO_INR,
) -> Tuple[int, int, float, float]:
    """
    Calculates estimated turn cost according to official Gemini pricing rules.
    1. Turn Output Tokens = Standard Output Tokens + Thinking/Reasoning Tokens
    2. Turn Cost (USD) = (Input * InRate) + (Turn Output * OutRate) + (Cached * CacheRate)
    3. Turn Cost (INR) = Turn Cost (USD) * usd_to_inr
    Returns: (total_tokens, total_output_tokens, cost_usd, cost_inr)
    """
    model_key = normalize_model_name(model_name)
    pricing = MODEL_PRICING.get(model_key, MODEL_PRICING["fallback-flash"])

    total_output = output_tokens + reasoning_thinking_tokens
    total_tokens = prompt_tokens + cached_tokens + total_output

    if pricing["family"] == "pro":
        if prompt_tokens > 200_000:
            in_rate = pricing["input_per_million_large"] / 1_000_000.0
            out_rate = pricing["output_per_million_large"] / 1_000_000.0
        else:
            in_rate = pricing["input_per_million_standard"] / 1_000_000.0
            out_rate = pricing["output_per_million_standard"] / 1_000_000.0
        cache_rate = pricing["cached_per_million"] / 1_000_000.0
    else:
        in_rate = pricing["input_per_million"] / 1_000_000.0
        out_rate = pricing["output_per_million"] / 1_000_000.0
        cache_rate = pricing["cached_per_million"] / 1_000_000.0

    cost_usd = (prompt_tokens * in_rate) + (total_output * out_rate) + (cached_tokens * cache_rate)
    cost_inr = cost_usd * usd_to_inr

    return (total_tokens, total_output, round(cost_usd, 6), round(cost_inr, 4))
