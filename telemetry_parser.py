"""
Telemetry and Thinking Token Parser for Antigravity & Gemini 3.x series.
High performance, non-blocking, batched ingestion with SHA256 Turn Deduplication
and explicit metadata distinction for estimated vs observed metrics.
"""

import os
import glob
import json
import re
import hashlib
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from config import (
    DISCOVERY_PATHS,
    DEFAULT_ACCOUNTS,
    DATABASE_PATH,
)
from pricing_engine import (
    normalize_model_name,
    parse_thinking_level,
    calculate_turn_cost,
    estimate_tokens,
    THINKING_BUDGET_ESTIMATES,
)
from db import (
    get_db_connection,
    get_sync_state,
    update_sync_state,
    upsert_account,
    compute_turn_hash,
    _lock,
)
from logger import get_logger, log_error

logger = get_logger("parser")


def hash_to_account_id(identifier: str) -> str:
    """Deterministically map a workspace path, profile, or session to one of 5 account buckets (acc_1 to acc_5)."""
    if not identifier:
        return "acc_1"
    h = int(hashlib.md5(identifier.encode("utf-8")).hexdigest(), 16)
    idx = (h % 5) + 1
    return f"acc_{idx}"


def extract_accounts_from_global_storage() -> List[Dict[str, Any]]:
    """Quickly scan state.vscdb for user emails and profile keys."""
    found_accounts = []
    vscdb_paths = [
        Path.home() / "AppData" / "Roaming" / "Antigravity IDE" / "User" / "globalStorage" / "state.vscdb",
        Path.home() / "AppData" / "Roaming" / "Antigravity" / "User" / "globalStorage" / "state.vscdb",
    ]

    for p in vscdb_paths:
        if not p.exists():
            continue
        try:
            import sqlite3
            conn = sqlite3.connect(str(p), timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM ItemTable WHERE key IN ('antigravityUnifiedStateSync.userStatus', 'antigravity.profileUrl', 'antigravityUnifiedStateSync.oauthToken')")
            for row in cur.fetchall():
                key, val = row[0], str(row[1])
                emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', val)
                for email in emails:
                    if "example" not in email and "schema" not in email:
                        found_accounts.append({"email": email, "source_key": key})
            conn.close()
        except Exception as e:
            logger.debug(f"Non-critical scan notice for {p}: {e}")

    return found_accounts


def parse_single_transcript(file_path: str, conn: Any) -> Tuple[int, int, float]:
    """
    Parses a single transcript.jsonl file with byte-offset resumption, SHA256 deduplication,
    and explicit estimation metadata tagging.
    Returns: (turns_ingested, total_tokens, cost_usd)
    """
    path_obj = Path(file_path)
    if not path_obj.exists():
        return (0, 0, 0.0)

    session_id = path_obj.parent.parent.name
    if session_id.startswith("."):
        session_id = path_obj.parent.parent.parent.name

    cur = conn.cursor()
    cur.execute("SELECT last_byte_offset, last_mtime FROM sync_state WHERE file_path = ?", (file_path,))
    sync_row = cur.fetchone()
    last_offset = sync_row[0] if sync_row else 0
    last_mtime = sync_row[1] if sync_row else 0

    try:
        curr_size = path_obj.stat().st_size
        curr_mtime = path_obj.stat().st_mtime
    except Exception as e:
        log_error("parser", f"Could not stat transcript file {file_path}", e)
        return (0, 0, 0.0)

    if sync_row and curr_size == last_offset and curr_mtime <= last_mtime:
        return (0, 0, 0.0)

    lines = []
    new_offset = last_offset
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            if last_offset > 0:
                f.seek(last_offset)
            while True:
                line = f.readline()
                if not line:
                    break
                if line.strip():
                    lines.append(line)
            new_offset = f.tell()
    except Exception as e:
        log_error("parser", f"Error reading transcript {file_path}", e)
        return (0, 0, 0.0)

    if not lines:
        return (0, 0, 0.0)

    turns_ingested = 0
    total_tokens_all = 0
    total_cost_all = 0.0

    current_model = "Gemini 3.6 Flash (High)"
    current_thinking_level = "High"
    current_workspace = str(Path.home() / "Workspace")
    current_account_id = hash_to_account_id(session_id)
    attribution_mode = "workspace_bucket"
    accumulated_context_tokens = 2500

    last_user_prompt = ""
    last_user_time = datetime.utcnow().isoformat() + "Z"

    for line in lines:
        try:
            data = json.loads(line)
        except Exception as e:
            logger.debug(f"Skipping malformed json line in {file_path}: {e}")
            continue

        step_idx = data.get("step_index", 0)
        source = data.get("source", "")
        step_type = data.get("type", "")
        created_at = data.get("created_at") or last_user_time
        content = str(data.get("content", ""))

        if step_type == "USER_INPUT" or source == "USER_EXPLICIT":
            last_user_time = created_at
            req_match = re.search(r'<USER_REQUEST>(.*?)</USER_REQUEST>', content, re.DOTALL)
            if req_match:
                last_user_prompt = req_match.group(1).strip()[:200]
            else:
                last_user_prompt = content[:200].replace("\n", " ")

            ws_match = re.search(r'Active Document:\s*([^\r\n]+)', content)
            if ws_match:
                doc_path = ws_match.group(1).strip()
                current_workspace = str(Path(doc_path).parent)
                current_account_id = hash_to_account_id(current_workspace)

            model_match = re.search(r'Model Selection` from \S+ to (Gemini [^\n<]+?)(?:\. |\n|<|$)', content)
            if model_match:
                raw_m = model_match.group(1).strip()
                current_model = raw_m
                current_thinking_level = parse_thinking_level(raw_m)
            elif "gemini 3.7" in content.lower():
                current_model = "Gemini 3.7 Flash (High)"
                current_thinking_level = parse_thinking_level(content)
            elif "gemini 3.6" in content.lower():
                current_model = "Gemini 3.6 Flash (High)"
                current_thinking_level = parse_thinking_level(content)
            elif "gemini 3.5" in content.lower():
                current_model = "Gemini 3.5 Pro (Medium)"
                current_thinking_level = parse_thinking_level(content)

            tok_count, _ = estimate_tokens(content)
            accumulated_context_tokens += tok_count

        elif source == "SYSTEM":
            tok_count, _ = estimate_tokens(content)
            accumulated_context_tokens += tok_count

        elif step_type == "PLANNER_RESPONSE" or source == "MODEL":
            output_tokens, _ = estimate_tokens(content)
            for tc in data.get("tool_calls", []):
                args_str = json.dumps(tc.get("args", {}))
                tc_tok, _ = estimate_tokens(args_str)
                output_tokens += tc_tok

            reasoning_tokens = 0
            estimation_conf = "heuristic_char"
            thought_match = re.search(r'<thought>(.*?)</thought>', content, re.DOTALL)
            if thought_match:
                reasoning_tokens, _ = estimate_tokens(thought_match.group(1))
                estimation_conf = "tag_extracted"
            else:
                base_budget = THINKING_BUDGET_ESTIMATES.get(current_thinking_level, 0)
                if base_budget > 0:
                    tool_count = len(data.get("tool_calls", []))
                    reasoning_tokens = int(base_budget * (1.0 + (tool_count * 0.15)))
                estimation_conf = "heuristic_char"

            prompt_tokens = accumulated_context_tokens
            if prompt_tokens > 4000:
                cached_tokens = int(prompt_tokens * 0.65)
                active_prompt_tokens = prompt_tokens - cached_tokens
            else:
                cached_tokens = 0
                active_prompt_tokens = prompt_tokens

            t_tot, t_out, cost_usd, cost_inr = calculate_turn_cost(
                model_name=current_model,
                prompt_tokens=active_prompt_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                reasoning_thinking_tokens=reasoning_tokens,
            )

            turn_hash = compute_turn_hash(
                session_id=session_id,
                timestamp=created_at,
                prompt_tokens=active_prompt_tokens,
                output_tokens=output_tokens,
                model_name=current_model,
                step_index=step_idx,
                prompt_preview=last_user_prompt or "Agent task execution step",
            )

            # Insert into database with explicit confidence and provenance
            cur.execute("""
            INSERT OR IGNORE INTO token_logs (
                turn_hash, session_id, account_id, timestamp, model_name, thinking_level,
                prompt_tokens, cached_tokens, reasoning_thinking_tokens, output_tokens,
                total_tokens, cost_usd, cost_inr, step_index, prompt_preview, metadata_json,
                is_estimated, estimation_confidence, data_source, account_attribution_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                turn_hash,
                session_id,
                current_account_id,
                created_at,
                current_model,
                current_thinking_level,
                active_prompt_tokens,
                cached_tokens,
                reasoning_tokens,
                output_tokens,
                t_tot,
                cost_usd,
                cost_inr,
                step_idx,
                last_user_prompt or "Agent task execution step",
                json.dumps({"source_file": file_path, "workspace": current_workspace}),
                1,
                estimation_conf,
                "live_transcript",
                attribution_mode,
            ))

            if cur.rowcount > 0:
                turns_ingested += 1

            cur.execute("""
            INSERT INTO sessions (session_id, account_id, model_name, thinking_level, workspace_path, timestamp, turn_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(session_id) DO UPDATE SET
                model_name = excluded.model_name,
                thinking_level = excluded.thinking_level,
                turn_count = sessions.turn_count + 1
            """, (session_id, current_account_id, current_model, current_thinking_level, current_workspace, created_at))

            total_tokens_all += t_tot
            total_cost_all += cost_usd
            accumulated_context_tokens += output_tokens

        elif step_type in ("VIEW_FILE", "GREP_SEARCH", "RUN_COMMAND", "LIST_DIRECTORY", "CODE_ACTION"):
            tok_count, _ = estimate_tokens(content)
            accumulated_context_tokens += tok_count

    now_iso = datetime.utcnow().isoformat() + "Z"
    cur.execute("""
    INSERT INTO sync_state (file_path, file_hash, last_byte_offset, last_mtime, last_synced_at, last_error)
    VALUES (?, ?, ?, ?, ?, NULL)
    ON CONFLICT(file_path) DO UPDATE SET
        file_hash = excluded.file_hash,
        last_byte_offset = excluded.last_byte_offset,
        last_mtime = excluded.last_mtime,
        last_synced_at = excluded.last_synced_at,
        last_error = NULL
    """, (
        file_path,
        hashlib.md5(f"{curr_size}_{curr_mtime}".encode("utf-8")).hexdigest(),
        new_offset,
        curr_mtime,
        now_iso,
    ))

    return (turns_ingested, total_tokens_all, total_cost_all)


def discover_and_sync_all(verbose: bool = False) -> Dict[str, Any]:
    """Scan discovery paths and parse all telemetry across 5 accounts efficiently."""
    total_files = 0
    total_turns_ingested = 0
    discovered_transcripts = []
    failed_files = 0

    # 1. Update detected accounts from global storage
    try:
        extracted = extract_accounts_from_global_storage()
        for item in extracted:
            email = item["email"]
            acc_id = hash_to_account_id(email)
            alias = f"{acc_id.upper()} ({email.split('@')[0]})"
            upsert_account(
                account_id=acc_id,
                alias=alias,
                email=email,
            )
    except Exception as e:
        log_error("parser", "Account extraction error from storage", e)

    # 2. Fast scan for transcript.jsonl
    for base_p in DISCOVERY_PATHS:
        if not base_p.exists():
            continue
        try:
            for child in base_p.iterdir():
                if child.is_dir():
                    t_log = child / ".system_generated" / "logs" / "transcript.jsonl"
                    if t_log.exists():
                        discovered_transcripts.append(str(t_log))
                    else:
                        t_log2 = child / "logs" / "transcript.jsonl"
                        if t_log2.exists():
                            discovered_transcripts.append(str(t_log2))
        except Exception as e:
            logger.debug(f"Could not iterate discovery dir {base_p}: {e}")

    total_files = len(discovered_transcripts)
    
    with _lock:
        conn = get_db_connection()
        for t_file in discovered_transcripts:
            try:
                turns, _, _ = parse_single_transcript(t_file, conn)
                total_turns_ingested += turns
            except Exception as e:
                failed_files += 1
                log_error("parser", f"Error parsing {t_file}", e)
        conn.commit()
        conn.close()

    logger.info(f"Sync complete: {total_files} files checked, {total_turns_ingested} turns ingested, {failed_files} failures.")

    return {
        "files_scanned": total_files,
        "turns_ingested": total_turns_ingested,
        "failed_files": failed_files,
        "synced_at": datetime.utcnow().isoformat() + "Z",
    }


if __name__ == "__main__":
    print("Testing telemetry_parser.py with SHA256 turn deduplication...")
    res = discover_and_sync_all(verbose=True)
    print("Sync results:", res)
