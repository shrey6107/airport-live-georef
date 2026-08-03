"""Route resolved chart requests to the appropriate web preparation pipeline."""

from typing import Any

from approach_chart_web import prepare_approach_chart_for_web
from chart_catalog import resolve_chart
from diagram_web import prepare_airport_for_web, validate_icao


def prepare_chart_for_web(icao: str, chart_id: str) -> dict[str, Any]:
    normalized_icao = validate_icao(icao)
    chart = resolve_chart(normalized_icao, chart_id)

    if chart["type"] == "airport_diagram":
        result = prepare_airport_for_web(normalized_icao)
        return {
            **result,
            "chart_id": chart["id"],
            "chart_name": chart["name"],
            "chart_type": chart["type"],
        }

    return prepare_approach_chart_for_web(normalized_icao, chart)
