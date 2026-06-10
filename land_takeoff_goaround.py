# ==========================================================
# Landing/Takeoff/Go-around Detection
#
# This script continuously monitors live ADS-B traffic around
# an airport and detects:
#   - Landings
#   - Takeoffs
#   - Go-arounds
#
# Aircraft events are stored in a SQLite database for
# downstream analysis of runway utilization, traffic demand,
# airline activity, and fleet composition.
#
# Detection is based on:
#   1. Aircraft altitude
#   2. Altitude trend (regression slope)
#   3. Ground speed
#   4. Runway heading alignment
# ==========================================================

from collections import defaultdict, deque, Counter
from datetime import datetime
import time
import sqlite3

import numpy as np
import requests

# ----------------------------------------------------------
# Airport Configuration
# ----------------------------------------------------------
# Generates a georeferenced airport representation and
# extracts all runway headings for the selected airport.

from runway_detection import get_airport_runway_headings
from georeferencing import ensure_geotiff_exists, get_geotiff_center_lon_lat


AIRPORT_NAME = "KSFO"
DIST_NM = 2
TAKEOFF_SPEED = 80
DB_NAME = AIRPORT_NAME

geotiff_path = ensure_geotiff_exists(AIRPORT_NAME)
lon, lat = get_geotiff_center_lon_lat(geotiff_path)
possible_headings = get_airport_runway_headings(AIRPORT_NAME)

url = f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{DIST_NM}"
HTTPS = requests.Session()

landing_votes = defaultdict(lambda: deque(maxlen=10))
takeoff_votes = defaultdict(lambda: deque(maxlen=10))
goaround_votes = defaultdict(lambda: deque(maxlen=10))
altitudes = defaultdict(lambda: deque(maxlen=10))

possible_landings = set()
possible_takeoffs = set()
possible_goarounds = set()
completed = set()

# ----------------------------------------------------------
# SQLite Database
# ----------------------------------------------------------
# Stores detected operational events for later analytics.

conn = sqlite3.connect(f"{DB_NAME}.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS aircraft_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT,
    callsign TEXT,
    aircraft_type TEXT,
    event_type TEXT,
    airport TEXT
)
""")
conn.commit()


def save_event(callsign, aircraft_type, event_type):
    cursor.execute("""
        INSERT INTO aircraft_events (
            event_time,
            callsign,
            aircraft_type,
            event_type,
            airport
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        (callsign or "Unknown").strip(),
        aircraft_type,
        event_type,
        AIRPORT_NAME
    ))
    conn.commit()

# ----------------------------------------------------------
# Altitude Trend Estimation
# ----------------------------------------------------------
# Uses a rolling altitude history and linear regression
# to estimate climb/descent trend. This helps smooth
# noisy vertical-speed values from the ADS-B feed.

def get_alt_trend(altitude_history):
    if len(altitude_history) < 7:
        return None

    x = np.arange(1, len(altitude_history) + 1)
    y = np.array(altitude_history)

    return np.polyfit(x, y, 1)[0]


def normalize_altitude(altitude):
    if altitude == "ground":
        return 0

    if isinstance(altitude, (int, float)):
        return max(altitude, 0)

    return altitude


def heading_is_aligned(heading):
    return any(
        min(abs(heading - h), 360 - abs(heading - h)) <= 2
        for h in possible_headings
    )

# ----------------------------------------------------------
# Cleanup Utilities
# ----------------------------------------------------------
# Removes aircraft from all tracking structures once an
# operational event has been finalized.

def cleanup_aircraft(hex_id):
    landing_votes.pop(hex_id, None)
    takeoff_votes.pop(hex_id, None)
    goaround_votes.pop(hex_id, None)
    altitudes.pop(hex_id, None)

    possible_landings.discard(hex_id)
    possible_takeoffs.discard(hex_id)
    possible_goarounds.discard(hex_id)

# ----------------------------------------------------------
# Landing Detection
# ----------------------------------------------------------
# Phase 1:
#   Generate landing votes when aircraft are:
#       - Below 500 ft
#       - Descending
#       - Aligned with a runway
#
# Phase 2:
#   Promote aircraft to landing candidate after
#   sufficient vote confidence. (70% confidence)
#
# Phase 3:
#   Confirm landing when aircraft reaches ground
#   and slows below 40 knots.

def update_landing_logic(
    hex_id,
    name,
    aircraft_type,
    altitude,
    ground_speed,
    heading_match,
    eff_trend
):
    if hex_id in possible_landings:
        if altitude == 0 and ground_speed is not None and ground_speed < 40:
            print(f"Saving {name} as landed")

            save_event(
                callsign=name,
                aircraft_type=aircraft_type,
                event_type="landing"
            )

            completed.add(hex_id)
            cleanup_aircraft(hex_id)
            return True

        # Already a landing candidate.
        # Do not keep adding landing votes.
        return False

    if eff_trend is None:
        landing_votes[hex_id].append("Unk")

    elif altitude < 500 and eff_trend < 0 and heading_match:
        landing_votes[hex_id].append("L")

    else:
        landing_votes[hex_id].append("Unk")

    votes = Counter(landing_votes[hex_id])

    if votes["L"] > 6:
        possible_landings.add(hex_id)

    return False

# ----------------------------------------------------------
# Takeoff / Go-Around Detection
# ----------------------------------------------------------
# Shared logic used for:
#   - Normal departures
#   - Go-arounds
#
# Aircraft generate votes when:
#   - Ground speed exceeds TAKEOFF_SPEED
#   - Altitude trend is positive
#
# Events are finalized after continued climb.

def update_takeoff_logic(
    hex_id,
    name,
    aircraft_type,
    altitude,
    ground_speed,
    eff_trend,
    event_type="takeoff"
):
    if event_type == "go_around":
        vote_store = goaround_votes
        candidate_set = possible_goarounds
        vote_label = "G"
        saved_label = "go-around"
    else:
        vote_store = takeoff_votes
        candidate_set = possible_takeoffs
        vote_label = "T"
        saved_label = "takeoff"

    if hex_id in candidate_set:
        if 800 < altitude < 3000:
            print(f"Saving {name} as {saved_label}")

            save_event(
                callsign=name,
                aircraft_type=aircraft_type,
                event_type=event_type
            )

            completed.add(hex_id)
            cleanup_aircraft(hex_id)
            return True

    if eff_trend is None:
        vote_store[hex_id].append("Unk")

    elif ground_speed is not None and ground_speed > TAKEOFF_SPEED and eff_trend > 0:
        vote_store[hex_id].append(vote_label)

    else:
        vote_store[hex_id].append("Unk")

    votes = Counter(vote_store[hex_id])

    if votes[vote_label] > 6:
        candidate_set.add(hex_id)

    return False

# ----------------------------------------------------------
# Main ADS-B Processing Loop
# ----------------------------------------------------------
# Continuously:
#   1. Fetch aircraft data
#   2. Update altitude histories
#   3. Detect landings
#   4. Detect takeoffs
#   5. Detect go-arounds
#   6. Persist completed events

while True:
    try:
        response = HTTPS.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        print("Connection error:", e)
        time.sleep(2)
        continue

    for raw_aircraft in data.get("ac", []):
        hex_id = raw_aircraft.get("hex")
        name = raw_aircraft.get("flight") or raw_aircraft.get("r") or "Unknown"
        altitude = raw_aircraft.get("alt_baro")
        ground_speed = raw_aircraft.get("gs")
        heading = raw_aircraft.get("track")
        v_speed = raw_aircraft.get("baro_rate")
        aircraft_type = raw_aircraft.get("t", "Unknown")

        if hex_id is None or hex_id in completed:
            continue

        if altitude is None or heading is None:
            continue

        altitude = normalize_altitude(altitude)

        if not isinstance(altitude, (int, float)):
            continue

        altitudes[hex_id].append(altitude)

        heading_match = heading_is_aligned(heading)

        alt_trend = get_alt_trend(altitudes[hex_id])

        if alt_trend is None:
            eff_trend = v_speed
        else:
            eff_trend = alt_trend

        finalized = update_landing_logic(
            hex_id=hex_id,
            name=name,
            aircraft_type=aircraft_type,
            altitude=altitude,
            ground_speed=ground_speed,
            heading_match=heading_match,
            eff_trend=eff_trend
        )

        if finalized:
            continue

        if hex_id in possible_landings:
            update_takeoff_logic(
                hex_id=hex_id,
                name=name,
                aircraft_type=aircraft_type,
                altitude=altitude,
                ground_speed=ground_speed,
                eff_trend=eff_trend,
                event_type="go_around"
            )

        else:
            update_takeoff_logic(
                hex_id=hex_id,
                name=name,
                aircraft_type=aircraft_type,
                altitude=altitude,
                ground_speed=ground_speed,
                eff_trend=eff_trend,
                event_type="takeoff"
            )

    time.sleep(3)