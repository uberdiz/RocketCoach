# Rocket Coach AI: implementation plan and blockers

Written 2026-10-25 after inspecting this machine and researching the current interfaces.

## Machine (inspected, not assumed)

| Item | Value | Consequence |
|---|---|---|
| CPU | Intel i5-10500H, 6C/12T | Rule engine at 30 Hz is cheap; fine |
| RAM | 7.8 GB | No local LLM by default (a 7B model plus Rocket League would swap) |
| GPU | GTX 1650 Max-Q 4 GB, plus Intel UHD | Rocket League needs the GPU; no CV/LLM on GPU while playing |
| Disk | 21.9 GB free on C: | Use `PySide6-Essentials` (not the full PySide6 bundle) |
| Python | 3.15 (default), 3.13, 3.8 | Use 3.13: `rlbot` needs >=3.11 and PySide6 wheels exist for 3.13 |
| Rocket League | Epic, `C:\Program Files\Epic Games\rocketleague`, build `Prime+Update60.1` (files dated 2026-10-21), ships `RocketLeague_EAC.exe` | Easy Anti-Cheat is present; telemetry needs the EAC-off offline path |
| RLBot | Only the old v4 `RLBotGUIX` launcher (2023); its bundled Python is gone; no v5 install | User must install RLBot v5 |
| BakkesMod | Installed in `%APPDATA%\bakkesmod` | Not used (see below) |
| Replays | No `TAGame\Demos` folder exists | No .replay files to test against |

## Interface research

- **RLBot v5** (pre-release, actively developed; Python package `rlbot==2.0.0b56`, 2026-10-02, needs `rlbot_flatbuffers~=0.19`).
  A *Script* is a non-bot process that receives every `GamePacket` (players including humans, balls, boost pad
  states, match phase, scores, `latest_touch`, `air_state`, `has_jumped/has_double_jumped/has_dodged`,
  `dodge_dir`, `demolished_timeout`, `is_supersonic`, `accolades`, and `last_input` controller state) plus a
  6-second, 120 Hz `BallPrediction`, and a static `FieldInfo` (boost pad locations, goals). This covers almost
  every telemetry field the spec asks for. Wheel contact per wheel is **not** exposed: only `air_state`.
- **Easy Anti-Cheat** became mandatory for online PC play on 2026-04-28. Epic's help page says that with EAC off,
  mods work in offline matches, training, LAN and replays. RLBot is an offline framework and launches the game itself.
  I found **no authoritative statement** that RLBot v5 works with the post-EAC Epic build. This must be checked on
  this machine.
- **BakkesMod** does not run with EAC on, and reportedly is no longer maintained against new game updates. Not used.
- **Replay parsing**: no replays on disk. The app records its own compressed telemetry per match and analyses that.
  `.replay` parsing (e.g. `subtr-actor`/`boxcars`) is listed as future work.
- **Overlay**: a Qt frameless, translucent, always-on-top window with `WS_EX_TRANSPARENT|WS_EX_LAYERED|WS_EX_NOACTIVATE`
  works over *borderless/windowed* games. Exclusive fullscreen is not supported by desktop overlays. The user must
  run Rocket League in borderless mode.
- **Training packs**: codes come from the prejump.com pack database (each with a per-code URL), fetched 2026-10-25.
  `rocketcoach verify-packs` re-checks every code against its source URL.

## Blockers needing the user

1. Install RLBot v5 (`rlbot-v5-installer.msi` from github.com/RLBot/launcher) and confirm it can start an offline match
   on the Epic build with EAC off.
2. Set Rocket League to **Borderless** display mode.
3. Real-match verification of everything telemetry-driven (I cannot play the game).

## Build order (vertical slice first)

1. Telemetry models, mock simulator, RLBot Script adapter, recorder.
2. Derived game context (roles, threat, possession) and event detection.
3. Tactical, boost and mechanics rules with confidence scores.
4. Coach prioritiser (cooldowns, dedupe, categories, min confidence), voice.
5. PySide6 overlay driven by mock telemetry.
6. SQLite storage, post-match report (Markdown + HTML with boost heatmap), history comparison.
7. Training recommender from observed weaknesses.
8. Optional Claude provider (off by default) with rate limit, cache, cost tracking.
9. Tests, launch scripts, README.
