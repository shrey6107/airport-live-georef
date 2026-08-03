"""FastAPI entry point for the Airport Traffic Dashboard."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from airport_data import get_aircraft_data
from aircraft_track_history import clear_tracks
from airport_lookup import find_nearest_airport
from approach_chart_web import ApproachChartPreparationError
from chart_catalog import (
    ChartCatalogError,
    ChartNotFoundError,
    list_airport_charts,
)
from chart_web import prepare_chart_for_web
from diagram_web import GeoreferencingError, prepare_airport_for_web


app = FastAPI(
    title="Airport Traffic Dashboard",
    description="Web-based airport diagram overlay with live ADS-B traffic.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")


class AirportRequest(BaseModel):
    """Request body for loading an airport."""

    icao: str = Field(..., min_length=3, max_length=4)


class ChartRequest(AirportRequest):
    """Expected JSON body for loading a chart selected from the catalog."""

    chart_id: str = Field(..., min_length=1, max_length=160)


@app.get("/")
def home() -> FileResponse:
    """Serve the main dashboard page."""
    return FileResponse("static/index.html")


@app.get("/aircraft")
def aircraft(lat: float, lon: float, dist: int = 5) -> list[dict]:
    """
    Return live ADS-B aircraft around a given latitude/longitude.

    The frontend stores the selected airport in browser state and sends the
    airport center coordinates with every aircraft request. This avoids using
    a shared global current_airport variable on the backend.
    """
    return get_aircraft_data(lat, lon, dist)


@app.post("/aircraft/tracks/clear", status_code=204)
def clear_aircraft_tracks() -> None:
    """Clear path history when this browser changes its selected airport."""
    clear_tracks()


@app.get("/airport/nearest")
def nearest_airport(lat: float, lon: float) -> dict:
    """
    Return the nearest supported airport for a clicked map location.

    The frontend sends the returned code through the normal chart-discovery flow.
    """
    airport = find_nearest_airport(lat, lon)

    if airport is None:
        raise HTTPException(
            status_code=404,
            detail="No supported airport found near clicked location.",
        )

    return airport


@app.get("/airport/charts")
def airport_charts(icao: str) -> dict:
    """List supported FAA charts without downloading or georeferencing them."""
    try:
        normalized_icao = icao.upper().strip()
        charts = list_airport_charts(normalized_icao)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ChartCatalogError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not charts:
        raise HTTPException(
            status_code=404,
            detail=f"No supported FAA charts found for {normalized_icao}.",
        )

    return {"icao": normalized_icao, "charts": charts}


@app.post("/chart/load")
def load_chart(request: ChartRequest) -> dict:
    """Prepare an airport diagram or approach chart using its existing pipeline."""
    try:
        return prepare_chart_for_web(request.icao, request.chart_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ChartNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GeoreferencingError as exc:
        icao = request.icao.upper().strip()
        raise HTTPException(
            status_code=400,
            detail=f"Unable to georeference {icao}: {exc}",
        ) from exc
    except ApproachChartPreparationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Approach plate georeferencing failed: {exc}",
        ) from exc
    except ChartCatalogError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        print(f"Failed to load chart {request.chart_id} for {request.icao}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load chart for {request.icao.upper().strip()}",
        ) from exc


@app.post("/airport/load")
def load_airport(request: AirportRequest) -> dict:
    """Load and prepare an airport diagram for compatibility with existing clients."""
    try:
        return prepare_airport_for_web(request.icao)

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except GeoreferencingError as exc:
        icao = request.icao.upper().strip()
        raise HTTPException(
            status_code=400,
            detail=f"Unable to georeference {icao}: {exc}",
        ) from exc

    except Exception as exc:
        print(f"Failed to load airport {request.icao}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load airport {request.icao.upper().strip()}",
        ) from exc
