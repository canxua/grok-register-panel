# Decision Log

What was decided, when, and why. **Decisions only** - not specs, not current state, not
implementation details. One line per decision; reference issue IDs instead of embedding detail.
If an entry needs more than 2 lines, it belongs in a dedicated doc, not here.

Format: `- **YYYY-MM-DD** - Decision description. See <issue-ref>.`

---

- **2026-07-31** - Keep the legacy replenishment components running until the new panel uploads an auth, CLIProxy hot-loads it, and the public data plane passes a canary. See `CONVERGENCE_AND_GAPS.md`.
- **2026-07-31** - Keep registration in manual one-account batch mode until proxy leases and the verified success state machine are implemented. See `CURRENT_STATE.md`.
- **2026-07-31** - Treat controller `ACTIVE`, CLIProxy auth file count, and exact per-auth verification as separate metrics. See `CURRENT_STATE.md`.
- **2026-07-31** - Adopt `craigcossairt/trellis` v1.1.0 with Codex/Grok adapters and a local project brain; preserve existing project docs and code. See `TRELLIS.md`.
- **2026-07-31** - Inject only the required CLIProxy management and New API client keys through a dedicated mode-`0600` panel bridge env; do not load the controller's database-bearing secret file. See `CURRENT_STATE.md`.
- **2026-07-31** - Keep the legacy stack after the first successful bridge replay; retirement requires multiple new-account end-to-end canaries and an explicit operator decision. See `CURRENT_STATE.md`.
- **2026-08-01** - Keep automatic registration disabled after the first WARP-backed end-to-end canary; one successful account is evidence of reachability, not a sustained success-rate baseline. See `CURRENT_STATE.md`.
- **2026-08-01** - Use Cloudflare WARP only as a scoped local-proxy registration exit; do not describe it as dynamic residential and do not replace the host default route. See `OPERATIONS_ARCHITECTURE.md`.
- **2026-08-01** - Prefer Authorization Code for Grok Build credentials and retain Device Flow only as fallback because the exact Device credential was provider-denied. See `CURRENT_STATE.md`.
- **2026-08-01** - Route verified panel auth through the AI Stack controller import API before CLIProxy hot-load verification, so monitoring and runtime state share one write path. See `7af0e72`.
- **2026-08-01** - Keep the old replenishment components and pool controller running per the operator's decision; no resource-convergence action is currently scheduled. See `CONVERGENCE_AND_GAPS.md`.
- **2026-08-01** - Publish the register panel through the existing Cloudflare Tunnel while binding its origin only to the AI Stack Docker bridge; retain `MONITOR_TOKEN` for every operational read and write. See `CURRENT_STATE.md`.
