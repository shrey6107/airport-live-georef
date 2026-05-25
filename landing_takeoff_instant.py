import requests
import time
from runway_detection import get_airport_runway_headings
from georeferencing import ensure_geotiff_exists, get_geotiff_center_lon_lat

name = "KJFK"

geotiff_path = ensure_geotiff_exists(name)

lon, lat = get_geotiff_center_lon_lat(geotiff_path)

url = f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{2}"

possible_headings = get_airport_runway_headings(name)

session = requests.Session()

while True:

    time.sleep(1.5)

    try:

        req = session.get(url, timeout=5)

        data = req.json()

    except requests.exceptions.RequestException as exc:

        print("ADS-B request failed:", exc)

        continue

    for items in data['ac']:

        if items.get('alt_baro') != 'ground':

            track = items.get('track')
            if track is None:
                continue
            track = round(track)

            heading_match = any(
                min(abs(track - h), 360 - abs(track - h)) <= 2
                for h in possible_headings
            )

            if (
                items.get('baro_rate') is not None
                and items.get('baro_rate') < 0
                and heading_match
                and items.get('alt_baro') < 500
            ):

                print("Landing", items.get('flight', 'UNK'))


            if (
                items.get('baro_rate') is not None
                and items.get('baro_rate') > 0
                and heading_match
                and items.get('alt_baro') < 500
            ):

                print("Taking Off", items.get('flight', 'UNK'))
