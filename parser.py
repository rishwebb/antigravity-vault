"""
Parser module wrapper - re-exports telemetry_parser
"""
from telemetry_parser import (
    estimate_tokens,
    hash_to_account_id,
    extract_accounts_from_global_storage,
    parse_single_transcript,
    discover_and_sync_all,
)

if __name__ == "__main__":
    from telemetry_parser import discover_and_sync_all
    print("Running parser sync...")
    print(discover_and_sync_all(True))
