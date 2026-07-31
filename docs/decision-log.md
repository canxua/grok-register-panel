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
