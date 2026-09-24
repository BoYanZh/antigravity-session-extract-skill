---
name: antigravity-session-extract
description: Extract Antigravity chat transcripts for a project and load prior prompts, responses, and tool activity into the current agent context. Use when asked to find, list, load, continue, recover, or search an Antigravity session for a workspace.
license: MIT
---

# Antigravity Session Extraction

Use the bundled extractor. It treats `brain/<conversation-id>/.system_generated/logs/transcript.jsonl` as the only conversation-content source.

## Run

Resolve this Skill's directory as `$skillRoot`, then run:

```powershell
$extractor = Join-Path $skillRoot 'scripts\extract_antigravity_session.py'

python $extractor --project $PWD --list
python $extractor --project $PWD --latest
python $extractor --project $PWD --session <conversation-id>
```

Useful narrowing options:

- `--keyword TEXT` may be repeated to keep matching parts.
- `--tail N` keeps the last N matching parts.
- `--format json` produces structured output.
- `--max-part-chars 0` disables content truncation when the extra context is necessary.
- `--root PATH` selects a non-default Antigravity CLI data directory.

The default action is latest-session extraction. The script uses `cache/last_conversations.json` only for workspace mapping and also matches project paths found in transcripts. Because one conversation can span several repositories, extraction groups records into user turns and keeps turns that mention the requested project. A mapped conversation with no path-bearing turn is kept whole.

## Load context

Read the extracted output and summarize goals, completed changes, unresolved failures, and the last successful state. Cross-check the repository's current files, `git status`, and recent commits because transcripts can end after an error or empty planner response.

An empty or missing transcript is an explicit unsupported case. Report the session ID and error; raw SQLite/protobuf recovery is outside this Skill's reliability boundary.
