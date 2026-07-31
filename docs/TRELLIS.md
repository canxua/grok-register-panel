# Trellis Integration

This repository uses the project-continuity scaffold from
[`craigcossairt/trellis`](https://github.com/craigcossairt/trellis).

## Installed Baseline

- Trellis release: `v1.1.0`
- Upstream commit: `486f84c75f6b283f6a05597a63b5ad7198aebd65`
- Release date: 2026-07-24
- Latest-tag recheck: 2026-08-01; upstream still exposes `v1.1.0` as the newest release tag
- Integration style: selected-file merge into an existing repository
- Package manager: none. This Trellis is a GitHub template, not the unrelated
  npm packages named `trellis` or `@mindfoldhq/trellis`.

The existing project README, license, CI workflow, architecture documents, and
business code were preserved. Trellis-specific behavior is concentrated in
`AGENTS.md`, `.claude/`, `.grok/`, `.githooks/`, `bin/`, `brain/`, and the
methodology documents under `docs/`.

## How Continuity Works

### Codex

Codex reads the root `AGENTS.md` natively. Its session bootstrap points to
`docs/CURRENT_STATE.md`, the architecture documents, decisions, and gotchas.
Opening a new Codex session in the repository root is sufficient.

### Grok Build

Grok reads `.grok/config.toml`, reuses commands/skills from `.claude/`, and runs
the shared hooks through `bin/run-claude-hook.sh`. On the first Grok session,
trust the repository hooks when prompted (or use Grok's `/hooks-trust` command).

### Project Brain

The brain is a local, generated search index over project docs, root context
files, and git history. Generated corpus and index state remain gitignored.

Refresh after important documentation or decision changes:

```bash
bash brain/scripts/ingest.sh
bash brain/bin/qmd update
```

Search manually from any harness:

```bash
bash brain/bin/qmd search "CLIProxy auth bridge"
```

Grok and Claude hooks inject small matching excerpts automatically. Codex uses
the `AGENTS.md` bootstrap and may invoke the same search commands when needed.

BM25 full-text search is the verified retrieval baseline on this workstation.
QMD 2.5.3 vector commands require a working `node-llama-cpp` backend. Both the
default and `QMD_FORCE_CPU=1` embedding attempts currently fall back to the same
packaged backend and stop at a Metal shader compilation error, so `embed`,
`vsearch`, and hybrid `query` are not part of the session bootstrap. This does
not affect ingestion, BM25 indexing, hooks, or continuity. Re-test vector search
after QMD or its llama.cpp backend is upgraded; do not report it as available
until `qmd embed` and a sample `qmd vsearch` both finish successfully.

## Updating Trellis

Template copies do not auto-update. Check the official release page periodically,
compare the new tag with the version in `.trellis-version`, and selectively port
useful changes. Do not overwrite the customized `AGENTS.md`, project docs, or
secret patterns.

```bash
git ls-remote --tags https://github.com/craigcossairt/trellis.git 'refs/tags/v*'
git diff --no-index .claude /path/to/new-trellis/.claude
git diff --no-index .grok /path/to/new-trellis/.grok
```

After an update, run:

```bash
bash -n .claude/hooks/*.sh bin/*.sh .githooks/pre-push \
  brain/scripts/*.sh brain/hooks/*.sh brain/bin/qmd
PYTHON_BIN=.venv/bin/python bash scripts/run_tests.sh
bash brain/scripts/ingest.sh
bash brain/bin/qmd update
```
