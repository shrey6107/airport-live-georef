"""
ADS-B aircraft data access layer.

This module is responsible for:
    1. Requesting live aircraft data from the ADS-B API.
    2. Cleaning each raw aircraft object.
    3. Returning only the fields needed by the Leaflet frontend.
"""

import requests


REQUEST_TIMEOUT_SECONDS = 5
ADSB_BASE_URL = "http://api.airplanes.live/v2/"

# Reuse one HTTP session instead of creating a new connection for every request.
HTTP_SESSION = requests.Session()


def classify_aircraft_status(vertical_rate):
    """
    Classify aircraft using vertical speed.

    This is a simple display-oriented rule:
        - Positive vertical rate  -> Taking off / climbing
        - Negative vertical rate  -> Landing / descending
        - Missing or non-numeric   -> N/A

    Later, this can be replaced with your more advanced runway-heading and
    landing/takeoff detection logic.
    """
    if not isinstance(vertical_rate, (int, float)):
        return "N/A"

    if vertical_rate > 0:
        return "Taking off"

    if vertical_rate < 0:
        return "Landing"

    return "N/A"


def normalize_altitude(altitude):
    """
    Normalize altitude values for display.

    ADS-B altitude can be:
        - a number, such as 3500
        - a negative number near the ground, such as -25
        - a string, such as "ground"

    Negative numeric altitudes are clamped to 0. Non-numeric values are kept
    unchanged because values like "ground" are meaningful.
    """
    if isinstance(altitude, (int, float)) and altitude < 0:
        return 0

    return altitude


def normalize_aircraft(ac):
    """
    Convert one raw ADS-B aircraft object into the frontend format.

    Returns None when the aircraft has no usable latitude/longitude, because
    Leaflet cannot display an aircraft without position data.
    """
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


def get_aircraft_data(lat, lon, dist=2):
    """
    Fetch live aircraft around a latitude/longitude point.

    Args:
        lat: Airport center latitude.
        lon: Airport center longitude.
        dist: Search radius in nautical miles.

    Returns:
        A list of cleaned aircraft dictionaries for the frontend.
    """
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

    return aircraft
