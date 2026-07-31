# About The Operator

This file helps coding agents calibrate explanations without inferring identity
from usernames, paths, domains, or infrastructure metadata.

## Background

- Works primarily with Codex and Grok on software, infrastructure, and operations tasks.
- Has Java development experience and is learning the Python, browser automation,
  networking, proxy, and OAuth parts of this project while operating them.
- Prefers concrete commands, diagrams, live verification, and durable documentation.

## Technical level

Explain networking, encryption, OAuth, queues, leases, and provider boundaries in
plain Chinese first, then map the explanation to exact files, processes, APIs,
and commands. Do not hide important engineering detail behind jargon.

## Working style

- Give the direct conclusion first and evidence immediately after it.
- Continue through implementation and verification when the request is actionable.
- For broad exploration, use clean subagents to compress noisy evidence; keep final
  judgment and exact edits in the main session.
- Distinguish confirmed facts, engineering inferences, unknowns, and next actions.

## Strengths / gaps

The operator supplies product intent and prior operational context. Agents should
compensate with architecture judgment, source inspection, failure-domain analysis,
security hygiene, regression testing, and clear rollback plans.

## Current constraints

- One small OVH host currently runs both control and data-plane components.
- Reliability and evidence matter more than maximum registration throughput.
- Secrets must remain in private runtime files and must never enter git, logs,
  screenshots, prompts, or the Trellis brain corpus.
