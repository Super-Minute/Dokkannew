# dokkan

A single-account helper organised as concentric rings. Dependencies point inward only;
`tests/test_architecture.py` fails the build if an inner ring imports an outer one.

```
            -> cli -\
domain -> application -> adapters -> service        -> (presentation)
                                               -> gui -/
```

Both `cli` and `gui` sit at the same outermost ring: each is its own
composition root (wires concrete adapters to `service`/`application`),
neither imports the other, and both expose the exact same capabilities.

| Ring | Package | Contents |
|------|---------|----------|
| 0 | `dokkan.domain` | Models (`Card`, `Stage`, `Stamina`, `Team`, `Account`), invariants, pure policies (what to sell, who to field) |
| 1 | `dokkan.application` | Use cases (`FarmStage`, `SellCards`, `FindStage`, `ListEvents`), the `GameGateway` port, settings |
| 2 | `dokkan.adapters` | JSON settings loader, save loader, `SimulatedGateway` (offline), envelope crypto, version store, `HttpGateway` (game API) |
| service | `dokkan.service` | `DokkanService` — one facade object exposing every operation |
| 3 | `dokkan.cli` | Command parser/script runner and the composition root (`main.py`) |
| 3 | `dokkan.gui` | Tk desktop front end: `controller.py` (display-agnostic logic, its own composition root) + `app.py` (the widgets) |

## Run

```
pip install -e .          # or: PYTHONPATH=src python -m dokkan.cli.main ...
dokkan events
dokkan farm 1001 8
dokkan --script commands.txt   # one command per line, '#' comments
PYTHONPATH=src:tests python -m unittest discover -s tests
```

Commands: `info`, `events`, `sell auto`, `farm <stage_id> <runs>`, `ping`.

## GUI

A desktop front end (stdlib `tkinter`, no extra dependency) offering the
same operations as the interactive CLI menu — nothing more:

```
PYTHONPATH=src python -m dokkan.gui   # or: make gui, or the `dokkan-gui` script after `pip install -e .`
```

The window has a connection bar (gateway: simulated/http, save file,
client, API URL override — same defaults as the CLI, simulated by
default), buttons for Account info / Events / Farm a stage / Daily tasks
/ List summon banners / Summon / Greatest Warrior board / Link skill
status / Sell cards automatically / Ping the server / Box summary / Box
as a webpage / Plan a sale, "Log in" and "Log in with Google" (the same
browser-based OAuth flow as the CLI, with its disclosure dialog before
anything opens), and an output pane mirroring what the CLI prints.

It drives the exact same `DokkanService` facade and application use
cases as the CLI — see `dokkan/gui/controller.py` (display-agnostic, unit
tested without a display) and `dokkan/gui/app.py` (the Tk widgets, a thin
shell with no game logic of its own). `gui` is architecturally a sibling
of `cli`: both are composition roots at the same outer ring, enforced by
`tests/test_architecture.py`; the GUI adds no capability, endpoint, or
scope beyond what the CLI already exposes.

## Game API gateway

`--gateway http` runs the same commands against the real game API using the
cleanroom envelope (memories 04/06/12): EVP_BytesToKey(MD5) → AES-256-CBC +
PKCS7 → HMAC-MD5 `sign`, `data`/`sign` form fields, plaintext-or-`{"sign":…}`
responses, and the `new_client_version_exists` refresh-and-retry-once handshake.

```
dokkan --gateway http --save save.json [--client glb|japan] ping
dokkan --gateway http --save save.json info
```

- `--save PATH` — account record (`identifier`, `access_token`, `secret`,
  `user_id`, `client`); required for the http gateway.
- `--client glb|japan` — overrides the save's client.
- `--api-url URL` — overrides the base host (defaults: `ishin-global.aktsk.com`
  for GLB, `ishin-production.aktsk.jp` for JP; used for tests/proxies).
- `--version-file version.json` — cached client versions (memory 08); when a
  code is cached it is sent with every request.
- `--version-url URL` — remote version feed (`version_code_url`, memory 03/08;
  JSON or `key=value` lines). When the server replies
  `new_client_version_exists`, the feed is fetched, merged into
  `version.json` under the same lock, and the call retried once with the new
  code.

Requests through the http gateway are paced: `bot_config.json`
`client_delays` (default 20–21 s per client, memory 10) inserts one randomized
delay before every game call after the first, matching the original bot's
respect-the-rate-limit behaviour. `ping` is never paced. The simulated
gateway is never paced.

Offline, from a local card-list JSON file (`[{"id","name","rarity","type","level","locked","favorite","attack"}]`):
`box <file>` (summary), `plan-sell <file>` (what `sell auto` would sell, dry run), `team <file> [--types AGL,TEQ] [--max-rarity SR] [--size N]`.
Settings come from `bot_config.json` (`stamina.prefer_meat`, `stamina.use_stone`, `auto_sell.enabled`, `game.team_build`, `client_delays.{global|japan}.{min,max}`); other keys are ignored, wrong types are a clean error.
Flags: `--config PATH` (default `bot_config.json`), `--script FILE`. Exit codes: 0 ok, 1 runtime/config/usage error, 2 bad arguments.

## Scope

- The simulated gateway remains the default and the test suite stays fully offline.
- The HTTP gateway is a cleanroom reimplementation from recovered interface
  facts only (`memories/`); unconfirmed wire details (secret-to-client/direction
  assignment, some response field names) are parameterised and documented in
  the adapters.
- Deliberately out of scope, with no extension point: spoofing client/device
  identity or analytics, evading bot detection, rate-limit dodging, proxy
  rotation, and bulk account creation or management.
