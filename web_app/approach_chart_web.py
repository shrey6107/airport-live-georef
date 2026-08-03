"""Prepare FAA instrument approach charts as Leaflet image overlays."""

import json
import sys
from pathlib import Path
from typing import Any

import rasterio
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_APP_DIR = Path(__file__).resolve().parent
sys.path.append(str(PROJECT_ROOT))

from approach_plate_georeferencing import (  # noqa: E402
    FAA_BASE_URL,
    download_pdf,
    georef_pdf_using_viewport,
)
from diagram_web import (  # noqa: E402
    extract_rotated_corners,
    read_geotiff_as_rgb,
    validate_icao,
)
from airport_lookup import find_airport_by_code  # noqa: E402


APPROACH_ZOOM = 4
STATIC_CHARTS_DIR = WEB_APP_DIR / "static" / "charts"
AIRPORT_STUFF_DIR = WEB_APP_DIR / "airport_stuff"


class ApproachChartPreparationError(RuntimeError):
    """Raised when an approach chart cannot be downloaded or georeferenced."""


def _approach_paths(icao: str, chart_id: str) -> tuple[Path, Path, Path]:
    source_dir = AIRPORT_STUFF_DIR / icao / "approaches"
    pdf_path = source_dir / f"{chart_id}.pdf"
    geotiff_path = source_dir / f"{chart_id}_affine.tif"
    static_dir = STATIC_CHARTS_DIR / icao / chart_id
    return pdf_path, geotiff_path, static_dir


def _write_web_assets(geotiff_path: Path, output_dir: Path) -> tuple[Path, Path, float, float]:
    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / "diagram.png"
    corners_path = output_dir / "corners.json"

    with rasterio.open(geotiff_path) as src:
        image = read_geotiff_as_rgb(src)
        Image.fromarray(image).save(png_path)
        corners = extract_rotated_corners(src)
        center_lon, center_lat = src.xy(src.height // 2, src.width // 2)

    with corners_path.open("w", encoding="utf-8") as file:
        json.dump(corners, file, indent=2)

    return png_path, corners_path, float(center_lat), float(center_lon)


def prepare_approach_chart_for_web(icao: str, chart: dict[str, Any]) -> dict[str, Any]:
    """Download/cache, georeference, and publish one resolved IAP record."""
    normalized_icao = validate_icao(icao)

    if chart.get("type") != "approach":
        raise ValueError("The selected chart is not an approach chart.")

    chart_id = str(chart["id"])
    source_file = str(chart["source_file"])
    pdf_path, geotiff_path, static_dir = _approach_paths(normalized_icao, chart_id)

    try:
        if not geotiff_path.exists():
            if not pdf_path.exists():
                download_pdf(f"{FAA_BASE_URL}/{source_file}", pdf_path)

            georef_pdf_using_viewport(
                pdf_path=pdf_path,
                output_tif=geotiff_path,
                page_number=0,
                zoom=APPROACH_ZOOM,
            )

        _, _, chart_center_lat, chart_center_lon = _write_web_assets(
            geotiff_path,
            static_dir,
        )
    except Exception as exc:
        raise ApproachChartPreparationError(str(exc)) from exc

    # Keep the existing ADS-B radius centered on the selected airport. The map
    # overlay itself continues to use the chart's georeferenced corner bounds.
    airport = find_airport_by_code(normalized_icao)
    center_lat = airport["lat"] if airport else chart_center_lat
    center_lon = airport["lon"] if airport else chart_center_lon

    return {
        "icao": normalized_icao,
        "chart_id": chart_id,
        "chart_name": chart["name"],
        "chart_type": "approach",
        "center_lat": center_lat,
        "center_lon": center_lon,
        "diagram_url": f"/static/charts/{normalized_icao}/{chart_id}/diagram.png",
        "corners_url": f"/static/charts/{normalized_icao}/{chart_id}/corners.json",
    }
