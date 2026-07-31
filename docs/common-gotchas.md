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
| Ops dashboard shows many healthy credentials but a new auth is not counted | Direct CLIProxy upload bypasses controller state; `ACTIVE` rows and auth files are separate stores | Import through `/v1/credentials/import`, then confirm exact controller row, hot load, and public-path probe | 2026-08-01 | `7af0e72` |
| Historical residential proxy attempts all return `407` | That provider's credential, subscription, or balance is invalid; it is not evidence that every current exit is broken | Trip a provider-level circuit breaker; assess the current managed-pool entry independently | 2026-08-01 | `CONVERGENCE_AND_GAPS.md` |
| Runtime process-scope test fails on macOS although the child is running | Process discovery assumed Linux `/proc`; macOS has no procfs mount | Keep `/proc` as the Linux fast path and use pinned `psutil` for cwd, argv, and PID enumeration elsewhere | 2026-07-31 | `webui/process_utils.py` |
| CLIProxy data API works but Management API returns `401` | The client env and pool-controller env contain different management keys; only the controller key matches the running service | Test the key against loopback without printing it, then copy only the valid key into a dedicated 0600 panel bridge env | 2026-07-31 | `CURRENT_STATE.md` |
| Exact provider probe returns HTTP `200` but state becomes `verification_failed` | A 2/16-token budget can finish as `status=incomplete` even when the token works | Ask for exact `pong` with a 128-token completion budget; keep rejecting generic `200` and `incomplete` | 2026-08-01 | `c61e09f` |
| A Camoufox canary reaches the profile page but ends with `wait-cf:0` | The OVH-direct browser session did not clear Turnstile; this happens before SSO and does not test the auth bridge | Stop after one account and retry through a healthy managed sticky exit; the WARP local-proxy canary passed once | 2026-08-01 | `CURRENT_STATE.md` |
| Device Flow returns a token but the exact Grok Build request is `403 permission-denied` | Token issuance and provider eligibility are separate; the Device token lacks the working Build context seen in the auth-code token | Prefer Authorization Code, retain Device as fallback, and accept only an exact provider probe | 2026-08-01 | `c61e09f` |
| Controller import returns `422` for an auth already accepted by CLIProxy | Panel filenames contain `@`, but the controller import regex did not allow that CLIProxy-safe character | Permit `@` while continuing to reject `/`, `..`, and non-JSON names | 2026-08-01 | `f31b737` |
| Registration works through managed proxy but pending recovery fails | Recovery spawned OAuth without inheriting the managed registration exit | Pass `CPA_PROXY` from the healthy proxy snapshot and fail closed when a configured pool is empty | 2026-08-01 | `2655fea` |
| Cloudflare Tunnel cannot reach a panel that is healthy on `127.0.0.1` | The tunnel runs inside Docker, so its loopback is not the host loopback | Bind the panel to the specific AI Stack bridge gateway, keep the public NIC closed, then test from a container and externally | 2026-08-01 | `CURRENT_STATE.md` |
| A controller-only Compose update tries to remove the shared live network | IPAM was added after the network already existed, so Compose treated it as a network replacement | Keep the existing network definition, record its current gateway, and coordinate panel/Tunnel updates only during an intentional network recreation | 2026-08-01 | `DEPLOYMENT.md` |
