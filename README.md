# Antigravity Session Extract Skill

An Agent Skill that loads project-scoped Antigravity conversation context from local transcript files.

The extractor is intentionally **transcript-only**. It reads conversation content from:

```text
~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl
```

It may use `cache/last_conversations.json` to map workspaces to conversation IDs, but it never treats that cache, SQLite databases, or protobuf records as conversation content. Missing or empty transcripts fail explicitly instead of silently returning an unreliable reconstruction.

## Install

Install globally for Codex:

```powershell
gh skill install BoYanZh/antigravity-session-extract-skill antigravity-session-extract --agent codex --scope user
```

Install globally for OpenCode:

```powershell
gh skill install BoYanZh/antigravity-session-extract-skill antigravity-session-extract --agent opencode --scope user
```

For a reproducible project install, pin a release:

```powershell
gh skill install BoYanZh/antigravity-session-extract-skill antigravity-session-extract --agent opencode --scope project --pin v1.0.0
```

Use one installation scope for each agent and skill ID to avoid duplicate discovery. A Codex user install under `.agents/skills` may also be visible to OpenCode, depending on its configured skill directories.

## Use

Agents invoke the bundled script according to [`SKILL.md`](skills/antigravity-session-extract/SKILL.md). You can also run it directly:

```powershell
$extractor = 'skills/antigravity-session-extract/scripts/extract_antigravity_session.py'

python $extractor --project $PWD --list
python $extractor --project $PWD --latest
python $extractor --project $PWD --session <conversation-id>
python $extractor --project $PWD --latest --keyword updater --tail 20
python $extractor --project $PWD --latest --format json --output session.json
```

Run `python $extractor --help` for all options.

## Project scoping

Antigravity conversations can operate across multiple repositories. The extractor groups transcript records by user turn and uses tool locations such as `Cwd`, `TargetFile`, and `SearchPath` to retain turns that actively operated in the requested project. A command that merely mentions another repository does not move the turn into that repository.

The result is context recovery, not proof that every described change reached disk. Always compare extracted context with the current worktree, logs, and tests.

## Privacy

Transcripts can contain prompts, responses, file paths, commands, and tool output. Review generated Markdown or JSON before sharing it. The extractor performs local reads only and does not upload transcript data.

## Development

Python 3.10 or newer is required. The runtime uses only the Python standard library.

```powershell
python -m unittest discover -s tests -v
python -m py_compile skills/antigravity-session-extract/scripts/extract_antigravity_session.py
ruff check .
```

## License

[MIT](LICENSE)
