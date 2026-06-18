"""
FastAPI entry point for the Airport Traffic Dashboard.

This file connects the browser frontend to the Python backend.

Run locally:
    uvicorn main:app --reload

Expose on local network:
    uvicorn main:app --host 0.0.0.0
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from airport_data import get_aircraft_data
from airport_lookup import find_nearest_airport
from diagram_web import GeoreferencingError, prepare_airport_for_web


# Create the FastAPI application.
app = FastAPI(
    title="Airport Traffic Dashboard",
    description="Web-based airport diagram overlay with live ADS-B traffic.",
    version="0.1.0",
)

# Serve files from the static/ folder.
# This allows the browser to load:
#   - static/index.html
#   - generated airport diagram PNGs
#   - generated corners.json files
app.mount("/static", StaticFiles(directory="static"), name="static")


class AirportRequest(BaseModel):
    """
    Expected JSON body for loading an airport.

    Example request from the frontend:
        {
            "icao": "KSFO"
        }

    The Field validation keeps the airport code between 3 and 4 characters.
    """
    icao: str = Field(..., min_length=3, max_length=4)


@app.get("/")
def home() -> FileResponse:
    """
    Serve the main dashboard page.

    When the user opens http://127.0.0.1:8000, FastAPI returns the
    frontend HTML file.
    """
    return FileResponse("static/index.html")


@app.get("/aircraft")
def aircraft(lat: float, lon: float, dist: int = 2) -> list[dict]:
    """
    Return live ADS-B aircraft around a given latitude/longitude.

    The frontend stores the selected airport in browser state and sends the
    airport center coordinates with every aircraft request. This avoids using
    a shared global current_airport variable on the backend.
    """
    return get_aircraft_data(lat, lon, dist)


@app.get("/airport/nearest")
def nearest_airport(lat: float, lon: float) -> dict:
    """
    Return the nearest supported airport for a clicked map location.

    This endpoint only resolves a click into an airport code. The frontend then
    sends that code through the existing /airport/load georeferencing pipeline.
    """
    airport = find_nearest_airport(lat, lon)

    if airport is None:
        raise HTTPException(
            status_code=404,
            detail="No supported airport found near clicked location.",
        )

    return airport


@app.post("/airport/load")
def load_airport(request: AirportRequest) -> dict:
    """
    Load and prepare an airport diagram for the web app.

    Flow:
        1. Receive ICAO code from the frontend.
        2. Generate/load the airport GeoTIFF using the georeferencing pipeline.
        3. Convert the GeoTIFF into browser-friendly assets:
            - diagram.png
            - corners.json
        4. Return metadata needed by Leaflet:
            - airport center lat/lon
            - diagram URL
            - corners URL
    """
    try:
        return prepare_airport_for_web(request.icao)

    except ValueError as exc:
        # Validation errors, such as invalid ICAO format, should return 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except GeoreferencingError as exc:
        # Expected georeferencing failures should be visible to the browser.
        icao = request.icao.upper().strip()
        raise HTTPException(
            status_code=400,
            detail=f"Unable to georeference {icao}: {exc}",
        ) from exc

    except Exception as exc:
        # Unexpected failures are logged in the backend and returned as 500.
        print(f"Failed to load airport {request.icao}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Unable to load airport {request.icao.upper().strip()}",
        ) from exc
