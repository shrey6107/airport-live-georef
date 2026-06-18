# Airport Traffic Visualization & Operations Analytics

## Updates

### 06-17-26
1. Added click to load support in the web app. Airports can now be selected directly on the map, no need to manually enter airport code before loading and georeferencing the airport diagram.
2. Improved georeferencing fault detection. The script now detects common failures like unreadable lat/lon labels, unsupported PDF diagrams and affine transform failures when georeferencing cannot be completed.

### 06-10-26
1. Added final landing/takeoff/go-around detection script with SQLite database to store classified events with metadata (event time, callsign, aircraft type, airport name) to gain operational insights.

### 06-04-26
1. Added web app support using FastAPI, uvicorn and Leaflet


### 05-29-26

1. Added continuous landing/takeoff detection script. Classification filters generates vote over time which are stored in rolling deque. Aircrafts are classified once the confidence exceeds customizable threshold (currently at 70%). Next, working on storing classified aircraft and telemetry data in SQLite for analysis.

### 05-24-26

1. Added runway detection to identify runways and compute true runway headings from FAA airport diagrams

2. Refactored the original `airport_live_georef.py` into separate standalone georeferencing and visualization scripts

3. Added instantaneous landing/takeoff detection script using single-poll ADS-B telemetry within a 2 NM airport radius

4. Improved Qt visualization performance using reusable labels/brushes, thread overlap protection, and a dynamic display field system for adding new visualization parameters without renderer modifications

### 04-27-26

1. Added initial `airport_live_georef.py` prototype combining georeferencing and live visualization logic

---

## Known Issues

1. The free ADS-B API currently used is unreliable and occasionally returns incomplete or inconsistent telemetry data

2. Some FAA airport diagram PDFs contain broken or incomplete text layers, while others encode latitude/longitude labels as vector graphics instead of text, preventing automatic georeferencing

---

## How to Run

### Requirements
- Python 3.11+
- Internet connection

### 1. Create a virtual environment

MacOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows
```bash
python3 -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the script

```bash
cd web_app
uvicorn main:app
```
The application will be available at
```bash
http://127.0.0.1:8000
```
Open the URL in a browser.

### 4. Load an airport

You can load an airport in two ways:

* Enter an ICAO code (e.g., KSFO, KLAX, KORD) and click Load Airport
* Click an airport directly on the map and select Load Airport from the popup

The system will automatically:

1. Download the FAA airport diagram (if required)
2. Georeference the diagram
3. Generate the GeoTIFF and web overlay assets
4. Display the airport diagram in the browser

### 5. Enable live traffic

When live traffic is enabled, the application:

1. Queries the ADS-B API
2. Retrieves nearby aircraft telemetry
3. Projects aircraft positions onto the georeferenced airport diagram
4. Updates aircraft positions in real time

---

## Overview

This project automatically converts FAA Airport Diagram PDFs into geo-referenced maps and overlays real-time aircraft traffic using ADS-B telemetry.

The system extracts latitude/longitude reference information directly from airport diagrams, generates GeoTIFFs without relying on external coordinate datasets, and provides both desktop and web-based visualization of airport traffic.

The project also includes aircraft event classification logic capable of identifying arrivals, departures, and go-arounds from live ADS-B telemetry. Classified events are stored in SQLite and can be used for operational analytics such as runway utilization, traffic volume analysis, aircraft type distribution, and peak activity periods.

The goal was to turn static airport diagrams into dynamic systems that allow real-time visualization and analysis of aircraft movement on the ground.

---

## Motivation

I like listening to ground and tower frequencies but it was difficult to keep track of aircraft positions and follow the instructions they were given. I had to keep switching between airport diagram pdf and Flightradar24. 

I built the project to solve this problem by combining both of them into a single system.

---

## Key Features

- Automatic georeferencing of FAA airport diagrams  
- Works without external coordinate datasets (fully self-contained)  
- Live ADS-B aircraft tracking  
- Real-time overlay of aircraft positions on airport diagrams  

---

## How It Works

### 1. Diagram Processing
- Loads FAA airport diagram PDFs
- Extracts vector gridline geometry using PyMuPDF
- Identifies latitude/longitude grid intersections

### 2. Georeferencing
- Converts DMS coordinate labels → decimal lat/lon
- Constructs Ground Control Points (GCPs)
- Generates affine transformation using rasterio
- Supports both:
  - north-up diagrams  
  - rotated diagrams (e.g., SFO)

### 3. Real-Time Data Integration
- Fetches live ADS-B data via API
- Transforms WGS84 coordinates → GeoTIFF coordinate system using pyproj

### 4. Visualization
- Renders GeoTIFF using PyQt6 + pyqtgraph
- Applies affine transformation to correctly align the image
- Overlays aircraft positions and metadata (callsign, altitude, track)

---

## Tech Stack

Backend

* Python
* FastAPI
* Uvicorn

Frontend

* HTML
* CSS
* JavaScript
* Leaflet

Geospatial Processing

* PyMuPDF
* Rasterio
* PyProj
* GeoTIFF

Data Processing

* NumPy
* SQLite

Visualization

* Leaflet
* PyQt6
* pyqtgraph

Data Sources

* FAA Airport Diagram PDFs
* ADS-B REST APIs 

---

## Results

- Achieved ~1 meter average spatial alignment accuracy  
- Successfully visualized real-time aircraft movement on airport surfaces  
- Built a fully automated pipeline with no reliance on external coordinate datasets  

---

## Future Improvements

- Cloud deployment / 24x7 event
- ATC audio integration and transcription
