"""
Convert georeferenced airport diagrams into web-ready Leaflet assets.

The georeferencing pipeline creates a GeoTIFF. GIS tools like QGIS understand
the GeoTIFF's affine transform, but a browser map needs simpler files.

This module converts each airport GeoTIFF into:

    static/airports/<ICAO>/diagram.png
    static/airports/<ICAO>/corners.json

The PNG is the visible airport diagram.
The corners.json file tells Leaflet where to place and rotate that PNG.
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from georeferencing import (
    GeoreferencingError,
    ensure_geotiff_exists,
    get_geotiff_center_lon_lat,
)


# All generated web assets are stored under static/ so FastAPI can serve them.
STATIC_AIRPORTS_DIR = Path("static") / "airports"

# Allows common ICAO-style airport identifiers like KSFO, KLAX, KJFK, and some 3-character IDs.
ICAO_PATTERN = re.compile(r"^[A-Z0-9]{3,4}$")


def validate_icao(icao: str) -> str:
    """
    Normalize and validate an airport identifier.

    Example:
        "ksfo" -> "KSFO"
    """
    normalized = icao.upper().strip()

    if not ICAO_PATTERN.match(normalized):
        raise ValueError(
            "Invalid airport identifier. Use an ICAO-style code like KSFO, KLAX, or KJFK."
        )

    return normalized


def normalize_to_uint8(img: np.ndarray) -> np.ndarray:
    """
    Convert raster values into uint8 image values.

    Browser images use pixel values from 0 to 255. GeoTIFF data can sometimes
    be stored in other numeric ranges, so this function rescales it when needed.
    """
    if img.dtype == np.uint8:
        return img

    img_min = np.nanmin(img)
    img_max = np.nanmax(img)

    # Avoid division by zero if the image has no useful value range.
    if img_max - img_min < 1e-9:
        return np.zeros_like(img, dtype=np.uint8)

    return ((img - img_min) / (img_max - img_min) * 255).astype(np.uint8)


def read_geotiff_as_rgb(src: rasterio.DatasetReader) -> np.ndarray:
    """
    Read a GeoTIFF as an RGB image array.

    If the GeoTIFF already has 3 or more bands, use the first three bands.
    If it has only one band, duplicate that band into R/G/B channels so it can
    still be saved as a normal PNG.
    """
    if src.count >= 3:
        img = src.read([1, 2, 3])
        img = np.transpose(img, (1, 2, 0))
        return normalize_to_uint8(img)

    band = normalize_to_uint8(src.read(1))
    return np.dstack([band, band, band])


def extract_rotated_corners(src: rasterio.DatasetReader) -> dict[str, list[float]]:
    """
    Extract the three image corners needed by Leaflet's rotated overlay plugin.

    Normal Leaflet image overlays only need a rectangular bounding box, but your
    airport diagrams can be rotated. A rotated overlay needs:

        top-left
        top-right
        bottom-left

    Rasterio returns coordinates as (x, y). For EPSG:4326, that means
    (longitude, latitude). Leaflet expects [latitude, longitude], so each pair
    is swapped before writing to JSON.
    """
    transform = src.transform

    top_left = transform * (0, 0)
    top_right = transform * (src.width, 0)
    bottom_left = transform * (0, src.height)

    return {
        "topLeft": [top_left[1], top_left[0]],
        "topRight": [top_right[1], top_right[0]],
        "bottomLeft": [bottom_left[1], bottom_left[0]],
    }


def prepare_airport_for_web(icao: str) -> dict[str]:
    """
    Generate Leaflet-ready assets for one airport.

    Flow:
        1. Validate the airport code.
        2. Ensure the georeferenced GeoTIFF exists.
        3. Convert the GeoTIFF image data into diagram.png.
        4. Extract rotated corner coordinates into corners.json.
        5. Return metadata needed by the frontend.
    """
    icao = validate_icao(icao)

    output_dir = STATIC_AIRPORTS_DIR / icao
    output_dir.mkdir(parents=True, exist_ok=True)

    png_path = output_dir / "diagram.png"
    corners_path = output_dir / "corners.json"

    # This calls your existing georeferencing pipeline.
    geotiff_path = ensure_geotiff_exists(icao)

    with rasterio.open(geotiff_path) as src:
        # Convert the GeoTIFF pixels into a browser-friendly PNG.
        img = read_geotiff_as_rgb(src)
        Image.fromarray(img).save(png_path)

        # Save the real-world image corners so Leaflet can rotate/place the PNG.
        corners = extract_rotated_corners(src)

        with corners_path.open("w", encoding="utf-8") as f:
            json.dump(corners, f, indent=2)

    # Center point is used by the frontend/backend for map centering and ADS-B queries.
    center_lon, center_lat = get_geotiff_center_lon_lat(geotiff_path)

    return {
        "icao": icao,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "diagram_url": f"/static/airports/{icao}/diagram.png",
        "corners_url": f"/static/airports/{icao}/corners.json",
    }
