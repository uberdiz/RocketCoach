"""RLBot v5 telemetry adapter.

Runs an RLBot *Script* (a read-only agent that does not drive a car) in a background thread and
converts every GamePacket into a GameFrame. The script never sends controller input or state-setting
messages, so it cannot affect gameplay. RLBot only works in offline matches it launches itself.

Requires the RLBot v5 server (installed by rlbot-v5-installer.msi) and the `rlbot` Python package.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
from pathlib import Path
from typing import Any

from rocketcoach.telemetry import source as src
from rocketcoach.telemetry.models import (
    AirState,
    BallState,
    BoostPadInfo,
    CarState,
    Controls,
    GameFrame,
    MatchPhase,
    PredictionPoint,
    Touch,
    Vec3,
    standard_boost_pads,
)

log = logging.getLogger(__name__)

DEFAULT_AGENT_ID = "rocketcoach/live-coach"
_RELAY_HANDLER_LISTS = ("on_connect_handlers", "packet_handlers", "field_info_handlers", "match_config_handlers",
                        "match_comm_handlers", "ball_prediction_handlers", "controllable_team_info_handlers",
                        "raw_handlers")
PREDICTION_STRIDE = 6  # keep every 6th of the 120 Hz slices -> 20 Hz, 6 s horizon


def _v(v: Any) -> Vec3:
    return Vec3(float(v.x), float(v.y), float(v.z))


def _enum_name(value: Any) -> str:
    # rlbot_flatbuffers enums print as e.g. "MatchPhase.Active"; tolerate plain ints too.
    text = repr(value)
    return text.split(".")[-1].strip("<>").split(":")[0].lower()


_PHASES = {
    "inactive": MatchPhase.INACTIVE, "countdown": MatchPhase.COUNTDOWN, "kickoff": MatchPhase.KICKOFF,
    "active": MatchPhase.ACTIVE, "goalscored": MatchPhase.GOAL_SCORED, "replay": MatchPhase.REPLAY,
    "paused": MatchPhase.PAUSED, "ended": MatchPhase.ENDED,
}
_AIR = {
    "onground": AirState.ON_GROUND, "jumping": AirState.JUMPING, "doublejumping": AirState.DOUBLE_JUMPING,
    "dodging": AirState.DODGING, "inair": AirState.IN_AIR,
}


def human_player_ids(match_config: Any) -> set[int]:
    """player_ids of Human cars. Needed because RLBot v5 reports Psyonix bots with is_bot=False
    (observed live on 2026-10-25), so PlayerInfo.is_bot alone can't tell you apart from them."""
    return {int(pc.player_id) for pc in match_config.player_configurations
            if type(pc.variety).__name__ == "Human"}


# Names Rocket League gives Psyonix bots (a duplicate gets a " (2)" suffix).
PSYONIX_BOT_NAMES = frozenset(n.lower() for n in (
    "Armstrong", "Bandit", "Beast", "Boomer", "Buzz", "C-Block", "Casper", "Caveman", "Centice", "Chipper",
    "Cougar", "Dude", "Foamer", "Fury", "Gerwin", "Goose", "Heater", "Hollywood", "Hound", "Iceman", "Imp",
    "Jester", "Junker", "Khan", "Marley", "Maverick", "Merlin", "Middy", "Mountain", "Myrtle", "Outlaw",
    "Poncho", "Rainmaker", "Raja", "Rex", "Roundhouse", "Sabretooth", "Saltie", "Samara", "Scout", "Shepard",
    "Slider", "Squall", "Sticks", "Stinger", "Storm", "Sultan", "Sundown", "Swabbie", "Tex", "Tusk", "Viper",
    "Wolfman", "Yuri",
))


def _base_name(name: str) -> str:
    return re.sub(r"\s*\(\d+\)$", "", str(name)).strip().lower()


def identify_humans(players: Any, match_config: Any | None, known_names: set[str],
                    player_name: str = "") -> tuple[set[int], str]:
    """Which packet players are human, and how we know.

    1. The match configuration's Human entries, when it actually lists the cars in the packet.
    2. Otherwise (RLBot sometimes streams a match with an empty configuration): the configured player name,
       then names learned from earlier matches, then the only car that isn't a known Psyonix bot name.
    Returns an empty set when it can't tell; the coach then waits instead of coaching a bot.
    """
    if match_config is not None:
        ids = human_player_ids(match_config)
        if ids:
            return ids, "match configuration"
    named = player_name.strip().lower()
    if named:
        ids = {int(p.player_id) for p in players if _base_name(p.name).startswith(named)}
        if ids:
            return ids, "player_name setting"
    ids = {int(p.player_id) for p in players if _base_name(p.name) in known_names}
    if ids:
        return ids, "name learned from an earlier match"
    candidates = [p for p in players if not p.is_bot and _base_name(p.name) not in PSYONIX_BOT_NAMES]
    if len(candidates) == 1:
        return {int(candidates[0].player_id)}, "only car without a Psyonix bot name"
    return set(), "unknown"


def convert_packet(packet: Any, prediction: Any | None, human_ids: set[int] | None = None) -> GameFrame:
    """Convert an rlbot.flat.GamePacket (+ BallPrediction) to a GameFrame. Pure function for testing."""
    mi = packet.match_info
    cars: list[CarState] = []
    for i, p in enumerate(packet.players):
        ph = p.physics
        touch = None
        if p.latest_touch is not None:
            t = p.latest_touch
            touch = Touch(float(t.game_seconds), _v(t.location), _v(t.normal))
        li = p.last_input
        cars.append(
            CarState(
                index=i,
                name=str(p.name),
                team=int(p.team),
                is_bot=int(p.player_id) not in human_ids if human_ids is not None else bool(p.is_bot),
                location=_v(ph.location),
                velocity=_v(ph.velocity),
                rotation=Vec3(float(ph.rotation.pitch), float(ph.rotation.yaw), float(ph.rotation.roll)),
                angular_velocity=_v(ph.angular_velocity),
                boost=float(p.boost),
                air_state=_AIR.get(_enum_name(p.air_state), AirState.IN_AIR),
                has_jumped=bool(p.has_jumped),
                has_double_jumped=bool(p.has_double_jumped),
                has_dodged=bool(p.has_dodged),
                dodge_dir=(float(p.dodge_dir.x), float(p.dodge_dir.y)),
                is_demolished=float(p.demolished_timeout) >= 0.0,
                is_supersonic=bool(p.is_supersonic),
                latest_touch=touch,
                controls=Controls(
                    float(li.throttle), float(li.steer), float(li.pitch), float(li.yaw), float(li.roll),
                    bool(li.jump), bool(li.boost), bool(li.handbrake),
                ),
                goals=int(p.score_info.goals),
                saves=int(p.score_info.saves),
                shots=int(p.score_info.shots),
                accolades=tuple(str(a) for a in p.accolades),
            )
        )
    ball = None
    if len(packet.balls) > 0:
        b = packet.balls[0].physics
        ball = BallState(_v(b.location), _v(b.velocity), _v(b.angular_velocity))
    preds: list[PredictionPoint] = []
    if prediction is not None:
        slices = prediction.slices
        for k in range(0, len(slices), PREDICTION_STRIDE):
            s = slices[k]
            preds.append(PredictionPoint(float(s.game_seconds), _v(s.physics.location), _v(s.physics.velocity)))
    scores = (0, 0)
    if len(packet.teams) >= 2:
        scores = (int(packet.teams[0].score), int(packet.teams[1].score))
    return GameFrame(
        time=float(mi.seconds_elapsed),
        frame_num=int(mi.frame_num),
        phase=_PHASES.get(_enum_name(mi.match_phase), MatchPhase.ACTIVE),
        time_remaining=float(mi.game_time_remaining),
        is_overtime=bool(mi.is_overtime),
        ball=ball,
        cars=cars,
        pads_active=[bool(s.is_active) for s in packet.boost_pads],
        scores=scores,
        prediction=preds,
    )


class RLBotSource(src.TelemetrySource):
    name = "rlbot"
    simulated = False
    capabilities = frozenset({src.CAP_INPUTS, src.CAP_PREDICTION, src.CAP_BOOST_PADS, src.CAP_TOUCHES,
                              src.CAP_JUMP_STATE})

    def __init__(self, agent_id: str | None = None, known_humans_path: Path | None = None,
                 player_name: str = "") -> None:
        super().__init__()
        # Started by RLBot as a match script -> RLBOT_AGENT_ID is set and RLBot waits for that agent.
        # Started by hand -> observe whatever match RLBot runs, like RLBot's own match manager does.
        self.agent_id = os.environ.get("RLBOT_AGENT_ID") or agent_id or DEFAULT_AGENT_ID
        self.observer = "RLBOT_AGENT_ID" not in os.environ
        self.known_path = known_humans_path
        self.player_name = player_name
        self.known_names: set[str] = set()
        if known_humans_path and known_humans_path.exists():
            try:
                self.known_names = set(json.loads(known_humans_path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        self._refresh_tried: set[frozenset[int]] = set()
        self._id_method = ""
        self._got_data = False
        self._thread: threading.Thread | None = None
        self._script: Any = None
        self._stop = threading.Event()
        self.error: str | None = None
        self.status = "connecting"
        self.boost_pads = standard_boost_pads()
        self.human_ids: set[int] = set()
        self.connections = 0

    def start(self, on_frame: src.FrameCallback) -> None:
        try:
            from rlbot.interface import RLBOT_SERVER_IP, RLBOT_SERVER_PORT, SocketRelay
            from rlbot.managers import Script  # imported lazily: optional dependency
        except ImportError as exc:  # pragma: no cover - depends on install
            raise RuntimeError("The 'rlbot' package is not installed. Run: pip install rlbot==2.0.0b56") from exc

        outer = self

        class CoachScript(Script):  # type: ignore[misc, valid-type]
            def initialize(self) -> None:
                pads = list(self.field_info.boost_pads)
                if pads:
                    outer.boost_pads = [BoostPadInfo(_v(p.location), bool(p.is_full_boost)) for p in pads]
                log.info("RLBot script initialised: %d boost pads, %d cars in match configuration, agent id %s%s",
                         len(pads), len(self.match_config.player_configurations), outer.agent_id,
                         " (observer)" if outer.observer else "")

            def handle_packet(self, packet: Any) -> None:
                outer._got_data = True
                outer.status = "live"
                pkt_ids = frozenset(int(p.player_id) for p in packet.players)
                if not pkt_ids:  # between matches / before cars spawn: nothing to identify
                    on_frame(convert_packet(packet, self.ball_prediction, outer.human_ids))
                    return
                cfg_ids = {int(pc.player_id) for pc in self.match_config.player_configurations}
                config_ok = bool(pkt_ids) and pkt_ids <= cfg_ids
                if pkt_ids and not config_ok and pkt_ids not in outer._refresh_tried:
                    # RLBot doesn't resend the configuration to an open connection when a new match starts.
                    # Reconnect once per new set of cars to fetch it. Closing the socket ends the receive loop
                    # cleanly; SocketRelay.disconnect() can't be called from inside a handler (it waits for the
                    # loop that is calling us, then the loop trips an AssertionError).
                    outer._refresh_tried.add(pkt_ids)
                    log.info("New match detected; reconnecting once to read its configuration")
                    try:
                        self._game_interface.socket.shutdown(socket.SHUT_RDWR)
                    except (OSError, AttributeError):
                        pass
                    return
                ids, method = identify_humans(packet.players, self.match_config if config_ok else None,
                                              outer.known_names, outer.player_name)
                if ids != outer.human_ids or method != outer._id_method:
                    names = [str(p.name) for p in packet.players if int(p.player_id) in ids]
                    log.info("Human player: %s (from %s)", ", ".join(names) or "not identified yet", method)
                    outer.human_ids, outer._id_method = ids, method
                    if method == "match configuration":
                        outer._learn(names)
                on_frame(convert_packet(packet, self.ball_prediction, ids))

            def run_persistent(self) -> None:
                """Like Script.run(), but asks RLBot to keep the connection open across StartMatch/StopMatch.

                Script.run() always uses close_between_matches=True, so starting or restarting a match from the
                RLBot window disconnected the coach a few seconds after RLBot launched it (seen 2026-10-26).
                Uses Script._run(), a private helper of rlbot 2.0.0b56 (pinned in requirements.txt).
                """
                ip = os.environ.get("RLBOT_SERVER_IP", RLBOT_SERVER_IP)
                port = int(os.environ.get("RLBOT_SERVER_PORT", RLBOT_SERVER_PORT))
                try:
                    self._game_interface.connect(wants_match_communications=False, wants_ball_predictions=True,
                                                 close_between_matches=False, rlbot_server_ip=ip,
                                                 rlbot_server_port=port)
                    self._run()
                finally:
                    self.retire()

        def run() -> None:
            delay = 1.0
            while not outer._stop.is_set():
                try:
                    # rlbot 2.0.0b56 keeps SocketRelay handler lists at class level (shared by all instances);
                    # give each connection fresh ones so a reconnect doesn't keep feeding the old script.
                    for attr in _RELAY_HANDLER_LISTS:
                        setattr(SocketRelay, attr, [])
                    outer._script = CoachScript(outer.agent_id)
                    if outer.observer:
                        # Empty agent id = observer: RLBot sends every match's packets without the coach being
                        # listed in that match (RLBot rejects an agent id the current match doesn't contain).
                        outer._script._game_interface.agent_id = ""
                    outer.connections += 1
                    outer.status = "connected"
                    outer._got_data = False
                    outer._script.run_persistent()
                    outer.error = None
                    if not outer._got_data and not outer.observer:
                        outer.observer = True
                        log.info("RLBot rejected agent id %s (not in the current match); switching to observer "
                                 "mode", outer.agent_id)
                    else:
                        log.info("RLBot closed the connection; reconnecting")
                    delay = 1.0
                except SystemExit:
                    outer.error = "RLBot script exited (agent id missing)."
                except Exception as exc:  # noqa: BLE001 - surfaced to the UI, then retried
                    outer.error = f"Waiting for RLBot ({type(exc).__name__}); retrying..."
                    log.warning("RLBot connection failed: %s", exc)
                    delay = min(delay * 2, 10.0)
                outer.status = "reconnecting"
                outer._stop.wait(delay)
            log.info("RLBot source stopped")

        self._thread = threading.Thread(target=run, name="rlbot-source", daemon=True)
        self._thread.start()

    def _learn(self, names: list[str]) -> None:
        new = {_base_name(n) for n in names} - self.known_names
        if new and self.known_path:
            self.known_names |= new
            try:
                self.known_path.parent.mkdir(parents=True, exist_ok=True)
                self.known_path.write_text(json.dumps(sorted(self.known_names)), encoding="utf-8")
            except OSError as exc:
                log.warning("Couldn't save known player names: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        iface = getattr(self._script, "_game_interface", None)
        if iface is not None:
            try:
                iface.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
