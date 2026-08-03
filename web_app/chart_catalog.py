"""FAA chart metadata lookup for the web app.

Only chart types with a web preparation pipeline are exposed: airport
diagrams (APD) and instrument approach procedures (IAP).
"""

import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from diagram_web import validate_icao


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DTPP_METAFILE_PATH = PROJECT_ROOT / "d-TPP_Metafile.xml"
SUPPORTED_CHART_CODES = {"APD", "IAP"}


class ChartCatalogError(RuntimeError):
    """Raised when FAA chart metadata cannot be loaded."""


class ChartNotFoundError(LookupError):
    """Raised when an airport or chart ID is absent from the catalog."""


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "chart"


def _chart_id(chart_code: str, procedure_id: str, pdf_name: str) -> str:
    if chart_code == "APD":
        return "airport-diagram"

    if procedure_id:
        return f"approach-{_slug(procedure_id)}"

    return f"approach-{_slug(Path(pdf_name).stem)}"


@lru_cache(maxsize=4)
def _load_catalog(xml_file: str) -> dict[str, tuple[dict[str, Any], ...]]:
    path = Path(xml_file)

    if not path.exists():
        raise ChartCatalogError(f"FAA chart metadata file not found: {path}")

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ChartCatalogError(f"Unable to read FAA chart metadata: {path}") from exc

    catalog: dict[str, tuple[dict[str, Any], ...]] = {}

    for airport_element in root.iter("airport_name"):
        icao = (airport_element.attrib.get("icao_ident") or "").strip().upper()

        if not icao:
            continue

        charts = []
        seen_ids: set[str] = set()

        for record in airport_element.findall("record"):
            chart_code = (record.findtext("chart_code") or "").strip().upper()
            chart_name = (record.findtext("chart_name") or "").strip()
            pdf_name = (record.findtext("pdf_name") or "").strip()
            procedure_id = (record.findtext("procuid") or "").strip()

            if chart_code not in SUPPORTED_CHART_CODES or not chart_name or not pdf_name:
                continue

            chart_id = _chart_id(chart_code, procedure_id, pdf_name)

            # Malformed or duplicate FAA records should not make the API ambiguous.
            if chart_id in seen_ids:
                chart_id = f"{chart_id}-{_slug(Path(pdf_name).stem)}"

            if chart_id in seen_ids:
                continue

            seen_ids.add(chart_id)
            chart_type = "airport_diagram" if chart_code == "APD" else "approach"
            charts.append(
                {
                    "id": chart_id,
                    "name": chart_name.title() if chart_code == "APD" else chart_name,
                    "type": chart_type,
                    "procedure": chart_name if chart_code == "IAP" else None,
                    "source_file": pdf_name,
                    "faa_chart_code": chart_code,
                    "faa_procedure_id": procedure_id or None,
                }
            )

        if charts:
            charts.sort(key=lambda chart: (chart["type"] != "airport_diagram", chart["name"]))
            catalog[icao] = tuple(charts)

    return catalog


def list_airport_charts(
    icao: str,
    xml_file: str | Path = DTPP_METAFILE_PATH,
) -> list[dict[str, Any]]:
    """Return all currently supported FAA charts for an airport."""
    normalized_icao = validate_icao(icao)
    charts = _load_catalog(str(Path(xml_file).resolve())).get(normalized_icao, ())
    return [dict(chart) for chart in charts]


def resolve_chart(
    icao: str,
    chart_id: str,
    xml_file: str | Path = DTPP_METAFILE_PATH,
) -> dict[str, Any]:
    """Resolve a chart ID within one airport without trusting client metadata."""
    normalized_icao = validate_icao(icao)
    normalized_chart_id = chart_id.strip().lower()

    if not normalized_chart_id:
        raise ValueError("A chart_id is required.")

    for chart in list_airport_charts(normalized_icao, xml_file):
        if chart["id"] == normalized_chart_id:
            return chart

    raise ChartNotFoundError(
        f"Chart {chart_id!r} was not found for {normalized_icao}."
    )
