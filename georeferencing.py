import os
import re
import pymupdf
import rasterio
import requests

from rasterio.control import GroundControlPoint
from rasterio.transform import from_gcps

HTTP_SESSION = requests.Session()

AIRPORT_NAME = "KCID"

TEMP_PNG_PATH = "temp_airport_diagram.png"

DPI = 220

# -----------------------------------------------------------------------------
# FAA airport diagram download
# -----------------------------------------------------------------------------
def download_airport_diagram(airport_name: str, pdf_path: str) -> None:

    if os.path.exists(pdf_path):
        print(f"{pdf_path} already exists")
        return

    url = (
        f"https://www.flightaware.com/resources/airport/{airport_name}/APD/AIRPORT+DIAGRAM/pdf"
    )

    response = HTTP_SESSION.get(url, timeout=30)
    response.raise_for_status()

    with open(pdf_path, "wb") as file:
        file.write(response.content)

    print(f"Downloaded airport diagram: {pdf_path}")


# -----------------------------------------------------------------------------
# PDF coordinate extraction
# -----------------------------------------------------------------------------
def get_coordinate_labels_and_orientation(
    pdf_path: str,
) -> tuple[dict[str, tuple[float, float, float, float]], bool]:

    coords_dict = {}
    north_up_flag = False

    with pymupdf.open(pdf_path) as doc:

        page = doc[0]

        for word_info in page.get_text("words"):

            x0, y0, x1, y1, word, *_ = word_info

            if "°" in word and "'" in word:
                coords_dict[word] = (x0, y0, x1, y1)

        for drawing in page.get_drawings():

            x0, y0, x1, y1 = drawing["rect"]

            height = y1 - y0
            width = x1 - x0

            if (10.65 <= height <= 10.68) and (4.88 <= width <= 4.91):
                north_up_flag = True
                break

    print(f"North-up orientation detected: {north_up_flag}")

    return coords_dict, north_up_flag


# -----------------------------------------------------------------------------
# Gridline detection
# -----------------------------------------------------------------------------
def get_gridline_control_values(
    pdf_path: str,
    coords_dict: dict[str, tuple[float, float, float, float]],
    north_up_flag: bool,
) -> tuple[dict[str, float], dict[str, float]]:

    with pymupdf.open(pdf_path) as doc:
        page = doc[0]
        paths = page.get_drawings()

    largest_rectangle_area = 0
    grid_frame = None

    for path in paths:

        items = path.get("items", [])

        if not items:
            continue

        if items[0][0] == "qu":

            x0, y0, x1, y1 = path["rect"]

            area = (x1 - x0) * (y1 - y0)

            if area > largest_rectangle_area:

                largest_rectangle_area = area
                grid_frame = (x0, y0, x1, y1)

    if grid_frame is None:
        raise RuntimeError("Could not find airport diagram frame")

    X0, Y0, X1, Y1 = grid_frame

    vertical_pts = []
    horizontal_pts = []

    edge_tolerance = 0.5

    for path in paths:

        items = path.get("items", [])

        if not items or items[0][0] != "l":
            continue

        x0, y0, x1, y1 = path["rect"]

        if Y0 <= y0 <= Y0 + edge_tolerance and X0 < x0 < X1:
            vertical_pts.append(x0)

        elif Y1 - edge_tolerance <= y1 <= Y1 and X0 < x1 < X1:
            vertical_pts.append(x1)

        if X0 <= x0 <= X0 + edge_tolerance and Y0 < y0 < Y1:
            horizontal_pts.append(y0)

        elif X1 - edge_tolerance <= x1 <= X1 and Y0 < y1 < Y1:
            horizontal_pts.append(y1)

    grid_x_dict = {}
    grid_y_dict = {}

    for label, bbox in coords_dict.items():

        x0, y0, x1, y1 = bbox

        x_axis_label = "W"
        y_axis_label = "N"

        if not north_up_flag:
            x_axis_label = "N"
            y_axis_label = "W"

        if x_axis_label in label:

            for x in vertical_pts:

                if x0 < x < x1:
                    grid_x_dict[label] = x

        if y_axis_label in label:

            for y in horizontal_pts:

                if y0 < y < y1:
                    grid_y_dict[label] = y

    if not north_up_flag:
        grid_x_dict, grid_y_dict = grid_y_dict, grid_x_dict

    return grid_x_dict, grid_y_dict


# -----------------------------------------------------------------------------
# Coordinate parsing
# -----------------------------------------------------------------------------
def parse_coord(label: str) -> float:

    match = re.match(
        r"^\s*(\d+(?:\.\d+)?)°\s*(\d+(?:\.\d+)?)'\s*([NSEW])\s*$",
        label,
    )

    if not match:
        raise ValueError(f"Could not parse coordinate label: {label}")

    degrees = float(match.group(1))
    minutes = float(match.group(2))
    hemisphere = match.group(3).upper()

    value = degrees + minutes / 60.0

    if hemisphere in ("W", "S"):
        value = -value

    return value


# -----------------------------------------------------------------------------
# GeoTIFF generation
# -----------------------------------------------------------------------------
def create_georeferenced_tiff(
    pdf_path: str,
    output_tif_path: str,
    x_dict: dict[str, float],
    y_dict: dict[str, float],
    north_up_flag: bool,
    dpi: int = DPI,
) -> None:

    lon_to_pdf_x = {parse_coord(label): x for label, x in x_dict.items()}
    lat_to_pdf_y = {parse_coord(label): y for label, y in y_dict.items()}

    scale = dpi / 72.0

    with pymupdf.open(pdf_path) as doc:

        page = doc[0]

        pix = page.get_pixmap(dpi=dpi)

        pix.save(TEMP_PNG_PATH)

    with rasterio.open(TEMP_PNG_PATH) as src:

        data = src.read()
        profile = src.profile.copy()

    lon_to_col_or_row = {
        lon: value * scale
        for lon, value in lon_to_pdf_x.items()
    }

    lat_to_row_or_col = {
        lat: value * scale
        for lat, value in lat_to_pdf_y.items()
    }

    gcps = []

    for lon, x_value in lon_to_col_or_row.items():

        for lat, y_value in lat_to_row_or_col.items():

            row = y_value
            col = x_value

            if not north_up_flag:
                row = x_value
                col = y_value

            gcps.append(
                GroundControlPoint(
                    row=row,
                    col=col,
                    x=lon,
                    y=lat,
                )
            )

    transform = from_gcps(gcps)

    profile.update(
        driver="GTiff",
        crs="EPSG:4326",
        transform=transform,
    )

    with rasterio.open(output_tif_path, "w", **profile) as dst:
        dst.write(data)

    if os.path.exists(TEMP_PNG_PATH):
        os.remove(TEMP_PNG_PATH)

    print(f"Saved GeoTIFF: {output_tif_path}")


def get_geotiff_center_lon_lat(tif_path: str) -> tuple[float, float]:

    with rasterio.open(tif_path) as src:

        center_row = src.height // 2
        center_col = src.width // 2

        lon, lat = src.xy(center_row, center_col)

        return float(lon), float(lat)

def ensure_geotiff_exists(airport_name: str) -> str:

    pdf_path = f"{airport_name}.pdf"

    geotiff_path = f"{airport_name}_affine.tif"

    if os.path.exists(geotiff_path):

        print(f"{geotiff_path} already exists")

        return geotiff_path

    download_airport_diagram(
        airport_name,
        pdf_path,
    )

    coords_dict, north_up_flag = (
        get_coordinate_labels_and_orientation(
            pdf_path
        )
    )

    x_dict, y_dict = get_gridline_control_values(
        pdf_path,
        coords_dict,
        north_up_flag,
    )

    create_georeferenced_tiff(
        pdf_path,
        geotiff_path,
        x_dict,
        y_dict,
        north_up_flag,
    )

    return geotiff_path

def main() -> None:
    geotiff_path = ensure_geotiff_exists(
        AIRPORT_NAME
    )

    center_lon, center_lat = (
        get_geotiff_center_lon_lat(
            geotiff_path
        )
    )

    print(center_lon, center_lat)

if __name__ == "__main__":
    geotiff_path = ensure_geotiff_exists(
        AIRPORT_NAME
    )
    center_lon, center_lat = (
        get_geotiff_center_lon_lat(
            geotiff_path
        )
    )
    print("Generated:", geotiff_path)

    print(
        "Center:",
        center_lon,
        center_lat,
    )