# Phase 4: Memory System + Hooks

## Goal
Wire up the memory system so Claude actually reads/writes MEMORY.md and daily logs across sessions.

## Step 4.1 — Create .claude/settings.json with hooks

Add a SessionStart hook that injects memory context:
```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "bash -c 'echo \"=== LONG-TERM MEMORY ===\"; cat memory/MEMORY.md 2>/dev/null || echo \"(no long-term memory yet)\"; echo; echo \"=== TODAY MEMORY ===\"; cat memory/$(date +%Y-%m-%d).md 2>/dev/null || echo \"(no entries today yet)\"'"
      }]
    }]
  }
}
```

## Step 4.2 — Create initial memory/MEMORY.md

A starter file with structure:
```markdown
# Long-Term Memory

> Updated by Claude when significant events happen.
> Read at the start of every session.

## User

(Filled after bootstrap)

## Preferences

(Filled as learned)

## Key Decisions

(Filled over time)
```

## Step 4.3 — Update CLAUDE.md memory section

Clarify that memory hooks auto-inject context. Claude should:
- Write to memory/MEMORY.md when something is worth keeping long-term
- Create/append to memory/YYYY-MM-DD.md for daily notes
- Not over-remember — only significant things

## Files to Change
- `.claude/settings.json` — add hooks
- `memory/MEMORY.md` — create starter template
- `CLAUDE.md` — refine memory instructions
