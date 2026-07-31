# AGENTS.md - Grok Register Panel

> Codex, Grok Build, Claude Code, and other coding agents use this file as the
> stable project contract. Volatile production facts belong in
> `docs/CURRENT_STATE.md`, not here.

## Project

- **Name:** Grok Register Panel
- **What it is:** A Python/Camoufox registration control plane that produces
  xAI-compatible auth records and is being integrated with the existing OVH AI
  Stack data plane.
- **Owner:** Repository operator. Do not infer a personal name from local paths.
- **Stage:** Production canary and controlled migration.
- **Primary language:** Communicate with the operator in Chinese unless asked
  otherwise.
- **Operator context:** See `docs/about-me.md`.

## Session Bootstrap

Before giving current-status answers or changing code:

1. Read `docs/CURRENT_STATE.md` for the latest verified snapshot and priorities.
2. Read `docs/OPERATIONS_ARCHITECTURE.md` and
   `docs/CONVERGENCE_AND_GAPS.md` before architectural or production work.
3. Run `git status --short --branch` and preserve unrelated local changes.
4. Recheck live OVH state for drift-prone claims. A service being `active` does
   not prove that a registration batch is running or that a credential works.
5. Never print access tokens, refresh tokens, mailbox credentials, proxy URLs,
   management keys, or raw auth JSON.

When a session changes current facts, decisions, or a recurring failure mode,
update the matching source before ending:

- Current deployment and next actions: `docs/CURRENT_STATE.md`
- Durable decisions: `docs/decision-log.md`
- Symptom/root-cause/fix patterns: `docs/common-gotchas.md`

## Sources Of Truth

| Concern | Source |
|---|---|
| Current production snapshot and priorities | `docs/CURRENT_STATE.md` |
| Deployed architecture and registration flow | `docs/OPERATIONS_ARCHITECTURE.md` |
| Convergence gates and known gaps | `docs/CONVERGENCE_AND_GAPS.md` |
| Deployment and rollback | `DEPLOYMENT.md` |
| Release checks | `RELEASE_CHECKLIST.md` |
| Decisions | `docs/decision-log.md` |
| Recurring incidents | `docs/common-gotchas.md` |
| Trellis wiring and upgrades | `docs/TRELLIS.md` |

## Technology And Runtime

| Layer | Technology |
|---|---|
| Registration worker | Python 3.11+, Camoufox, Xvfb |
| Control panel | Python HTTP server under systemd, private Docker bridge only; public HTTPS through Cloudflare Tunnel |
| Email | Cloudflare-managed mailbox integration |
| Auth output | Private `cpa_auth/*.json`, mode 0600 |
| Existing data plane | API gateway, New API, CLIProxyAPI, PostgreSQL, Redis |
| Production host | OVH VPS, project at `/opt/grok-register-panel` |
| Tests | Standalone Python tests plus compile and shell checks |

## Common Commands

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PYTHON_BIN=.venv/bin/python bash scripts/run_tests.sh

# Local monitor. Keep the token in an ignored environment file or shell.
MONITOR_HOST=127.0.0.1 MONITOR_PORT=18080 \
  .venv/bin/python webui/monitor.py
```

## Repository Map

```text
.
├── webui/                         # monitor API, process and recovery control
├── email_providers/               # mailbox provider adapters
├── tests/                         # focused regression tests
├── scripts/                       # tests, smoke tests and runtime hardening
├── deploy/                        # systemd and environment examples
├── docs/                          # architecture, live state and decisions
├── accounts/ cpa_auth/ log/       # ignored runtime state; never commit secrets
├── .grok/                         # Grok Build adapter and hooks
├── .claude/                       # canonical shared hooks/commands/skills
├── brain/                         # local searchable project corpus
└── bin/                           # Trellis hook adapters and verification gate
```

## Engineering Rules

### Production Safety

- Keep registration at one batch, one worker, one account until a new change
  passes a controlled canary.
- Back up affected remote files before production edits and state the rollback.
- Do not stop or remove the legacy replenishment stack until the new auth bridge,
  CLIProxy hot load, exact per-auth probe, and public data-plane probe all pass.
- Treat `403`, `407`, provider denial, pool sync failure, and data-plane failure as
  separate failure domains. Retry only errors classified as transient.
- A written auth file is `token_written`, not final success. Only the full path
  through provider verification, pool load, and public API verification is
  `verified`.
- Never use a generic API `200` to prove that a newly generated credential works;
  an older healthy credential may have served the request.

### Code And Verification

- Read the exact code being changed before editing it.
- Prefer the repository's existing patterns and narrowly scoped fixes.
- Add focused regression tests for behavioral changes.
- Run `PYTHON_BIN=.venv/bin/python bash scripts/run_tests.sh` before reporting
  completion. Create the documented Python 3.11 virtual environment first.
- For production-facing changes, also verify the remote process, loopback health,
  authenticated API behavior, and one real end-to-end canary as applicable.
- Do not commit or push unless the operator explicitly asks.

### AI Session Discipline

- Use clean subagents for broad searches, noisy logs, and independent audits;
  keep final judgment and exact code edits in the main session.
- When work stops midstream, update `docs/CURRENT_STATE.md` with completed,
  blocked, and next actions so a new Codex or Grok session can resume directly.
- Search the local Trellis brain for historical context when useful:
  `bash brain/bin/qmd search "<query>"`.
- New facts must supersede stale statements in the same session. Do not append a
  contradictory snapshot without updating the old one.

## Harness Wiring

| Harness | Wiring |
|---|---|
| Codex | Reads this `AGENTS.md` natively |
| Grok Build | `.grok/config.toml` plus shared hooks in `.grok/hooks/hooks.json` |
| Claude Code | `CLAUDE.md` plus `.claude/settings.json` |

Grok reuses the canonical `.claude/` commands, skills, and hooks through
`bin/run-claude-hook.sh`; do not fork separate Grok-only copies of hook logic.
