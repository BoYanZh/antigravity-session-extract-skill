#!/usr/bin/env python3
"""Extract Antigravity sessions from readable transcript.jsonl files only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

USER_REQUEST = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL)
PATH_SEPARATORS = re.compile(r"[\\/]+")


class ExtractError(RuntimeError):
    pass


def default_root() -> Path:
    configured = os.environ.get("ANTIGRAVITY_CLI_HOME")
    return Path(configured) if configured else Path.home() / ".gemini" / "antigravity-cli"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List or extract Antigravity sessions from transcript.jsonl files."
    )
    parser.add_argument("--root", type=Path, default=default_root(), help="Antigravity CLI data root")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Project directory")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="List sessions matching the project")
    action.add_argument("--session", help="Extract an exact or uniquely prefixed conversation ID")
    action.add_argument("--latest", action="store_true", help="Extract the latest matching session")
    parser.add_argument("--keyword", action="append", default=[], help="Keep matching transcript parts")
    parser.add_argument("--tail", type=int, default=0, help="Keep only the last N matching parts")
    parser.add_argument(
        "--max-part-chars",
        type=int,
        default=8000,
        help="Maximum characters per content field; 0 disables truncation",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path, help="Write output atomically instead of printing it")
    return parser.parse_args(argv)


def normalized_path(value: str | Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return PATH_SEPARATORS.sub("/", os.path.normcase(str(path))).rstrip("/").casefold()


def normalized_text(value: str) -> str:
    return PATH_SEPARATORS.sub("/", value).casefold()


def nested_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from nested_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_strings(item)


def transcript_path(root: Path, conversation_id: str) -> Path:
    return root / "brain" / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"


def read_mapping(root: Path) -> dict[str, str]:
    path = root / "cache" / "last_conversations.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExtractError(f"Cannot read workspace mapping: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ExtractError(f"Workspace mapping must be a JSON object: {path}")
    return {str(key): str(value) for key, value in payload.items()}


def read_transcript(path: Path, *, require_content: bool) -> list[dict[str, Any]]:
    if not path.is_file():
        if require_content:
            raise ExtractError(
                f"Session transcript is missing: {path}; raw database recovery is unsupported."
            )
        return []
    if path.stat().st_size == 0:
        if require_content:
            raise ExtractError(
                f"Session transcript is empty: {path}; raw database recovery is unsupported."
            )
        return []

    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ExtractError(f"Invalid transcript JSON at {path}:{line_number}: {error}") from error
                if not isinstance(record, dict):
                    raise ExtractError(f"Transcript record must be an object at {path}:{line_number}")
                records.append(record)
    except OSError as error:
        raise ExtractError(f"Cannot read transcript: {path}: {error}") from error

    if require_content and not records:
        raise ExtractError(
            f"Session transcript is empty: {path}; raw database recovery is unsupported."
        )
    return records


def user_prompt(record: dict[str, Any]) -> str:
    content = record.get("content")
    if not isinstance(content, str):
        return ""
    match = USER_REQUEST.search(content)
    return (match.group(1) if match else content).strip()


def transcript_status(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return "empty" if path.stat().st_size == 0 else "nonempty"


def record_mentions_project(record: dict[str, Any], project_key: str) -> bool:
    return any(project_key in normalized_text(value) for value in nested_strings(record))


def tool_project_state(record: dict[str, Any], project_key: str) -> bool | None:
    location_keys = {
        "cwd",
        "directory",
        "file",
        "filepath",
        "path",
        "searchpath",
        "targetfile",
        "workingdirectory",
    }

    def locations(value: Any) -> Iterable[str]:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key.casefold() in location_keys and isinstance(item, str):
                yield item
            if isinstance(item, dict):
                yield from locations(item)
            elif isinstance(item, list):
                for child in item:
                    yield from locations(child)

    calls = record.get("tool_calls")
    if isinstance(calls, dict):
        values = list(locations(calls))
    elif isinstance(calls, list):
        values = [value for call in calls for value in locations(call)]
    else:
        return None
    if not values:
        return None
    for value in values:
        candidate = normalized_text(value.strip().strip("\"'"))
        if candidate == project_key or candidate.startswith(project_key + "/"):
            return True
    return False


def summarize_session(
    root: Path,
    conversation_id: str,
    mapped_ids: set[str],
    project_key: str | None,
) -> dict[str, Any] | None:
    path = transcript_path(root, conversation_id)
    status = transcript_status(path)
    records = read_transcript(path, require_content=False) if status == "nonempty" else []
    mapped = conversation_id in mapped_ids
    mentioned = bool(project_key) and any(record_mentions_project(record, project_key) for record in records)
    if project_key and not mapped and not mentioned:
        return None

    prompts = [user_prompt(record) for record in records if record.get("type") == "USER_INPUT"]
    prompts = [prompt for prompt in prompts if prompt]
    timestamps = [str(record["created_at"]) for record in records if record.get("created_at")]
    modified_ns = path.stat().st_mtime_ns if path.is_file() else 0
    return {
        "id": conversation_id,
        "title": prompts[0] if prompts else f"({status} transcript)",
        "transcript_status": status,
        "record_count": len(records),
        "first_created_at": timestamps[0] if timestamps else None,
        "last_created_at": timestamps[-1] if timestamps else None,
        "mapped_to_project": mapped,
        "mentions_project": mentioned,
        "_sort_key": (1 if mapped else 0, timestamps[-1] if timestamps else "", modified_ns),
    }


def list_sessions(root: Path, project: Path | None) -> list[dict[str, Any]]:
    brain = root / "brain"
    if not brain.is_dir():
        raise ExtractError(f"Antigravity brain directory not found: {brain}")
    mapping = read_mapping(root)
    project_key = normalized_path(project) if project else None
    mapped_ids = {
        conversation_id
        for workspace, conversation_id in mapping.items()
        if project_key and normalized_path(workspace) == project_key
    }
    sessions = [
        summary
        for directory in brain.iterdir()
        if directory.is_dir()
        and (
            summary := summarize_session(root, directory.name, mapped_ids, project_key)
        )
        is not None
    ]
    sessions.sort(key=lambda session: session["_sort_key"], reverse=True)
    for session in sessions:
        session.pop("_sort_key", None)
    return sessions


def select_session(root: Path, project: Path, session_id: str | None) -> dict[str, Any]:
    if session_id:
        sessions = list_sessions(root, None)
        matches = [session for session in sessions if session["id"] == session_id]
        if not matches:
            matches = [session for session in sessions if session["id"].startswith(session_id)]
        if not matches:
            raise ExtractError(f"Antigravity session not found: {session_id}")
        if len(matches) > 1:
            ids = ", ".join(session["id"] for session in matches)
            raise ExtractError(f"Ambiguous session prefix {session_id}: {ids}")
        return matches[0]

    sessions = list_sessions(root, project)
    if not sessions:
        raise ExtractError(f"No Antigravity sessions found for project: {project.resolve(strict=False)}")
    return sessions[0]


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    omitted = len(text) - limit
    return f"{text[:limit]}\n… [truncated {omitted} characters]", True


def project_records(
    records: list[dict[str, Any]], project: Path, session: dict[str, Any]
) -> list[dict[str, Any]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for record in records:
        if record.get("type") == "USER_INPUT" and current:
            turns.append(current)
            current = []
        current.append(record)
    if current:
        turns.append(current)

    project_key = normalized_path(project)
    relevant: list[dict[str, Any]] = []
    for turn in turns:
        prompt = next((record for record in turn if record.get("type") == "USER_INPUT"), None)
        active = bool(prompt and record_mentions_project(prompt, project_key))
        selected: list[dict[str, Any]] = [prompt] if active and prompt else []
        for record in turn:
            if record is prompt:
                continue
            state = tool_project_state(record, project_key)
            if state is not None:
                if state and not selected and prompt:
                    selected.append(prompt)
                active = state
            if active:
                selected.append(record)
        relevant.extend(selected)
    if relevant:
        return relevant
    if session.get("mapped_to_project") and not any(record.get("tool_calls") for record in records):
        return records
    raise ExtractError(
        f"Session {session['id']} has no transcript turns matching project: {project.resolve(strict=False)}"
    )


def extract_session(
    root: Path,
    session: dict[str, Any],
    project: Path,
    keywords: list[str],
    tail: int,
    max_part_chars: int,
) -> dict[str, Any]:
    records = read_transcript(transcript_path(root, session["id"]), require_content=True)
    records = project_records(records, project, session)
    parts: list[dict[str, Any]] = []
    previous_prompt: str | None = None

    for record in records:
        record_type = str(record.get("type", ""))
        content = record.get("content")
        text = content if isinstance(content, str) else ""
        if record_type == "USER_INPUT":
            text = user_prompt(record)
            if not text or text == previous_prompt:
                continue
            previous_prompt = text

        tool_calls = record.get("tool_calls")
        if not text and not tool_calls:
            continue

        part: dict[str, Any] = {
            "step_index": record.get("step_index"),
            "source": record.get("source"),
            "type": record_type,
            "status": record.get("status"),
            "created_at": record.get("created_at"),
        }
        if text:
            part["text"], truncated = truncate_text(text, max_part_chars)
            if truncated:
                part["truncated"] = True
        if tool_calls:
            part["tool_calls"] = tool_calls
        parts.append(part)

    if keywords:
        lowered = [keyword.casefold() for keyword in keywords]
        parts = [
            part
            for part in parts
            if any(keyword in json.dumps(part, ensure_ascii=False).casefold() for keyword in lowered)
        ]
    if tail > 0:
        parts = parts[-tail:]

    prompts = [part["text"] for part in parts if part["type"] == "USER_INPUT" and part.get("text")]
    return {"session": session, "prompts": prompts, "parts": parts}


def render_markdown(payload: dict[str, Any]) -> str:
    session = payload["session"]
    lines = [
        f"# Antigravity session: {session['id']}",
        "",
        f"- Title: {session['title']}",
        f"- Last activity: {session.get('last_created_at') or 'unknown'}",
        f"- Transcript records: {session['record_count']}",
        "",
        "## Timeline",
        "",
    ]
    for part in payload["parts"]:
        label = f"{part.get('source') or 'UNKNOWN'} / {part.get('type') or 'UNKNOWN'}"
        lines.extend([f"### {label}", ""])
        if part.get("text"):
            lines.extend([part["text"], ""])
        if part.get("tool_calls"):
            lines.extend(
                ["```json", json.dumps(part["tool_calls"], ensure_ascii=False, indent=2), "```", ""]
            )
    return "\n".join(lines).rstrip() + "\n"


def serialize(value: Any, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    return render_markdown(value)


def write_output(path: Path, content: str) -> None:
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = args.root.expanduser().resolve(strict=False)
        project = args.project.expanduser()
        if not project.is_absolute():
            project = Path.cwd() / project
        if args.list:
            result: Any = list_sessions(root, project)
            content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        else:
            session = select_session(root, project, args.session)
            result = extract_session(root, session, project, args.keyword, args.tail, args.max_part_chars)
            content = serialize(result, args.format)
        if args.output:
            write_output(args.output, content)
        else:
            print(content, end="")
        return 0
    except ExtractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
