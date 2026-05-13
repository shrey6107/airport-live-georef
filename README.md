# Airport Traffic Visualization

## Overview

This project is a geospatial data pipeline and real-time visualization system that converts FAA airport diagrams into geo-referenced maps and overlays live aircraft traffic using ADS-B data.

The goal was to turn static airport diagrams into dynamic systems that allow real-time visualization and analysis of aircraft movement on the ground.

---

## Motivation

While listening to ground and tower frequencies, I found it difficult to track aircraft positions and had to constantly switch between airport diagram pdf and Flightradar24 to understand where aircraft were located.

This project was built to solve that problem by bringing everything into a single system.

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

- Python
- PyMuPDF – PDF parsing & vector extraction  
- rasterio – geospatial transformations & GeoTIFF generation  
- pyproj – coordinate transformations  
- NumPy – data processing  
- PyQt6 + pyqtgraph – real-time visualization  
- Requests – API integration  

---

## Results

- Achieved ~1 meter average spatial alignment accuracy  
- Successfully visualized real-time aircraft movement on airport surfaces  
- Built a fully automated pipeline with no reliance on external coordinate datasets  

---

## How to Run

### 1. Install dependencies

```bash
pip install pymupdf rasterio pyproj pyqt6 pyqtgraph requests numpy
```

### 2. Run the script

```bash
python airport_live_georef.py
```

### 3. What you’ll see

- Airport diagram (GeoTIFF)
- Live aircraft positions updating in real time
- Aircraft labels (callsign, altitude, track)

---

## Future Improvements

- Better label rendering / clustering
- Web-based visualization (instead of PyQt)
- 📊 Airport operations analytics:
  - runway usage  
  - aircraft type distribution  
  - peak traffic periods  
- Integration with historical ADS-B datasets for deeper analytics

