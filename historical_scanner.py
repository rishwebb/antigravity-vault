"""
Deep Historical Telemetry Crawler for Antigravity & Gemini.
Scans legacy brain folders, globalStorage, workspaceStorage SQLite DBs, and Temp caches.
Idempotently backfills missing past usage with SHA256 turn deduplication and clear inference flags.
"""

import os
import sys
import glob
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Set, Any, Tuple
from datetime import datetime

from config import (
    HISTORICAL_DISCOVERY_ROOTS,
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
    upsert_account,
    upsert_session,
    insert_token_log,
    compute_turn_hash,
    _lock,
)
from telemetry_parser import hash_to_account_id
from logger import get_logger, log_error

logger = get_logger("historical")


def scan_workspace_storage_sqlite(db_path: Path, conn: sqlite3.Connection) -> int:
    """Extract conversation traces, prompt logs, or model usage stored in workspaceStorage state.vscdb."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return 0
    
    turns_found = 0
    try:
        source_conn = sqlite3.connect(str(db_path), timeout=2.0)
        source_cur = source_conn.cursor()
        source_cur.execute("SELECT key, value FROM ItemTable WHERE key LIKE '%antigravity%' OR key LIKE '%gemini%' OR key LIKE '%chat%' OR key LIKE '%conversation%'")
        rows = source_cur.fetchall()
        source_conn.close()

        cur = conn.cursor()
        for key, val in rows:
            val_str = str(val)
            # Check for JSON chat or transcript traces
            if "PLANNER_RESPONSE" in val_str or "USER_INPUT" in val_str or "Model Selection" in val_str:
                json_matches = re.findall(r'\{[^{}]*"type"\s*:\s*"(?:PLANNER_RESPONSE|USER_INPUT)"[^{}]*\}', val_str)
                for j_str in json_matches:
                    try:
                        d = json.loads(j_str)
                        if d.get("type") == "PLANNER_RESPONSE":
                            content = str(d.get("content", ""))
                            out_tok, _ = estimate_tokens(content)
                            if out_tok > 5:
                                session_id = f"legacy_vscdb_{db_path.parent.name[:12]}"
                                timestamp = d.get("created_at") or datetime.utcnow().isoformat() + "Z"
                                acc_id = hash_to_account_id(session_id)
                                model_name = "Gemini 3.6 Flash (High)"
                                think_tok = 8000
                                t_tot, t_out, c_usd, c_inr = calculate_turn_cost(model_name, 2500, 0, out_tok, think_tok)
                                
                                th = compute_turn_hash(session_id, timestamp, 2500, out_tok, model_name, 0, "Legacy workspace session")
                                cur.execute("""
                                INSERT OR IGNORE INTO token_logs (
                                    turn_hash, session_id, account_id, timestamp, model_name, thinking_level,
                                    prompt_tokens, cached_tokens, reasoning_thinking_tokens, output_tokens,
                                    total_tokens, cost_usd, cost_inr, step_index, prompt_preview, metadata_json,
                                    is_estimated, estimation_confidence, data_source, account_attribution_mode
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    th, session_id, acc_id, timestamp, model_name, "High",
                                    2500, 0, think_tok, out_tok, t_tot, c_usd, c_inr, 0,
                                    "Recovered from workspaceStorage", json.dumps({"source": str(db_path)}),
                                    1, "synthetic_infer", "vscdb_trace", "workspace_bucket"
                                ))
                                if cur.rowcount > 0:
                                    turns_found += 1
                    except Exception as e:
                        logger.debug(f"JSON match parsing skipped: {e}")
    except Exception as e:
        logger.debug(f"Could not read state.vscdb {db_path}: {e}")
    return turns_found


def scan_transcript_file_deep(file_path: Path, conn: sqlite3.Connection) -> int:
    """Deep ingestion of a transcript file with full deduplication and explicit metadata."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return 0

    session_id = file_path.parent.parent.name
    if session_id.startswith("."):
        session_id = file_path.parent.parent.parent.name

    lines = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip():
                    lines.append(line)
    except Exception as e:
        log_error("historical", f"Error reading {file_path}", e)
        return 0

    if not lines:
        return 0

    cur = conn.cursor()
    turns_ingested = 0
    current_model = "Gemini 3.6 Flash (High)"
    current_thinking_level = "High"
    current_workspace = str(Path.home() / "Workspace")
    current_account_id = hash_to_account_id(session_id)
    accumulated_context_tokens = 2500
    last_user_prompt = ""
    last_user_time = datetime.utcnow().isoformat() + "Z"

    for line in lines:
        try:
            data = json.loads(line)
        except Exception:
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
            conf_level = "heuristic_char"
            thought_match = re.search(r'<thought>(.*?)</thought>', content, re.DOTALL)
            if thought_match:
                reasoning_tokens, _ = estimate_tokens(thought_match.group(1))
                conf_level = "tag_extracted"
            else:
                base_budget = THINKING_BUDGET_ESTIMATES.get(current_thinking_level, 0)
                if base_budget > 0:
                    tool_count = len(data.get("tool_calls", []))
                    reasoning_tokens = int(base_budget * (1.0 + (tool_count * 0.15)))

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
                json.dumps({"source_file": str(file_path), "workspace": current_workspace}),
                1,
                conf_level,
                "historical_scan",
                "workspace_bucket",
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

            accumulated_context_tokens += output_tokens

        elif step_type in ("VIEW_FILE", "GREP_SEARCH", "RUN_COMMAND", "LIST_DIRECTORY", "CODE_ACTION"):
            tok_count, _ = estimate_tokens(content)
            accumulated_context_tokens += tok_count

    return turns_ingested


def run_deep_historical_scan(verbose: bool = True) -> Dict[str, Any]:
    """
    Executes a comprehensive deep historical crawl across all IDE and system roots.
    Deduplicates every single turn with SHA256 turn_hash.
    """
    start_time = time.time()
    discovered_transcripts: Set[str] = set()
    discovered_sqlite_dbs: Set[str] = set()
    scanned_roots_count = 0

    logger.info("Starting deep historical telemetry scan...")

    for root_dir in HISTORICAL_DISCOVERY_ROOTS:
        if not root_dir.exists():
            continue
        scanned_roots_count += 1
        try:
            # 1. Direct and nested search for transcript files
            for p in root_dir.glob("**/transcript*.jsonl"):
                discovered_transcripts.add(str(p))

            # 2. Search for workspace SQLite state databases
            for db_p in root_dir.glob("**/state.vscdb"):
                discovered_sqlite_dbs.add(str(db_p))

            # 3. Direct brain subfolders
            if "brain" in str(root_dir):
                for child in root_dir.iterdir():
                    if child.is_dir():
                        t1 = child / ".system_generated" / "logs" / "transcript.jsonl"
                        if t1.exists():
                            discovered_transcripts.add(str(t1))
                        t2 = child / "logs" / "transcript.jsonl"
                        if t2.exists():
                            discovered_transcripts.add(str(t2))

        except Exception as e:
            logger.debug(f"Error scanning root {root_dir}: {e}")

    total_recovered_turns = 0
    total_files = len(discovered_transcripts) + len(discovered_sqlite_dbs)

    with _lock:
        conn = get_db_connection()
        for t_file in discovered_transcripts:
            try:
                recovered = scan_transcript_file_deep(Path(t_file), conn)
                total_recovered_turns += recovered
            except Exception as e:
                log_error("historical", f"Error scanning transcript {t_file}", e)

        for db_file in discovered_sqlite_dbs:
            try:
                recovered = scan_workspace_storage_sqlite(Path(db_file), conn)
                total_recovered_turns += recovered
            except Exception as e:
                log_error("historical", f"Error scanning sqlite {db_file}", e)

        conn.commit()

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM token_logs")
        total_stored_turns = cur.fetchone()[0]
        conn.close()

    elapsed = round(time.time() - start_time, 2)
    result = {
        "status": "success",
        "roots_scanned": scanned_roots_count,
        "files_examined": total_files,
        "transcripts_found": len(discovered_transcripts),
        "sqlite_dbs_found": len(discovered_sqlite_dbs),
        "new_turns_recovered": total_recovered_turns,
        "total_stored_turns": total_stored_turns,
        "duration_seconds": elapsed,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    logger.info(f"Historical crawl completed in {elapsed}s: recovered {total_recovered_turns} turns.")
    return result


if __name__ == "__main__":
    res = run_deep_historical_scan(verbose=True)
    print("Result:", res)
