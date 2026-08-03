"""
Nearest-airport lookup for map clicks.

This module reads the local US airports TSV once and returns the nearest
supported airport code. The frontend then uses the normal chart-discovery flow.
"""

import csv
import math
from functools import lru_cache
from pathlib import Path
from typing import Any


AIRPORTS_TSV_PATH = Path(__file__).with_name("airports.txt")
EARTH_RADIUS_NM = 3440.065
MAX_CLICK_DISTANCE_NM = 2


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two points in nautical miles."""
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_NM * c


def airport_code_from_row(row: dict[str, str]) -> str:
    """Pick the best code available for the existing airport loader."""
    for column in ("icao_code", "gps_code", "ident", "local_code"):
        code = (row.get(column) or "").strip().upper()

        if code:
            return code

    return ""


@lru_cache(maxsize=1)
def load_airports() -> tuple[dict[str, Any], ...]:
    """Load and cache valid airport rows from the local TSV file."""
    airports = []

    with AIRPORTS_TSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")

        for row in reader:
            code = airport_code_from_row(row)

            if not code:
                continue

            try:
                lat = float(row.get("latitude_deg") or "")
                lon = float(row.get("longitude_deg") or "")
            except ValueError:
                continue

            airports.append(
                {
                    "icao": code,
                    "name": (row.get("name") or code).strip(),
                    "lat": lat,
                    "lon": lon,
                }
            )

    return tuple(airports)


def find_nearest_airport(
    lat: float,
    lon: float,
    max_distance_nm: float = MAX_CLICK_DISTANCE_NM,
) -> dict[str, Any] | None:
    """Find the nearest cached airport within max_distance_nm."""
    nearest_airport = None
    nearest_distance = None

    for airport in load_airports():
        distance_nm = haversine_nm(lat, lon, airport["lat"], airport["lon"])

        if nearest_distance is None or distance_nm < nearest_distance:
            nearest_airport = airport
            nearest_distance = distance_nm

    if nearest_airport is None or nearest_distance is None:
        return None

    if nearest_distance > max_distance_nm:
        return None

    return {
        **nearest_airport,
        "distance_nm": round(nearest_distance, 1),
    }


def find_airport_by_code(icao: str) -> dict[str, Any] | None:
    """Return one cached airport record by its normalized identifier."""
    normalized_icao = icao.upper().strip()

    for airport in load_airports():
        if airport["icao"] == normalized_icao:
            return dict(airport)

    return None
