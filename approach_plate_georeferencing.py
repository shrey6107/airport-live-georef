import re
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import rasterio
import requests
from rasterio.control import GroundControlPoint
from rasterio.transform import from_gcps


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

XML_FILE = Path("d-TPP_Metafile.xml")

AIRPORT = "KHWD"

# This must match the FAA publication cycle used by your XML metafile.
FAA_CYCLE = "2607"

FAA_BASE_URL = f"https://aeronav.faa.gov/d-tpp/{FAA_CYCLE}"

# Save downloaded PDFs and generated GeoTIFFs beside this script.
OUTPUT_DIRECTORY = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# XML lookup
# ---------------------------------------------------------------------------

def build_icao_lookup(xml_file):
    """
    Build a dictionary containing all IAP procedures grouped by ICAO airport.
    """
    xml_file = Path(xml_file)

    if not xml_file.exists():
        raise FileNotFoundError(f"XML metafile not found: {xml_file.resolve()}")

    root = ET.parse(xml_file).getroot()
    lookup = {}

    for airport_element in root.iter("airport_name"):
        icao = airport_element.attrib.get("icao_ident")
        alnum = airport_element.attrib.get("alnum")

        if not icao or not alnum:
            continue

        procedures = []

        for record in airport_element.findall("record"):
            chart_code = record.findtext("chart_code")
            chart_name = record.findtext("chart_name")
            pdf_name = record.findtext("pdf_name")

            if chart_code != "IAP":
                continue

            if not chart_name or not pdf_name:
                continue

            procedures.append(
                {
                    "chart_name": chart_name.strip(),
                    "pdf_name": pdf_name.strip(),
                }
            )

        if procedures:
            lookup[icao.strip().upper()] = {
                "alnum": alnum.zfill(5),
                "procedures": procedures,
            }

    return lookup


def find_procedure(lookup, airport, procedure_name):
    """
    Find an exact procedure-name match for an airport.
    """
    airport = airport.strip().upper()
    procedure_name = procedure_name.strip()

    if airport not in lookup:
        raise ValueError(f"Airport {airport!r} was not found in the XML metafile.")

    for procedure in lookup[airport]["procedures"]:
        if procedure["chart_name"] == procedure_name:
            return procedure

    available_names = [
        procedure["chart_name"]
        for procedure in lookup[airport]["procedures"]
    ]

    raise ValueError(
        f"Procedure {procedure_name!r} was not found for {airport}.\n"
        f"Available procedures:\n- " + "\n- ".join(available_names)
    )


# ---------------------------------------------------------------------------
# Filename handling
# ---------------------------------------------------------------------------

def sanitize_filename(value):
    """
    Replace characters that cannot safely appear in filenames.

    A normal slash cannot be preserved because macOS, Linux, and Windows
    treat it as a path separator.
    """
    value = value.strip()

    # Replace slash with a hyphen.
    value = value.replace("/", "-")
    value = value.replace("\\", "-")

    # Replace other characters that are invalid on Windows and problematic
    # on other platforms.
    value = re.sub(r'[<>:"|?*]', "-", value)

    # Collapse repeated whitespace.
    value = re.sub(r"\s+", " ", value)

    # Remove trailing dots and spaces.
    return value.strip(" .")


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

def download_pdf(url, destination):
    """
    Download a PDF and verify that the response contains a valid PDF header.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading from:\n{url}")
    print(f"\nSaving to:\n{destination}")

    try:
        response = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Safari/537.36"
                )
            },
        )

        response.raise_for_status()

    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Could not download the FAA PDF.\n"
            f"URL: {url}\n"
            f"Error: {exc}"
        ) from exc

    content = response.content

    if not content:
        raise ValueError(f"The FAA response was empty: {url}")

    if not content.startswith(b"%PDF"):
        content_type = response.headers.get("Content-Type", "unknown")

        preview = content[:200].decode(
            "utf-8",
            errors="replace",
        )

        raise ValueError(
            "The downloaded response is not a PDF.\n"
            f"URL: {url}\n"
            f"Content-Type: {content_type}\n"
            f"Response preview: {preview!r}"
        )

    destination.write_bytes(content)

    print(
        f"\nSuccessfully downloaded:\n"
        f"{destination}\n"
        f"Size: {destination.stat().st_size:,} bytes"
    )

    return destination


# ---------------------------------------------------------------------------
# PDF geospatial viewport extraction
# ---------------------------------------------------------------------------

def numbers_from_array(text):
    """
    Extract numeric values from a PDF array string.

    Example:
        [37.111799 -122.44436 37.11123 -121.85565]
    """
    pattern = r"[-+]?(?:\d*\.\d+|\d+)"
    return [float(value) for value in re.findall(pattern, text)]


def extract_viewport_geo(pdf_path, page_number=0):
    """
    Extract /BBox, /GPTS, and /LPTS information from a PDF viewport.
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")

    doc = fitz.open(str(pdf_path))

    try:
        if page_number < 0 or page_number >= doc.page_count:
            raise IndexError(
                f"Page {page_number} does not exist. "
                f"The PDF contains {doc.page_count} page(s)."
            )

        page = doc[page_number]

        kind, viewport_text = doc.xref_get_key(page.xref, "VP")

        if kind == "null":
            raise ValueError(
                f"No /VP viewport was found on page {page_number}."
            )

        bbox_match = re.search(
            r"/BBox\s*\[([^\]]+)\]",
            viewport_text,
            re.DOTALL,
        )
        gpts_match = re.search(
            r"/GPTS\s*\[([^\]]+)\]",
            viewport_text,
            re.DOTALL,
        )
        lpts_match = re.search(
            r"/LPTS\s*\[([^\]]+)\]",
            viewport_text,
            re.DOTALL,
        )

        if not bbox_match:
            raise ValueError("The viewport does not contain /BBox.")

        if not gpts_match:
            raise ValueError("The viewport does not contain /GPTS.")

        if not lpts_match:
            raise ValueError("The viewport does not contain /LPTS.")

        bbox = numbers_from_array(bbox_match.group(1))
        gpts = numbers_from_array(gpts_match.group(1))
        lpts = numbers_from_array(lpts_match.group(1))

        if len(bbox) != 4:
            raise ValueError(
                f"Expected four /BBox values, but found {len(bbox)}."
            )

        if len(gpts) % 2 != 0:
            raise ValueError(
                "/GPTS must contain latitude/longitude pairs."
            )

        if len(lpts) % 2 != 0:
            raise ValueError(
                "/LPTS must contain normalized coordinate pairs."
            )

        if len(gpts) != len(lpts):
            raise ValueError(
                "/GPTS and /LPTS contain different numbers of values."
            )

        return {
            "bbox": bbox,
            "gpts": gpts,
            "lpts": lpts,
            "page_rect": page.rect,
            "transformation_matrix": page.transformation_matrix,
            "viewport_raw": viewport_text,
        }

    finally:
        doc.close()


def viewport_to_gcps(pdf_path, page_number=0, zoom=4):
    """
    Convert viewport control points into Rasterio GroundControlPoints.
    """
    data = extract_viewport_geo(
        pdf_path=pdf_path,
        page_number=page_number,
    )

    bbox = data["bbox"]
    gpts = data["gpts"]
    lpts = data["lpts"]
    pdf_to_mupdf = data["transformation_matrix"]

    x0, y0, x1, y1 = bbox

    viewport_width = x1 - x0
    viewport_height = y1 - y0

    if viewport_width == 0 or viewport_height == 0:
        raise ValueError("The viewport /BBox has zero width or height.")

    gcps = []

    for index in range(0, len(lpts), 2):
        normalized_x = lpts[index]
        normalized_y = lpts[index + 1]

        latitude = gpts[index]
        longitude = gpts[index + 1]

        # Convert normalized viewport coordinates into PDF coordinates.
        pdf_x = x0 + normalized_x * viewport_width
        pdf_y = y0 + normalized_y * viewport_height

        # Convert PDF bottom-left coordinates to PyMuPDF top-left coordinates.
        mupdf_point = fitz.Point(pdf_x, pdf_y) * pdf_to_mupdf

        # Convert page coordinates into rendered pixel coordinates.
        column = mupdf_point.x * zoom
        row = mupdf_point.y * zoom

        gcps.append(
            GroundControlPoint(
                row=row,
                col=column,
                x=longitude,
                y=latitude,
                z=0,
            )
        )

    if len(gcps) < 3:
        raise ValueError(
            f"At least three GCPs are required, but only {len(gcps)} were found."
        )

    return gcps


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def render_pdf_page_to_array(pdf_path, page_number=0, zoom=4):
    """
    Render one PDF page into a NumPy RGB image array.
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")

    if zoom <= 0:
        raise ValueError("Zoom must be greater than zero.")

    doc = fitz.open(str(pdf_path))

    try:
        if page_number < 0 or page_number >= doc.page_count:
            raise IndexError(
                f"Page {page_number} does not exist. "
                f"The PDF contains {doc.page_count} page(s)."
            )

        page = doc[page_number]
        matrix = fitz.Matrix(zoom, zoom)

        pixmap = page.get_pixmap(
            matrix=matrix,
            alpha=False,
            colorspace=fitz.csRGB,
        )

        array = np.frombuffer(
            pixmap.samples,
            dtype=np.uint8,
        )

        array = array.reshape(
            pixmap.height,
            pixmap.width,
            pixmap.n,
        )

        return array, pixmap.width, pixmap.height

    finally:
        doc.close()


# ---------------------------------------------------------------------------
# GeoTIFF creation
# ---------------------------------------------------------------------------

def georef_pdf_using_viewport(
    pdf_path,
    output_tif,
    page_number=0,
    zoom=4,
):
    """
    Render a geospatial FAA PDF and save it as a GeoTIFF.
    """
    pdf_path = Path(pdf_path)
    output_tif = Path(output_tif)

    output_tif.parent.mkdir(parents=True, exist_ok=True)

    image_array, width, height = render_pdf_page_to_array(
        pdf_path=pdf_path,
        page_number=page_number,
        zoom=zoom,
    )

    gcps = viewport_to_gcps(
        pdf_path=pdf_path,
        page_number=page_number,
        zoom=zoom,
    )

    transform = from_gcps(gcps)

    # Rasterio uses bands, rows, columns.
    bands_first = np.transpose(
        image_array,
        (2, 0, 1),
    )

    with rasterio.open(
        output_tif,
        mode="w",
        driver="GTiff",
        height=height,
        width=width,
        count=bands_first.shape[0],
        dtype=bands_first.dtype,
        crs="EPSG:4326",
        transform=transform,
        compress="deflate",
    ) as destination:
        destination.write(bands_first)

    print(f"\nSaved GeoTIFF:\n{output_tif}")
    print("\nTransform:")
    print(transform)

    return output_tif


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def main():
    lookup = build_icao_lookup(XML_FILE)

    airport = AIRPORT.strip().upper()

    if airport not in lookup:
        raise ValueError(
            f"Airport {airport!r} was not found in {XML_FILE}."
        )

    procedures = lookup[airport]["procedures"]

    print(f"Available IAP procedures for {airport}:\n")

    for procedure in procedures:
        print(procedure["chart_name"])

    procedure_name = input("\nEnter procedure name: ").strip()

    selected_procedure = find_procedure(
        lookup=lookup,
        airport=airport,
        procedure_name=procedure_name,
    )

    faa_pdf_name = selected_procedure["pdf_name"]

    safe_procedure_name = sanitize_filename(procedure_name)

    local_pdf_path = (
        OUTPUT_DIRECTORY
        / f"{airport}_{safe_procedure_name}.pdf"
    )

    output_tif_path = (
        OUTPUT_DIRECTORY
        / f"{airport}_{safe_procedure_name}.tif"
    )

    pdf_url = f"{FAA_BASE_URL}/{faa_pdf_name}"

    print(f"\nSelected chart: {procedure_name}")
    print(f"FAA PDF filename: {faa_pdf_name}")

    download_pdf(
        url=pdf_url,
        destination=local_pdf_path,
    )

    if not local_pdf_path.exists():
        raise FileNotFoundError(
            f"The downloaded PDF could not be found: {local_pdf_path}"
        )

    georef_pdf_using_viewport(
        pdf_path=local_pdf_path,
        output_tif=output_tif_path,
        page_number=0,
        zoom=4,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nERROR")
        print("-----")
        print(exc)
        raise