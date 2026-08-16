"""
Unit tests for Telemetry Parser, Resumption Offsets, and Resilience (Issues 14, 15, 16, 26).
"""

import unittest
import tempfile
import os
import json
from pathlib import Path

from db import init_db, get_db_connection
from telemetry_parser import parse_single_transcript, estimate_tokens


class TestTelemetryParser(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_parser.db")
        init_db(self.db_path)
        self.conn = get_db_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_parse_valid_transcript(self):
        # Create mock transcript file
        sess_dir = Path(self.temp_dir.name) / "brain" / "sess_test_123" / ".system_generated" / "logs"
        sess_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = sess_dir / "transcript.jsonl"

        lines = [
            json.dumps({
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>Build a fast multi-threaded crawler</USER_REQUEST>\nActive Document: c:\\Users\\Dev\\Projects\\crawler\\main.py",
                "created_at": "2026-08-16T10:00:00Z"
            }),
            json.dumps({
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "<thought>I will plan the architecture with asyncio and thread pools.</thought>Here is the plan for the crawler.",
                "created_at": "2026-08-16T10:00:05Z",
                "tool_calls": []
            }),
        ]

        with open(transcript_file, "w", encoding="utf-8") as f:
            for l in lines:
                f.write(l + "\n")

        turns, tokens, cost = parse_single_transcript(str(transcript_file), self.conn)
        self.conn.commit()

        self.assertEqual(turns, 1)
        self.assertGreater(tokens, 0)
        self.assertGreater(cost, 0.0)

        # Check DB row for thought tag confidence
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM token_logs WHERE session_id = 'sess_test_123'")
        row = dict(cur.fetchone())
        self.assertEqual(row["estimation_confidence"], "tag_extracted")
        self.assertGreater(row["reasoning_thinking_tokens"], 0)

        # Test resumption: calling again on unchanged file should ingest 0 turns
        turns2, _, _ = parse_single_transcript(str(transcript_file), self.conn)
        self.assertEqual(turns2, 0)

    def test_malformed_line_resilience(self):
        sess_dir = Path(self.temp_dir.name) / "brain" / "sess_corrupt" / ".system_generated" / "logs"
        sess_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = sess_dir / "transcript.jsonl"

        # Write corrupted JSON lines mixed with valid
        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write("{corrupt json line here}\n")
            f.write("\n")
            f.write(json.dumps({
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>Hello</USER_REQUEST>",
                "created_at": "2026-08-16T11:00:00Z"
            }) + "\n")
            f.write("{another truncated\n")
            f.write(json.dumps({
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Valid response content.",
                "created_at": "2026-08-16T11:00:02Z"
            }) + "\n")

        # Parser should not crash and should parse valid turns
        turns, tokens, cost = parse_single_transcript(str(transcript_file), self.conn)
        self.conn.commit()
        self.assertEqual(turns, 1)


if __name__ == "__main__":
    unittest.main()
