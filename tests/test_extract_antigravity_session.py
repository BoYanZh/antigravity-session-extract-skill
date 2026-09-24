import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "antigravity-session-extract"
    / "scripts"
    / "extract_antigravity_session.py"
)
SPEC = importlib.util.spec_from_file_location("extract_antigravity_session", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class ExtractAntigravitySessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "antigravity-cli"
        self.project = Path(self.temp.name) / "project"
        self.other_project = Path(self.temp.name) / "other"
        self.project.mkdir()
        self.other_project.mkdir()
        (self.root / "cache").mkdir(parents=True)
        (self.root / "cache" / "last_conversations.json").write_text(
            json.dumps({str(self.project): "conv_good", str(self.other_project): "conv_other"}),
            encoding="utf-8",
        )
        self.write_transcript(
            "conv_good",
            [
                self.record(
                    1,
                    "USER_EXPLICIT",
                    "USER_INPUT",
                    "<USER_REQUEST>fix session loading</USER_REQUEST>"
                    "<ADDITIONAL_METADATA>The current local time is: now.</ADDITIONAL_METADATA>",
                ),
                self.record(
                    2,
                    "USER_EXPLICIT",
                    "USER_INPUT",
                    "<USER_REQUEST>fix session loading</USER_REQUEST>",
                ),
                {
                    "step_index": 3,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "COMPLETED",
                    "created_at": "2026-01-01T00:00:03Z",
                    "tool_calls": {"name": "view_file", "args": {"path": str(self.project / "main.py")}},
                },
                self.record(4, "MODEL", "VIEW_FILE", "file contents"),
                self.record(5, "MODEL", "PLANNER_RESPONSE", "implemented the fix"),
                {
                    "step_index": 51,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "COMPLETED",
                    "created_at": "2026-01-01T00:00:05.100Z",
                    "tool_calls": {
                        "name": "run_command",
                        "args": {"CommandLine": "python other.py", "Cwd": str(self.other_project)},
                    },
                },
                self.record(52, "MODEL", "RUN_COMMAND", "other project result"),
                self.record(6, "USER_EXPLICIT", "USER_INPUT", "<USER_REQUEST>unrelated work</USER_REQUEST>"),
                {
                    "step_index": 7,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "COMPLETED",
                    "created_at": "2026-01-01T00:00:07Z",
                    "tool_calls": {
                        "name": "run_command",
                        "args": {
                            "CommandLine": f"python {self.project / 'main.py'}",
                            "Cwd": str(self.other_project),
                        },
                    },
                },
            ],
        )
        self.write_transcript(
            "conv_other",
            [self.record(1, "USER_EXPLICIT", "USER_INPUT", "<USER_REQUEST>other work</USER_REQUEST>")],
        )
        noisy = self.record(
            9,
            "MODEL",
            "PLANNER_RESPONSE",
            f"unrelated inventory mentions {self.project}",
        )
        noisy["created_at"] = "2026-02-01T00:00:09Z"
        self.write_transcript("conv_noise", [noisy])
        self.write_transcript("conv_empty", [])

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def record(index, source, record_type, content):
        return {
            "step_index": index,
            "source": source,
            "type": record_type,
            "status": "COMPLETED",
            "created_at": f"2026-01-01T00:00:0{index}Z",
            "content": content,
        }

    def write_transcript(self, conversation_id, records):
        transcript = (
            self.root
            / "brain"
            / conversation_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = MODULE.main(["--root", str(self.root), *args])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_list_filters_by_project_and_reports_transcript_status(self):
        exit_code, stdout, stderr = self.run_cli(
            "--project", str(self.project), "--list", "--format", "json"
        )
        self.assertEqual(0, exit_code, stderr)
        sessions = json.loads(stdout)
        self.assertEqual(["conv_good", "conv_noise"], [session["id"] for session in sessions])
        self.assertEqual("nonempty", sessions[0]["transcript_status"])
        self.assertEqual("fix session loading", sessions[0]["title"])

    def test_windows_python_313_path_alias_does_not_change_project_key(self):
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("must stay lexical")):
            key = MODULE.normalized_path(self.project)

        self.assertTrue(key.endswith("/project"), key)

    def test_extract_cleans_and_deduplicates_prompts_and_keeps_tools(self):
        exit_code, stdout, stderr = self.run_cli(
            "--project", str(self.project), "--session", "conv_good", "--format", "json"
        )
        self.assertEqual(0, exit_code, stderr)
        payload = json.loads(stdout)
        self.assertEqual(["fix session loading"], payload["prompts"])
        self.assertEqual(4, len(payload["parts"]))
        tool_part = next(part for part in payload["parts"] if "tool_calls" in part)
        self.assertEqual("view_file", tool_part["tool_calls"]["name"])
        self.assertEqual("implemented the fix", payload["parts"][-1]["text"])
        self.assertNotIn("unrelated work", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("other project result", json.dumps(payload, ensure_ascii=False))

    def test_latest_prefers_exact_workspace_mapping_over_newer_path_mention(self):
        exit_code, stdout, stderr = self.run_cli(
            "--project", str(self.project), "--latest", "--format", "json"
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("conv_good", json.loads(stdout)["session"]["id"])

    def test_empty_transcript_fails_without_database_fallback(self):
        exit_code, stdout, stderr = self.run_cli("--session", "conv_empty", "--format", "json")
        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout)
        self.assertIn("transcript is empty", stderr)
        self.assertIn("database recovery is unsupported", stderr)

    def test_keyword_tail_and_atomic_output_use_public_cli(self):
        output = Path(self.temp.name) / "result.json"
        exit_code, stdout, stderr = self.run_cli(
            "--project",
            str(self.project),
            "--latest",
            "--keyword",
            "implemented",
            "--tail",
            "1",
            "--format",
            "json",
            "--output",
            str(output),
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("", stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(["implemented the fix"], [part["text"] for part in payload["parts"]])


if __name__ == "__main__":
    unittest.main()
