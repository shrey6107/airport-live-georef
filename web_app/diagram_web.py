"""Convert airport GeoTIFFs into PNG and corner metadata for the web maps."""

import json
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from georeferencing_with_glyph_fallback import (
    GeoreferencingError,
    ensure_geotiff_exists,
    get_geotiff_center_lon_lat,
    read_extraction_method,
)


STATIC_AIRPORTS_DIR = Path("static") / "airports"

# Some supported airport identifiers contain three characters.
ICAO_PATTERN = re.compile(r"^[A-Z0-9]{3,4}$")


def validate_icao(icao: str) -> str:
    """Normalize and validate an ICAO-style airport identifier."""
    normalized = icao.upper().strip()

    if not ICAO_PATTERN.match(normalized):
        raise ValueError(
            "Invalid airport identifier. Use an ICAO-style code like KSFO, KLAX, or KJFK."
        )

    return normalized


def normalize_to_uint8(img: np.ndarray) -> np.ndarray:
    """Scale raster values into the uint8 range used by browser images."""
    if img.dtype == np.uint8:
        return img

    img_min = np.nanmin(img)
    img_max = np.nanmax(img)

    if img_max - img_min < 1e-9:
        return np.zeros_like(img, dtype=np.uint8)

    return ((img - img_min) / (img_max - img_min) * 255).astype(np.uint8)


def read_geotiff_as_rgb(src: rasterio.DatasetReader) -> np.ndarray:
    """Read the first three bands, or expand a single band, as RGB."""
    if src.count >= 3:
        img = src.read([1, 2, 3])
        img = np.transpose(img, (1, 2, 0))
        return normalize_to_uint8(img)

    band = normalize_to_uint8(src.read(1))
    return np.dstack([band, band, band])


def extract_rotated_corners(src: rasterio.DatasetReader) -> dict[str, list[float]]:
    """
    Return the three corners needed by the rotated overlay plugin.

    Rasterio uses longitude/latitude order; Leaflet expects latitude/longitude.
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
    """Generate web-ready image assets and metadata for one airport."""
    icao = validate_icao(icao)

    output_dir = STATIC_AIRPORTS_DIR / icao
    output_dir.mkdir(parents=True, exist_ok=True)

    png_path = output_dir / "diagram.png"
    corners_path = output_dir / "corners.json"

    geotiff_path = ensure_geotiff_exists(icao)

    with rasterio.open(geotiff_path) as src:
        img = read_geotiff_as_rgb(src)
        Image.fromarray(img).save(png_path)

        corners = extract_rotated_corners(src)

        with corners_path.open("w", encoding="utf-8") as f:
            json.dump(corners, f, indent=2)

    center_lon, center_lat = get_geotiff_center_lon_lat(geotiff_path)

    return {
        "icao": icao,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "diagram_url": f"/static/airports/{icao}/diagram.png",
        "corners_url": f"/static/airports/{icao}/corners.json",
        "extraction_method": read_extraction_method(geotiff_path),
    }
