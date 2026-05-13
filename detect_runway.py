import pymupdf
import rasterio
import math


def euclidean_distance(p1, p2):
    """Return straight-line distance between two PDF points."""
    x1, y1 = p1
    x2, y2 = p2

    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def extract_unique_line_points(path):
    """
    Extract all unique points from line drawing commands in a PDF path.
    Runways in FAA diagrams are usually made of line segments.
    Runways in the FAA airport diagrams are drawn as filled black polygons.
    """
    points = set()

    if path.get("type") == "f" and path.get("fill") == (0.0, 0.0, 0.0):
        for item in path.get("items", []):
            if item[0] == "l":
                _, pt1, pt2 = item
                points.add(pt1)
                points.add(pt2)

    return list(points)


def get_runway_dimensions(points):
    """
    Estimate runway length and width from a set of polygon points.

    The idea:
    - Pick one reference point.
    - Measure distance from it to every other point.
    - The shortest distance is likely the runway width.
    - The second-longest distance is likely the runway length.

    We use second-longest instead of longest because the longest distance
    may be the diagonal corner-to-corner distance.
    """
    if len(points) < 4:
        return None

    reference_point = points[0]

    distances = []

    for point in points[1:]:
        distance = euclidean_distance(reference_point, point)
        distances.append((distance, point))

    distances.sort(key=lambda item: item[0])

    width = distances[0][0]
    length = distances[-2][0]
    length_point = distances[-2][1]

    return {
        "reference_point": reference_point,
        "length_point": length_point,
        "length": length,
        "width": width,
        "aspect_ratio": length / width if width else 0,
    }


def is_runway_path(
    path,
    min_aspect_ratio=20,
    min_width=2,
    max_width=12,
    max_points=15
):
    """
    Detect whether a PDF drawing path is likely a runway.

    Main runway pattern:
    - filled black shape
    - made from a small number of points
    - long and thin geometry
    - high length / width ratio
    """

    points = extract_unique_line_points(path)

    # Ignore complex shapes; runway polygons are usually simple.
    if len(points) >= max_points:
        return False, None

    dimensions = get_runway_dimensions(points)

    if dimensions is None:
        return False, None

    aspect_ratio = dimensions["aspect_ratio"]
    width = dimensions["width"]

    is_runway = (
        aspect_ratio > min_aspect_ratio
        and min_width < width < max_width
    )

    return is_runway, dimensions

def detect_runways(paths, geotiff_path, dpi=300):
    runway_results = []

    for path in paths:
        is_runway, dimensions = is_runway_path(path)

        if is_runway:
            point1 = dimensions["reference_point"]
            point2 = dimensions["length_point"]

            heading_info = get_runway_heading_from_pdf_points(
                point1,
                point2,
                geotiff_path,
                dpi
            )

            runway_results.append({
                "path": path, # For debugging
                "seqno": path.get("seqno"), # For debugging
                "dimensions": dimensions,
                "heading_info": heading_info,
            })

            print("Runway detected")
            print("Lat/Lon:", heading_info["point1_latlon"], heading_info["point2_latlon"])
            print("Heading:", heading_info["heading"], "\tOpposite heading:", heading_info["opposite_heading"])
            print()

    return runway_results #For Debugging

def points_to_latlon(pdf_x, pdf_y, geotiff_path, dpi=300):
    """
    Convert a PDF coordinate point to lat/lon using your generated GeoTIFF transform.
    """
    scale = dpi / 72.0

    col = pdf_x * scale
    row = pdf_y * scale

    with rasterio.open(geotiff_path) as src:
        lon, lat = src.xy(row, col)

    return lat, lon

def get_runway_heading_from_pdf_points(point1, point2, geotiff_path, dpi=300):
    """
    Convert two runway PDF points to lat/lon and calculate runway heading.
    """
    x1, y1 = point1
    x2, y2 = point2

    lat1, lon1 = points_to_latlon(x1, y1, geotiff_path, dpi)
    lat2, lon2 = points_to_latlon(x2, y2, geotiff_path, dpi)

    heading = calculate_heading(lat1, lon1, lat2, lon2)
    opposite_heading = (heading + 180) % 360

    return {
        "point1_pdf": point1,
        "point2_pdf": point2,
        "point1_latlon": (lat1, lon1),
        "point2_latlon": (lat2, lon2),
        "heading": heading,
        "opposite_heading": opposite_heading,
    }

def calculate_heading(lat1, lon1, lat2, lon2):
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)

    x = math.sin(dlon) * math.cos(lat2_rad)
    y = (
        math.cos(lat1_rad) * math.sin(lat2_rad)
        - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon)
    )

    heading = math.degrees(math.atan2(x, y))
    return round((heading + 360) % 360)

name = "KSFO"
page = pymupdf.open(f'{name}.PDF')[0]
paths = page.get_drawings()
runway_path = detect_runways(paths, f'{name}_affine.tif') #Get .tif from airport_live_georef.py
print("Total Runway", len(runway_path))