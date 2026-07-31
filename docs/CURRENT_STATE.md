# Current State

This is the volatile handoff for the next Codex or Grok session. Update it when
live facts or priorities change. Stable architecture belongs in the linked
architecture documents.

## Snapshot

- **Verified at:** 2026-07-31 12:42 Asia/Shanghai
- **Local repository:** `/Users/jack/project/github/grok-register-panel`
- **Remote register panel:** `/opt/grok-register-panel` on the OVH host
- **Remote AI stack:** `/opt/ai-stack` on the same OVH host
- **Panel access:** loopback `127.0.0.1:18080` through an SSH tunnel
- **Public data plane:** `api.canxu.top`
- **Operations console:** `ops.canxu.top`
- **Local test environment:** `.venv` on Python 3.11.14 with pinned direct
  dependencies installed; browser engine assets were not fetched locally

## Confirmed Live Facts

1. Registration artifacts are stored on OVH, not in the local checkout. The
   local `accounts/`, `cpa_auth/`, and `log/` directories only contain tracked
   placeholders.
2. Remote `/opt/grok-register-panel/cpa_auth` contains two private auth JSON
   records. The latest was written at 2026-07-31 04:20:37 UTC. Runtime files and
   directories are owner-only (`0600` files and `0700` directories).
3. `grok-register-panel.service` is enabled and active, but only the monitor
   process is currently running. There is no active registration child process,
   systemd timer, or cron registration job.
4. The latest one-account batch finished at 2026-07-31 04:20:38 UTC with one
   registration success and zero failures. The previous full canary also passed
   an independent Grok Build data-plane probe.
5. The new panel's `cpa_auth` directory is not mounted or uploaded into the
   existing CLIProxy auth volume. A new registration is therefore not yet
   automatically available through the public AI Stack.
6. At 2026-07-31 12:42 Asia/Shanghai, the controller reported 100 `ACTIVE`
   account rows against a target of 100. The screenshot accurately reflected
   the frontend value at 12:28, but that 101 meant controller `ACTIVE` rows, not
   101 credentials that had each just passed an independent provider request.
7. CLIProxy had 102 JSON files in its PostgreSQL-backed auth directory at the
   same audit point. File count, controller `ACTIVE` count, and independently
   verified credentials are deliberately different metrics.

## Current Operating Mode

- New panel: manual `batch` mode, one worker, one account, zero slot retries.
- Continuous auto-registration: not running.
- Legacy pool containers: still running and intentionally retained.
- Residential proxy pool: not usable; sampled credentials return `407`.
- Registration egress: current canaries use OVH direct with browser-compatible
  request fingerprinting.

## Open Work In Priority Order

1. Build the new-panel-to-CLIProxy auth bridge using the loopback Management API
   and a `0600` secret.
2. Add the real success state machine: exact credential probe, upload, hot-load
   confirmation, and public data-plane probe before `verified`.
3. Add a per-credential quota view. CLIProxyAPI v7.2.80 exposes auth health and
   request statistics but not provider quota windows natively. Evaluate
   CLIProxy Quota Tray first; keep provider-reported windows separate from local
   token/cost estimates.
4. Merge the upstream managed proxy pool after valid provider credentials are
   available, retaining one-account sticky sessions and fail-closed behavior.
5. Add SQLite task/account/proxy leases before increasing concurrency.

## Resume Checklist

```bash
git status --short --branch
bash brain/bin/qmd search "current OVH auth bridge verified"
PYTHON_BIN=.venv/bin/python bash scripts/run_tests.sh
```

Before changing production, re-run the relevant live audit because counts and
process state are expected to drift.

## Related Documents

- [Registration architecture](OPERATIONS_ARCHITECTURE.md)
- [Convergence and reliability gaps](CONVERGENCE_AND_GAPS.md)
- [Trellis integration](TRELLIS.md)
- [Deployment and rollback](../DEPLOYMENT.md)
