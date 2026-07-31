# Common Gotchas

Symptom → root cause → fix patterns discovered in this project. Agents: append a row after every
bug fix (see AGENTS.md § Autonomous Housekeeping). Check this table FIRST when diagnosing a bug -
the symptom may already be documented.

Keep entries terse: future sessions are the consumer and they have a limited attention budget.
Include a commit SHA and issue reference when known.

| Symptom | Root Cause | Fix | Date | Ref |
|---|---|---|---|---|
| Plain `curl` gets signup `403` while Camoufox works | Edge WAF/request fingerprint differs; the OVH IP is not blanket-blocked | Use the same browser-compatible preflight and worker fingerprint, then verify the real flow | 2026-07-31 | `OPERATIONS_ARCHITECTURE.md` |
| Panel service is active but no registrations appear | The systemd service runs only the monitor; batches are child processes started on demand | Check child processes, control JSON, and the latest batch log before calling it active | 2026-07-31 | `CURRENT_STATE.md` |
| Ops dashboard shows many healthy credentials but a new auth is not used | Controller `ACTIVE` rows and CLIProxy auth files are different stores; new panel auth is not bridged | Upload through the Management API, confirm hot load, and run an exact per-auth plus public-path probe | 2026-07-31 | `CONVERGENCE_AND_GAPS.md` |
| Residential proxy attempts all return `407` | Provider credentials, subscription, or balance is invalid; it is not an individual exit-IP failure | Trip a provider-level circuit breaker and repair the provider account before assigning sessions | 2026-07-31 | `CONVERGENCE_AND_GAPS.md` |
| Runtime process-scope test fails on macOS although the child is running | Process discovery assumed Linux `/proc`; macOS has no procfs mount | Keep `/proc` as the Linux fast path and use pinned `psutil` for cwd, argv, and PID enumeration elsewhere | 2026-07-31 | `webui/process_utils.py` |
| CLIProxy data API works but Management API returns `401` | The client env and pool-controller env contain different management keys; only the controller key matches the running service | Test the key against loopback without printing it, then copy only the valid key into a dedicated 0600 panel bridge env | 2026-07-31 | `CURRENT_STATE.md` |
