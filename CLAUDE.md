# dokkannew — cleanroom Dokkan single-account helper

Read `/home/kyle/dokkan-farm-bot/PROGRESS.md` first — it is the authoritative
state log and handoff, including the remaining work and boundaries.

## Commands

- Tests: `PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests`
- Run (offline default): `PYTHONPATH=src python -m dokkan.cli.main info`
- Real API: `... --gateway http --save save.json [login|login google|info|events|farm|summons|summon|daily|board|links|sell|ping]`
- Build executables: `make linux` (→ dist/dokkan, verified) and `make windows`
  (runs build.bat on Windows, or via Wine — see build.sh)

## Working on the bot

Work through `src/dokkan/service.py` (`DokkanService`) — it is the operations
facade; everything wire-level is encapsulated behind it in the adapters ring.
Adding a feature = a service method + a CLI verb; no transport changes needed.
Every endpoint family in the spec already has a typed mixin method on the
http gateway (cards, shops, social/battle, misc — see the adapters ring), and
`DokkanService.raw(method, path, ...)` is the passthrough for anything else —
you should never need to edit transport code.

## Architecture

Rings `cli → adapters → application → domain`, inward-only imports, enforced
by `tests/test_architecture.py`. Zero external dependencies, stdlib unittest,
dataclasses/Protocol style. The implemented spec lives in
`/home/kyle/dokkan-farm-bot/memories/00–13`.

## Hard boundaries (policy, not TODO)

- Single account, the user's own save only.
- Never open anything under `/home/kyle/Downloads/dokkanbot/` (the original
  binary — cleanroom discipline).
- Never implement: account creation, device-token generation, captcha
  solving/automation, bot-detection evasion, rate-limit dodging (keep the
  `client_delays` pacing), bulk account management.
- Never contact the original bot author's server (45.76.162.135:8443) or any
  license/tier infrastructure; this codebase has no licensing system and must
  not grow one.
- The `recovered/` extraction artifacts under dokkan-farm-bot are historical
  evidence — not needed and not to be processed.
