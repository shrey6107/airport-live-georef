"""Short-lived, in-memory aircraft position history."""

from collections import defaultdict, deque
import math
import time
from typing import Any


MAX_TRACK_POINTS = 120
STALE_TRACK_SECONDS = 180

# This assumes one Python process/Uvicorn worker. Multiple workers would keep
# separate track dictionaries.
_tracks: dict[str, deque[dict[str, float]]] = defaultdict(
    lambda: deque(maxlen=MAX_TRACK_POINTS)
)
_last_seen: dict[str, float] = {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def update_tracks(aircraft_list: list[dict[str, Any]]) -> None:
    now = time.time()

    for aircraft in aircraft_list:
        try:
            hex_id = str(aircraft.get("hex") or "").strip()
            latitude = _number(aircraft.get("lat"))
            longitude = _number(aircraft.get("lon"))
            raw_altitude = aircraft.get("altitude")
            altitude = 0.0 if str(raw_altitude).lower() == "ground" else _number(raw_altitude)

            if (
                not hex_id
                or latitude is None
                or longitude is None
                or altitude is None
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                continue

            point = {
                "lat": latitude,
                "lon": longitude,
                "alt_baro": altitude,
                "timestamp": now,
            }
            track = _tracks[hex_id]

            if (
                not track
                or track[-1]["lat"] != latitude
                or track[-1]["lon"] != longitude
                or track[-1]["alt_baro"] != altitude
            ):
                track.append(point)

            _last_seen[hex_id] = now
        except (AttributeError, TypeError):
            continue

    remove_stale_tracks(now)


def get_track(hex_id: str | None) -> list[dict[str, float]]:
    normalized_hex = str(hex_id or "").strip()
    return list(_tracks.get(normalized_hex, ()))


def remove_stale_tracks(now: float | None = None) -> None:
    current_time = time.time() if now is None else now

    for hex_id, last_seen in list(_last_seen.items()):
        if current_time - last_seen <= STALE_TRACK_SECONDS:
            continue

        _tracks.pop(hex_id, None)
        _last_seen.pop(hex_id, None)


def clear_tracks() -> None:
    _tracks.clear()
    _last_seen.clear()
