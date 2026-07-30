# Release Checklist

- [ ] Working tree contains only intended release files.
- [ ] `scripts/run_tests.sh` passes from a clean clone.
- [ ] `python -m pip check` passes in the deployment virtual environment.
- [ ] `python -m camoufox version` reports an installed browser engine.
- [ ] The batch supervisor test restarts a simulated `_getChildFrames` driver crash and resumes only remaining slots.
- [ ] `config.json`, all `proxies*.txt` / `stickies*.txt`, `accounts/`, auth directories, and `log/` are owner-only.
- [ ] `MONITOR_TOKEN` is set and anonymous operational API requests return 401.
- [ ] If `PANEL_INCLUDE_TAIL=1`, operational APIs require authentication and returned log lines pass the redaction tests.
- [ ] The monitor binds to the intended loopback, LAN, or Tailscale address only.
- [ ] Blacklist state was migrated before replacing legacy source files.
- [ ] Pending SSO counts and recovery controls were checked without starting a job.
- [ ] Desktop and mobile screenshots were reviewed in light and dark themes.
- [ ] The systemd service restarts cleanly and survives a service restart.
- [ ] No registration or recovery job is active during code synchronization.
- [ ] Geist font files ship with `LICENSES/OFL-1.1-Geist.txt`.
- [ ] Secret scanning reports no committed runtime credentials.
