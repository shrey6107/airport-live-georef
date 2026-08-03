from collections import deque, defaultdict, Counter
import requests
import time

from runway_detection import get_airport_runway_headings
from georeferencing import ensure_geotiff_exists, get_geotiff_center_lon_lat

aircrafts = defaultdict(lambda: deque(maxlen=10))

AIRPORT_NAME = "KOSH"
geotiff_path = ensure_geotiff_exists(AIRPORT_NAME)
lon, lat = get_geotiff_center_lon_lat(geotiff_path)

possible_headings = get_airport_runway_headings(AIRPORT_NAME)

landed, taking_off = set(), set()
last_landed = set()
last_taking_off = set()

session = requests.Session()

url = f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{2}"

while True:

    try:
        response = session.get(url, timeout=5)
        data = response.json()
    except requests.RequestException:
        print("Connection Error")
        continue

    for x in data['ac']:
        hex = x.get('hex')
        altitude = x.get('alt_baro')
        track = x.get('track')
        v_speed = x.get('baro_rate')
        name = x.get('flight', 'Unknown').strip()

        if altitude == "ground" or altitude is None:
            continue
        if track is None or v_speed is None or hex is None:
            continue

        heading_match = any(
            min(abs(track - h), 360 - abs(track - h)) <= 2
            for h in possible_headings
        )


        if altitude < 500 and v_speed < 0 and heading_match:
            state = "L"
        elif altitude < 500 and v_speed > 0 and heading_match:
            state = "T"
        else:
            state = 'Unk'

        aircrafts[hex].append(state)

        votes = Counter(aircrafts[hex])

        if votes['L'] > 6: #Threshold
            landed.add(name)
        elif votes['T'] > 6:
            taking_off.add(name)

    # Print only if there are updates to landing or taking off sets
    if landed != last_landed or taking_off != last_taking_off:
        new_landed = landed - last_landed
        new_taking_off = taking_off - last_taking_off

        if new_landed:
            print("Landing:", new_landed)

        if new_taking_off:
            print("Taking off:", new_taking_off)

        last_landed = landed.copy()
        last_taking_off = taking_off.copy()

    time.sleep(1.2)



