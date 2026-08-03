"""Fetch and normalize live ADS-B aircraft data for the web app."""

import requests

from aircraft_track_history import get_track, update_tracks


REQUEST_TIMEOUT_SECONDS = 5
ADSB_BASE_URL = "https://api.airplanes.live/v2"

# Reuse one HTTP session instead of creating a new connection for every request.
HTTP_SESSION = requests.Session()


def classify_aircraft_status(vertical_rate):
    """Classify aircraft for display using the existing vertical-speed rule."""
    if not isinstance(vertical_rate, (int, float)):
        return "N/A"

    if vertical_rate > 0:
        return "Taking off"

    if vertical_rate < 0:
        return "Landing"

    return "N/A"


def normalize_altitude(altitude):
    """Clamp negative numeric altitudes while preserving values like "ground"."""
    if isinstance(altitude, (int, float)) and altitude < 0:
        return 0

    return altitude


def normalize_aircraft(ac):
    """Convert one raw aircraft object into the existing frontend format."""
    lat = ac.get("lat")
    lon = ac.get("lon")

    if lat is None or lon is None:
        return None

    flight = (ac.get("flight") or "").strip()
    registration = (ac.get("r") or "").strip()
    hexid = (ac.get("hex") or "").strip()

    vertical_rate = ac.get("baro_rate")
    altitude = normalize_altitude(ac.get("alt_baro"))

    callsign = flight or registration or hexid or "UNK"

    return {
        "hex": hexid,
        "callsign": callsign,
        "lat": lat,
        "lon": lon,
        "altitude": altitude,
        "track": ac.get("track"),
        "ground_speed": ac.get("gs"),
        "vertical_rate": vertical_rate,
        "aircraft_type": ac.get("t"),
        "status": classify_aircraft_status(vertical_rate),
    }


def get_aircraft_data(lat, lon, dist=5):
    """Fetch live aircraft around a point and attach their path histories."""
    if lat is None or lon is None:
        return []

    url = f"{ADSB_BASE_URL}/point/{lat}/{lon}/{dist}"

    try:
        response = HTTP_SESSION.get(
            url,
            headers={"accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as exc:
        print(f"ADS-B connection error: {exc}")
        return []

    except ValueError as exc:
        print(f"ADS-B response was not valid JSON: {exc}")
        return []

    aircraft = []

    for raw_aircraft in data.get("ac", []):
        normalized = normalize_aircraft(raw_aircraft)

        if normalized is not None:
            aircraft.append(normalized)

    update_tracks(aircraft)

    for normalized in aircraft:
        normalized["path"] = get_track(normalized.get("hex"))

    return aircraft
