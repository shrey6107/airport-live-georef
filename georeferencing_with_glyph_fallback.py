import json
import os
import re
import sys
import math
from pathlib import Path
import pymupdf
import rasterio
import requests
import xml.etree.ElementTree as ET

from rasterio.control import GroundControlPoint
from rasterio.transform import from_gcps

HTTP_SESSION = requests.Session()

AIRPORT_NAME = "KDEN"

TEMP_PNG_PATH = "temp_airport_diagram.png"

DPI = 220

DTPP_CYCLE = "2607"
PROJECT_ROOT = Path(__file__).resolve().parent
WEB_APP_DIR = PROJECT_ROOT / "web_app"
AIRPORT_STUFF_DIR = WEB_APP_DIR / "airport_stuff"
OLD_AIRPORT_PDFS_DIR = AIRPORT_STUFF_DIR / "pdfs"
OLD_AIRPORT_GEOTIFFS_DIR = AIRPORT_STUFF_DIR / "geotiffs"
DTPP_METAFILE_PATH = PROJECT_ROOT / "d-TPP_Metafile.xml"

MIN_CONTROL_POINTS_PER_AXIS = 2

EXTRACTION_METHOD_TEXT = "text"
EXTRACTION_METHOD_GLYPH = "glyph"
EXTRACTION_METHOD_UNKNOWN = "unknown"


def normalize_airport_code(icao: str) -> str:
    return icao.upper().strip()


def get_airport_storage_paths(icao: str) -> tuple[Path, Path, Path]:
    icao = normalize_airport_code(icao)
    airport_dir = AIRPORT_STUFF_DIR / icao
    pdf_path = airport_dir / f"{icao}_apd.pdf"
    geotiff_path = airport_dir / f"{icao}_apd_affine.tif"
    return airport_dir, pdf_path, geotiff_path


def ensure_airport_stuff_dirs(icao: str | None = None) -> None:
    if icao is None:
        AIRPORT_STUFF_DIR.mkdir(parents=True, exist_ok=True)
        return

    airport_dir, _, _ = get_airport_storage_paths(icao)
    airport_dir.mkdir(parents=True, exist_ok=True)


def get_airport_pdf_path(airport_name: str) -> Path:
    _, pdf_path, _ = get_airport_storage_paths(airport_name)
    return pdf_path


def get_airport_geotiff_path(airport_name: str) -> Path:
    _, _, geotiff_path = get_airport_storage_paths(airport_name)
    return geotiff_path


def migrate_old_airport_file(old_path: Path, new_path: Path) -> None:
    if new_path.exists() or not old_path.exists():
        return

    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.replace(new_path)
    print(f"Migrated airport file: {old_path} -> {new_path}")


def migrate_old_airport_storage(icao: str, pdf_path: Path, geotiff_path: Path) -> None:
    icao = normalize_airport_code(icao)
    migrate_old_airport_file(OLD_AIRPORT_PDFS_DIR / f"{icao}.pdf", pdf_path)
    migrate_old_airport_file(
        OLD_AIRPORT_GEOTIFFS_DIR / f"{icao}_affine.tif",
        geotiff_path,
    )

    old_metadata_path = (
        OLD_AIRPORT_GEOTIFFS_DIR / f"{icao}_affine.tif.metadata.json"
    )
    new_metadata_path = Path(get_extraction_metadata_path(str(geotiff_path)))
    migrate_old_airport_file(old_metadata_path, new_metadata_path)


class GeoreferencingError(RuntimeError):
    """Base error for airport diagram georeferencing failures."""


class AirportDiagramDownloadError(GeoreferencingError):
    """Raised when the FAA airport diagram cannot be downloaded."""


class ControlPointError(GeoreferencingError):
    """Raised when coordinate labels or grid control points are missing."""


class InsufficientControlPointsError(GeoreferencingError):
    """Raised when there are not enough points to build a transform."""


class AffineTransformError(GeoreferencingError):
    """Raised when rasterio cannot produce a usable affine transform."""


class GeoTiffValidationError(GeoreferencingError):
    """Raised when a GeoTIFF is missing georeferencing metadata."""


def normalize_extraction_method(method: str | None) -> str:
    if method in ("words", EXTRACTION_METHOD_TEXT):
        return EXTRACTION_METHOD_TEXT

    if method in ("glyphs", EXTRACTION_METHOD_GLYPH):
        return EXTRACTION_METHOD_GLYPH

    return EXTRACTION_METHOD_UNKNOWN


def get_extraction_metadata_path(tif_path: str) -> str:
    return f"{tif_path}.metadata.json"


def read_extraction_method(tif_path: str) -> str:
    try:
        with open(get_extraction_metadata_path(tif_path), encoding="utf-8") as file:
            metadata = json.load(file)

    except (OSError, ValueError):
        return EXTRACTION_METHOD_UNKNOWN

    return normalize_extraction_method(metadata.get("extraction_method"))


def write_extraction_method(tif_path: str, method: str) -> None:
    metadata = {
        "extraction_method": normalize_extraction_method(method),
    }

    try:
        with open(
            get_extraction_metadata_path(tif_path),
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(metadata, file, indent=2)

    except OSError:
        # Debug metadata should never break diagram generation.
        return


def is_identity_transform(transform) -> bool:
    if transform is None:
        return True

    identity_check = getattr(transform, "is_identity", False)

    if callable(identity_check):
        return bool(identity_check())

    if identity_check:
        return True

    transform_values = tuple(transform)

    return transform_values[:6] == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def validate_geotiff(tif_path: str) -> None:

    try:
        with rasterio.open(tif_path) as src:

            if src.crs is None:
                raise GeoTiffValidationError(
                    f"GeoTIFF has no CRS: {tif_path}"
                )

            # Identity transforms mean raster pixels were not georeferenced.
            if is_identity_transform(src.transform):
                raise GeoTiffValidationError(
                    f"GeoTIFF has an identity transform: {tif_path}"
                )

    except GeoTiffValidationError:
        raise

    except Exception as exc:
        raise GeoTiffValidationError(
            f"Could not validate GeoTIFF {tif_path}: {exc}"
        ) from exc


def glyph_distance(a, b) -> float:
    if len(a) != len(b):
        return float("inf")

    total = 0

    for (x1, y1), (x2, y2) in zip(a, b):
        total += math.hypot(x1 - x2, y1 - y2)

    return total / len(a)


def rotate_minus_90(points):
    return [(round(1 - y, 3), round(x, 3)) for x, y in points]


def fuzzy_match_glyph(value, glyph_to_path, threshold=0.02):
    best_key = None
    best_dist = float("inf")

    for key, path in glyph_to_path.items():
        dist = glyph_distance(value, path)

        if dist < best_dist:
            best_dist = dist
            best_key = key

    if best_dist <= threshold:
        return best_key, best_dist

    return None, best_dist


GLYPH_TO_PATH = {
    "3": [(0.477, 0.526), (0.551, 0.528), (0.618, 0.534), (0.674, 0.548), (0.723, 0.569), (0.765, 0.596),
          (0.796, 0.629), (0.814, 0.666), (0.821, 0.709), (0.814, 0.746), (0.796, 0.781), (0.768, 0.814),
          (0.733, 0.841), (0.688, 0.864), (0.635, 0.88), (0.575, 0.891), (0.512, 0.895), (0.453, 0.893),
          (0.4, 0.885), (0.347, 0.872), (0.302, 0.856), (0.263, 0.833), (0.232, 0.808), (0.211, 0.777),
          (0.196, 0.744), (0.0, 0.744), (0.014, 0.8), (0.049, 0.852), (0.095, 0.895), (0.158, 0.932),
          (0.232, 0.961), (0.312, 0.984), (0.404, 0.996), (0.495, 1.0), (0.6, 0.996), (0.698, 0.981),
          (0.782, 0.957), (0.856, 0.924), (0.916, 0.882), (0.961, 0.835), (0.989, 0.777), (1.0, 0.715),
          (0.996, 0.678), (0.986, 0.641), (0.972, 0.608), (0.947, 0.575), (0.919, 0.546), (0.881, 0.52),
          (0.835, 0.497), (0.782, 0.476), (0.863, 0.435), (0.923, 0.384), (0.944, 0.357), (0.958, 0.326),
          (0.968, 0.293), (0.968, 0.258), (0.961, 0.202), (0.933, 0.151), (0.895, 0.107), (0.839, 0.07),
          (0.772, 0.039), (0.695, 0.019), (0.611, 0.004), (0.516, 0.0), (0.421, 0.004), (0.333, 0.016),
          (0.256, 0.035), (0.193, 0.062), (0.137, 0.097), (0.095, 0.138), (0.06, 0.186), (0.039, 0.241),
          (0.239, 0.241), (0.253, 0.212), (0.27, 0.186), (0.295, 0.163), (0.326, 0.142), (0.365, 0.128),
          (0.407, 0.115), (0.456, 0.107), (0.509, 0.105), (0.568, 0.109), (0.621, 0.118), (0.67, 0.132),
          (0.709, 0.151), (0.744, 0.175), (0.768, 0.202), (0.786, 0.233), (0.789, 0.268), (0.782, 0.307),
          (0.765, 0.34), (0.737, 0.367), (0.695, 0.388), (0.649, 0.404), (0.596, 0.414), (0.537, 0.419),
          (0.474, 0.421)],
    "9": [(0.319, 1.0), (0.846, 0.561), (0.903, 0.503), (0.953, 0.441), (0.973, 0.408), (0.987, 0.375),
          (0.997, 0.342), (1.0, 0.305), (0.99, 0.243), (0.96, 0.188), (0.913, 0.134), (0.849, 0.089),
          (0.775, 0.052), (0.691, 0.025), (0.597, 0.006), (0.5, 0.0), (0.396, 0.006), (0.302, 0.025),
          (0.218, 0.054), (0.144, 0.091), (0.084, 0.138), (0.04, 0.192), (0.01, 0.252), (0.0, 0.315), (0.01, 0.371),
          (0.034, 0.427), (0.07, 0.476), (0.124, 0.522), (0.185, 0.561), (0.262, 0.59), (0.346, 0.608),
          (0.436, 0.614), (0.513, 0.61), (0.587, 0.596), (0.591, 0.598), (0.178, 0.934), (0.5, 0.509),
          (0.44, 0.505), (0.383, 0.495), (0.326, 0.476), (0.275, 0.452), (0.235, 0.423), (0.201, 0.388),
          (0.181, 0.348), (0.171, 0.307), (0.178, 0.268), (0.198, 0.229), (0.232, 0.196), (0.272, 0.165),
          (0.326, 0.14), (0.383, 0.122), (0.446, 0.109), (0.513, 0.105), (0.574, 0.109), (0.634, 0.122),
          (0.688, 0.142), (0.735, 0.169), (0.775, 0.198), (0.802, 0.233), (0.822, 0.27), (0.829, 0.307),
          (0.822, 0.346), (0.805, 0.384), (0.779, 0.416), (0.738, 0.447), (0.691, 0.472), (0.634, 0.493),
          (0.57, 0.505)],
    "°": [(0.498, 0.0), (0.295, 0.038), (0.14, 0.144), (0.034, 0.303), (0.0, 0.5), (0.034, 0.702), (0.14, 0.861),
          (0.295, 0.962), (0.498, 1.0), (0.7, 0.962), (0.86, 0.861), (0.966, 0.702), (1.0, 0.5), (0.966, 0.303),
          (0.86, 0.144), (0.7, 0.038), (0.498, 0.769), (0.396, 0.745), (0.309, 0.692), (0.251, 0.606), (0.232, 0.5),
          (0.251, 0.399), (0.309, 0.313), (0.396, 0.255), (0.498, 0.231), (0.604, 0.255), (0.691, 0.313),
          (0.749, 0.399), (0.773, 0.5), (0.749, 0.606), (0.691, 0.692), (0.604, 0.745)],
    "1": [(0.611, 0.111), (0.611, 1.0), (1.0, 1.0), (1.0, 0.0), (0.244, 0.0), (0.0, 0.111)],
    "4": [(0.84, 0.754), (0.84, 0.0), (0.0, 0.859), (0.687, 0.859), (0.687, 1.0), (0.84, 1.0), (0.84, 0.859),
          (1.0, 0.859), (1.0, 0.754), (0.687, 0.754), (0.292, 0.754), (0.684, 0.358), (0.687, 0.358)],
    ".": [(0.5, 0.0), (0.294, 0.044), (0.147, 0.147), (0.029, 0.309), (0.0, 0.5), (0.029, 0.706), (0.147, 0.853),
          (0.294, 0.971), (0.5, 1.0), (0.691, 0.971), (0.853, 0.853), (0.956, 0.706), (1.0, 0.5), (0.956, 0.309),
          (0.853, 0.147), (0.691, 0.044)],
    "0": [(0.498, 1.0), (0.565, 0.998), (0.629, 0.988), (0.686, 0.971), (0.737, 0.951), (0.781, 0.924),
          (0.822, 0.895), (0.857, 0.862), (0.889, 0.825), (0.94, 0.746), (0.975, 0.664), (0.984, 0.621),
          (0.994, 0.581), (0.997, 0.54), (1.0, 0.503), (0.997, 0.464), (0.994, 0.425), (0.984, 0.384),
          (0.975, 0.342), (0.94, 0.258), (0.892, 0.177), (0.86, 0.14), (0.822, 0.107), (0.781, 0.076),
          (0.737, 0.052), (0.686, 0.029), (0.629, 0.014), (0.568, 0.004), (0.498, 0.0), (0.432, 0.004),
          (0.368, 0.014), (0.311, 0.029), (0.26, 0.052), (0.216, 0.076), (0.175, 0.107), (0.14, 0.14),
          (0.108, 0.177), (0.057, 0.258), (0.025, 0.342), (0.013, 0.384), (0.006, 0.425), (0.0, 0.464),
          (0.0, 0.503), (0.0, 0.54), (0.006, 0.581), (0.013, 0.621), (0.025, 0.664), (0.057, 0.746), (0.108, 0.825),
          (0.14, 0.862), (0.175, 0.895), (0.216, 0.924), (0.263, 0.951), (0.314, 0.971), (0.368, 0.988),
          (0.432, 0.998), (0.498, 0.105), (0.546, 0.109), (0.59, 0.118), (0.629, 0.13), (0.663, 0.148),
          (0.721, 0.196), (0.768, 0.254), (0.8, 0.318), (0.822, 0.384), (0.832, 0.447), (0.838, 0.501),
          (0.832, 0.557), (0.822, 0.619), (0.8, 0.685), (0.768, 0.748), (0.721, 0.806), (0.663, 0.852),
          (0.629, 0.87), (0.59, 0.885), (0.546, 0.893), (0.498, 0.895), (0.451, 0.893), (0.41, 0.885),
          (0.368, 0.87), (0.337, 0.852), (0.276, 0.806), (0.232, 0.748), (0.2, 0.685), (0.178, 0.619),
          (0.165, 0.557), (0.162, 0.501), (0.165, 0.447), (0.178, 0.384), (0.2, 0.318), (0.232, 0.254),
          (0.276, 0.196), (0.337, 0.148), (0.368, 0.13), (0.41, 0.118), (0.451, 0.109)],
    "'": [(0.58, 0.0), (0.0, 0.922), (0.294, 1.0), (1.0, 0.106)],
    "N": [(0.0, 0.962), (0.15, 0.962), (0.15, 0.269), (1.0, 1.0), (1.0, 0.044), (0.853, 0.044), (0.853, 0.715),
          (0.0, 0.0)],
    "2": [(0.382, 0.895), (0.894, 0.495), (0.939, 0.451), (0.973, 0.4), (0.993, 0.347), (1.0, 0.291),
          (0.993, 0.229), (0.962, 0.173), (0.918, 0.124), (0.86, 0.082), (0.788, 0.046), (0.706, 0.021),
          (0.618, 0.006), (0.519, 0.0), (0.416, 0.006), (0.324, 0.021), (0.242, 0.044), (0.174, 0.078),
          (0.116, 0.12), (0.072, 0.168), (0.038, 0.225), (0.02, 0.288), (0.215, 0.288), (0.225, 0.253),
          (0.246, 0.219), (0.273, 0.187), (0.311, 0.16), (0.355, 0.139), (0.406, 0.122), (0.461, 0.112),
          (0.519, 0.107), (0.58, 0.112), (0.635, 0.122), (0.689, 0.139), (0.734, 0.162), (0.771, 0.192),
          (0.802, 0.223), (0.823, 0.259), (0.829, 0.297), (0.816, 0.345), (0.782, 0.392), (0.737, 0.434),
          (0.689, 0.474), (0.0, 1.0), (1.0, 1.0), (1.0, 0.895)],
    "6": [(0.684, 0.0), (0.158, 0.44), (0.098, 0.498), (0.047, 0.56), (0.027, 0.591), (0.013, 0.624),
          (0.003, 0.659), (0.0, 0.694), (0.01, 0.756), (0.04, 0.814), (0.091, 0.866), (0.152, 0.911),
          (0.226, 0.948), (0.313, 0.975), (0.404, 0.994), (0.502, 1.0), (0.606, 0.994), (0.7, 0.975),
          (0.785, 0.946), (0.859, 0.909), (0.919, 0.862), (0.963, 0.808), (0.99, 0.75), (1.0, 0.686),
          (0.993, 0.628), (0.97, 0.572), (0.933, 0.523), (0.879, 0.477), (0.815, 0.44), (0.741, 0.411),
          (0.66, 0.393), (0.566, 0.386), (0.488, 0.393), (0.414, 0.409), (0.411, 0.405), (0.825, 0.052),
          (0.502, 0.895), (0.438, 0.89), (0.374, 0.878), (0.32, 0.859), (0.269, 0.835), (0.229, 0.804),
          (0.199, 0.771), (0.178, 0.731), (0.172, 0.692), (0.178, 0.653), (0.199, 0.616), (0.232, 0.583),
          (0.273, 0.552), (0.323, 0.527), (0.377, 0.508), (0.438, 0.496), (0.502, 0.492), (0.562, 0.496),
          (0.623, 0.506), (0.677, 0.525), (0.727, 0.55), (0.768, 0.581), (0.801, 0.616), (0.822, 0.655),
          (0.828, 0.698), (0.822, 0.738), (0.801, 0.775), (0.771, 0.808), (0.731, 0.837), (0.68, 0.862),
          (0.626, 0.878), (0.566, 0.89)],
    "5": [(0.98, 0.109), (0.98, 0.0), (0.391, 0.0), (0.16, 0.505), (0.195, 0.505), (0.254, 0.469), (0.322, 0.441),
          (0.397, 0.424), (0.479, 0.418), (0.547, 0.424), (0.616, 0.437), (0.674, 0.458), (0.73, 0.486),
          (0.772, 0.522), (0.808, 0.561), (0.827, 0.604), (0.837, 0.651), (0.827, 0.698), (0.808, 0.743),
          (0.775, 0.784), (0.73, 0.82), (0.678, 0.85), (0.616, 0.872), (0.547, 0.887), (0.472, 0.891),
          (0.423, 0.889), (0.371, 0.88), (0.322, 0.867), (0.274, 0.85), (0.231, 0.829), (0.192, 0.803),
          (0.163, 0.775), (0.14, 0.745), (0.0, 0.831), (0.039, 0.869), (0.085, 0.902), (0.137, 0.931),
          (0.195, 0.955), (0.257, 0.974), (0.322, 0.987), (0.391, 0.996), (0.459, 1.0), (0.518, 0.998),
          (0.57, 0.994), (0.625, 0.985), (0.674, 0.974), (0.765, 0.944), (0.847, 0.904), (0.912, 0.854),
          (0.961, 0.794), (0.977, 0.762), (0.99, 0.728), (1.0, 0.692), (1.0, 0.655), (0.993, 0.589), (0.971, 0.525),
          (0.928, 0.467), (0.876, 0.413), (0.808, 0.37), (0.73, 0.338), (0.684, 0.325), (0.635, 0.317),
          (0.586, 0.31), (0.534, 0.308), (0.427, 0.313), (0.521, 0.109)],
    "W": [(0.098, 0.035), (0.0, 0.035), (0.298, 1.0), (0.501, 0.313), (0.702, 1.0), (1.0, 0.035), (0.904, 0.035),
          (0.702, 0.697), (0.501, 0.0), (0.299, 0.683)],
    "7": [(0.719, 0.109), (0.0, 0.931), (0.136, 1.0), (1.0, 0.0), (0.0, 0.0), (0.0, 0.109)],
    "8": [(0.0, 0.719), (0.007, 0.781), (0.039, 0.837), (0.082, 0.884), (0.143, 0.924), (0.218, 0.957),
          (0.304, 0.979), (0.396, 0.994), (0.5, 1.0), (0.6, 0.994), (0.696, 0.979), (0.779, 0.957), (0.854, 0.924),
          (0.914, 0.884), (0.961, 0.837), (0.989, 0.781), (1.0, 0.719), (0.996, 0.682), (0.982, 0.647),
          (0.964, 0.614), (0.936, 0.581), (0.904, 0.554), (0.861, 0.529), (0.811, 0.508), (0.754, 0.492),
          (0.846, 0.452), (0.914, 0.401), (0.936, 0.372), (0.954, 0.341), (0.964, 0.308), (0.968, 0.273),
          (0.961, 0.215), (0.932, 0.163), (0.893, 0.118), (0.836, 0.076), (0.768, 0.045), (0.686, 0.021),
          (0.596, 0.006), (0.5, 0.0), (0.4, 0.006), (0.311, 0.021), (0.232, 0.045), (0.164, 0.076), (0.107, 0.118),
          (0.064, 0.163), (0.039, 0.215), (0.029, 0.273), (0.032, 0.308), (0.043, 0.341), (0.061, 0.372),
          (0.082, 0.401), (0.15, 0.452), (0.236, 0.492), (0.182, 0.508), (0.132, 0.529), (0.093, 0.554),
          (0.061, 0.581), (0.032, 0.614), (0.014, 0.647), (0.004, 0.682), (0.5, 0.105), (0.554, 0.11),
          (0.607, 0.118), (0.657, 0.134), (0.7, 0.155), (0.736, 0.18), (0.764, 0.209), (0.782, 0.24),
          (0.786, 0.273), (0.782, 0.302), (0.761, 0.333), (0.732, 0.36), (0.696, 0.384), (0.654, 0.407),
          (0.604, 0.424), (0.554, 0.434), (0.5, 0.438), (0.446, 0.434), (0.393, 0.424), (0.346, 0.407),
          (0.3, 0.384), (0.264, 0.36), (0.236, 0.333), (0.218, 0.302), (0.211, 0.273), (0.218, 0.24),
          (0.236, 0.209), (0.261, 0.18), (0.296, 0.155), (0.339, 0.134), (0.389, 0.118), (0.443, 0.11),
          (0.5, 0.543), (0.561, 0.548), (0.618, 0.558), (0.671, 0.572), (0.721, 0.595), (0.761, 0.62),
          (0.789, 0.651), (0.811, 0.684), (0.818, 0.719), (0.811, 0.754), (0.789, 0.785), (0.761, 0.816),
          (0.718, 0.843), (0.671, 0.864), (0.618, 0.88), (0.557, 0.89), (0.5, 0.895), (0.439, 0.89), (0.382, 0.88),
          (0.329, 0.864), (0.279, 0.843), (0.239, 0.816), (0.207, 0.785), (0.189, 0.754), (0.182, 0.719),
          (0.189, 0.684), (0.207, 0.651), (0.239, 0.62), (0.279, 0.595), (0.325, 0.572), (0.379, 0.558),
          (0.439, 0.548)],
}

# -----------------------------------------------------------------------------
# FAA airport diagram download
# -----------------------------------------------------------------------------
def build_icao_lookup(xml_file: str | Path) -> dict[str, str]:
    root = ET.parse(xml_file).getroot()

    lookup = {}

    for airport in root.iter("airport_name"):
        icao = airport.attrib.get("icao_ident")
        alnum = airport.attrib.get("alnum")

        if icao and alnum:
            lookup[icao.upper()] = alnum.zfill(5)

    return lookup


def get_dtpp_metafile_path() -> Path:
    if DTPP_METAFILE_PATH.exists():
        return DTPP_METAFILE_PATH

    raise AirportDiagramDownloadError("d-TPP_Metafile.xml not found.")


def download_airport_diagram(airport_name: str, pdf_path: str | Path) -> None:
    airport_name = normalize_airport_code(airport_name)
    pdf_path = Path(pdf_path)

    if pdf_path.exists():
        print(f"{pdf_path} already exists")
        return

    try:
        lookup = build_icao_lookup(get_dtpp_metafile_path())

    except ET.ParseError as exc:
        raise AirportDiagramDownloadError(
            "d-TPP_Metafile.xml could not be parsed."
        ) from exc

    except OSError as exc:
        raise AirportDiagramDownloadError(
            "d-TPP_Metafile.xml not found."
        ) from exc

    if airport_name not in lookup:
        raise AirportDiagramDownloadError(
            f"No FAA airport diagram found for {airport_name}."
        )

    url = f"https://aeronav.faa.gov/d-tpp/{DTPP_CYCLE}/{lookup[airport_name]}AD.PDF"

    try:
        response = HTTP_SESSION.get(url, timeout=30)
        response.raise_for_status()

    except requests.RequestException as exc:
        raise AirportDiagramDownloadError(
            f"Failed to download FAA airport diagram for {airport_name}."
        ) from exc

    # Save only responses that look like actual PDFs.
    if not response.content or not response.content.lstrip().startswith(b"%PDF"):
        raise AirportDiagramDownloadError(
            f"Failed to download FAA airport diagram for {airport_name}."
        )

    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)

        with pdf_path.open("wb") as file:
            file.write(response.content)

    except OSError as exc:
        raise AirportDiagramDownloadError(
            f"Failed to download FAA airport diagram for {airport_name}."
        ) from exc

    print(f"Downloaded airport diagram: {pdf_path}")


# -----------------------------------------------------------------------------
# PDF coordinate extraction
# -----------------------------------------------------------------------------
def extract_word_coordinate_labels(page) -> dict[str, tuple[float, float, float, float]]:

    coords_dict = {}

    for word_info in page.get_text("words"):

        x0, y0, x1, y1, word, *_ = word_info

        if "°" in word and "'" in word:
            coords_dict[word] = (x0, y0, x1, y1)

    return coords_dict


def extract_glyph_coordinate_labels(
    page,
    north_up_flag: bool,
) -> dict[str, tuple[float, float, float, float]]:

    glyphs = []
    text = ""

    for path in page.get_drawings():

        x0, y0, x1, y1 = path["rect"]
        height = abs(y1 - y0)
        width = abs(x1 - x0)

        if width == 0 or height == 0:
            continue

        value = []

        for cmd, *pts in path["items"]:

            if cmd != "l":
                continue

            pt1, _ = pts
            x, y = pt1

            value.append(
                (
                    round((x - x0) / width, 3),
                    round((y - y0) / height, 3),
                )
            )

        if not north_up_flag:
            value = rotate_minus_90(value)

        key, _ = fuzzy_match_glyph(value, GLYPH_TO_PATH)

        if key is None:
            key = "/"

        key = str(key)

        glyphs.append(
            {
                "char": key,
                "rect": path["rect"],
            }
        )

        text += key

    pattern = re.compile(
        r"""
        (?:
            \d{2}°\d{1,2}(?:\.\d+)?'N   # N: exactly 2 digits before °
          |
            \d{2,3}°\d{1,2}(?:\.\d+)?'W # W: exactly 2 or 3 digits before °
        )
        """,
        re.VERBOSE,
    )

    coords_dict = {}

    for match in re.finditer(pattern, text):

        coord = match.group()

        start_idx = match.start()
        end_idx = match.end() - 1

        first_rect = glyphs[start_idx]["rect"]
        last_rect = glyphs[end_idx]["rect"]

        x0 = first_rect[0]
        y0 = first_rect[1]
        x1 = last_rect[2]
        y1 = last_rect[3]

        coords_dict[coord] = (x0, y0, x1, y1)

        print()
        print(f"Coords: {coords_dict}")
        print()

    return coords_dict


def is_coordinate_label_set_valid(labels: dict[str, tuple[float, float, float, float]]) -> bool:

    if not labels:
        return False

    lat_count = 0
    lon_count = 0

    for label in labels:

        if not label or "°" not in label or "'" not in label:
            return False

        try:
            parse_coord(label)

        except ValueError:
            return False

        hemisphere = label.strip()[-1].upper()

        if hemisphere in ("N", "S"):
            lat_count += 1

        elif hemisphere in ("E", "W"):
            lon_count += 1

        else:
            return False

    return (
        lat_count >= MIN_CONTROL_POINTS_PER_AXIS
        and lon_count >= MIN_CONTROL_POINTS_PER_AXIS
    )


def get_north_up_orientation(page) -> bool:

    for drawing in page.get_drawings():

        x0, y0, x1, y1 = drawing["rect"]

        height = y1 - y0
        width = x1 - x0

        if (10.65 <= height <= 10.68) and (4.88 <= width <= 4.91):
            return True

    return False


def extract_coordinate_labels_with_fallback(
    page,
    north_up_flag: bool,
) -> tuple[dict[str, tuple[float, float, float, float]], str]:

    word_error = None

    try:
        coords_dict = extract_word_coordinate_labels(page)

        if is_coordinate_label_set_valid(coords_dict):
            print("Coordinate extraction method: words")
            return coords_dict, "words"

        word_error = ControlPointError(
            "Word extraction returned incomplete coordinate labels"
        )

    except GeoreferencingError as exc:
        word_error = exc

    except Exception as exc:
        word_error = ControlPointError(
            f"Word coordinate extraction failed: {exc}"
        )

    print("Word extraction failed or incomplete; trying glyph extraction...")

    try:
        coords_dict = extract_glyph_coordinate_labels(page, north_up_flag)

    except GeoreferencingError:
        raise

    except Exception as exc:
        raise ControlPointError(
            f"Glyph coordinate extraction failed: {exc}"
        ) from exc

    if is_coordinate_label_set_valid(coords_dict):
        print("Coordinate extraction method: glyphs")
        return coords_dict, "glyphs"

    raise ControlPointError(
        "Could not extract usable coordinate labels with words or glyphs: "
        f"{word_error}"
    )


def get_coordinate_labels_and_orientation(
    pdf_path: str,
) -> tuple[dict[str, tuple[float, float, float, float]], bool]:

    coords_dict, north_up_flag, _ = get_coordinate_labels_orientation_and_method(
        pdf_path
    )

    return coords_dict, north_up_flag


def get_coordinate_labels_orientation_and_method(
    pdf_path: str,
) -> tuple[dict[str, tuple[float, float, float, float]], bool, str]:

    with pymupdf.open(pdf_path) as doc:

        page = doc[0]
        north_up_flag = get_north_up_orientation(page)
        coords_dict, extraction_method = extract_coordinate_labels_with_fallback(
            page,
            north_up_flag,
        )

    return (
        coords_dict,
        north_up_flag,
        normalize_extraction_method(extraction_method),
    )


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
        raise ControlPointError("Could not find airport diagram frame")

    X0, Y0, X1, Y1 = grid_frame

    vertical_pts = []
    horizontal_pts = []

    edge_tolerance = 3

    for path in paths:

        items = path.get("items", [])

        if not items or items[0][0] != "l":
            continue

        x0, y0, x1, y1 = path["rect"]

        if min(Y0 - edge_tolerance, Y0 + edge_tolerance) <= y0 <= max(Y0 - edge_tolerance, Y0 + edge_tolerance) and X0 < x0 < X1:
            vertical_pts.append(x0)

        elif min(Y1 - edge_tolerance, Y1 + edge_tolerance) <= y1 <= max(Y1 - edge_tolerance, Y1 + edge_tolerance) and X0 < x1 < X1:
            vertical_pts.append(x1)

        if min(X0 - edge_tolerance, X0 + edge_tolerance) <= x0 <= max(X0 - edge_tolerance, X0 + edge_tolerance) and Y0 < y0 < Y1:
            horizontal_pts.append(y0)

        elif min(X1 - edge_tolerance, X1 + edge_tolerance) <= x1 <= max(X1 - edge_tolerance, X1 + edge_tolerance) and Y0 < y1 < Y1:
            horizontal_pts.append(y1)

    # Grid edge ticks are needed to place label-derived control points.
    if not vertical_pts or not horizontal_pts:
        raise ControlPointError(
            "Could not find vertical and horizontal grid control points"
        )

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

                if min(x0, x1) < x < max(x0, x1):
                    grid_x_dict[label] = x

        if y_axis_label in label:

            for y in horizontal_pts:

                if min(y0, y1) < y < max(y0, y1):
                    grid_y_dict[label] = y

    if not north_up_flag:
        grid_x_dict, grid_y_dict = grid_y_dict, grid_x_dict

    # Both axes must have matched labels before GCP generation.
    if not grid_x_dict or not grid_y_dict:
        raise ControlPointError(
            "Could not match coordinate labels to diagram grid control points"
        )

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
    pdf_path: str | Path,
    output_tif_path: str | Path,
    x_dict: dict[str, float],
    y_dict: dict[str, float],
    north_up_flag: bool,
    dpi: int = DPI,
) -> None:
    pdf_path = Path(pdf_path)
    output_tif_path = Path(output_tif_path)

    if (
        len(x_dict) < MIN_CONTROL_POINTS_PER_AXIS
        or len(y_dict) < MIN_CONTROL_POINTS_PER_AXIS
    ):
        raise InsufficientControlPointsError(
            "Need at least two grid control points on each axis; "
            f"found {len(x_dict)} x-axis and {len(y_dict)} y-axis points"
        )

    try:
        lon_to_pdf_x = {parse_coord(label): x for label, x in x_dict.items()}
        lat_to_pdf_y = {parse_coord(label): y for label, y in y_dict.items()}

    except ValueError as exc:
        raise ControlPointError(f"Invalid coordinate label: {exc}") from exc

    scale = dpi / 72.0

    temp_output_tif_path = Path(f"{output_tif_path}.tmp")

    try:

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

        if (
            len(gcps)
            < MIN_CONTROL_POINTS_PER_AXIS * MIN_CONTROL_POINTS_PER_AXIS
        ):
            raise InsufficientControlPointsError(
                f"Need at least 4 GCPs; found {len(gcps)}"
            )

        try:
            transform = from_gcps(gcps)

        except Exception as exc:
            raise AffineTransformError(
                f"Could not generate affine transform: {exc}"
            ) from exc

        if is_identity_transform(transform):
            raise AffineTransformError(
                "Generated affine transform is identity"
            )

        profile.update(
            driver="GTiff",
            crs="EPSG:4326",
            transform=transform,
        )

        output_tif_path.parent.mkdir(parents=True, exist_ok=True)

        if temp_output_tif_path.exists():
            temp_output_tif_path.unlink()

        with rasterio.open(temp_output_tif_path, "w", **profile) as dst:
            dst.write(data)

        # The final filename is used only after georeferencing validates.
        validate_geotiff(temp_output_tif_path)
        os.replace(temp_output_tif_path, output_tif_path)

    except Exception:

        if temp_output_tif_path.exists():
            temp_output_tif_path.unlink()

        raise

    finally:

        if os.path.exists(TEMP_PNG_PATH):
            os.remove(TEMP_PNG_PATH)

    print(f"Saved GeoTIFF: {output_tif_path}")


def get_geotiff_center_lon_lat(tif_path: str) -> tuple[float, float]:

    validate_geotiff(tif_path)

    with rasterio.open(tif_path) as src:

        center_row = src.height // 2
        center_col = src.width // 2

        lon, lat = src.xy(center_row, center_col)

        return float(lon), float(lat)

def ensure_geotiff_exists(airport_name: str) -> str:
    airport_name = normalize_airport_code(airport_name)
    airport_dir, pdf_path, geotiff_path = get_airport_storage_paths(airport_name)
    airport_dir.mkdir(parents=True, exist_ok=True)
    migrate_old_airport_storage(airport_name, pdf_path, geotiff_path)

    if geotiff_path.exists():

        print(f"{geotiff_path} already exists")

        try:
            validate_geotiff(geotiff_path)

        except GeoTiffValidationError as exc:
            print(f"{geotiff_path} is invalid and will be regenerated: {exc}")

            try:
                geotiff_path.unlink()

            except OSError as remove_exc:
                raise GeoTiffValidationError(
                    f"Could not remove invalid GeoTIFF {geotiff_path}: "
                    f"{remove_exc}"
                ) from remove_exc

        else:
            return str(geotiff_path)

    download_airport_diagram(
        airport_name,
        pdf_path,
    )

    coords_dict, north_up_flag, extraction_method = (
        get_coordinate_labels_orientation_and_method(
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

    write_extraction_method(geotiff_path, extraction_method)

    return str(geotiff_path)

def main() -> None:
    airport_name = AIRPORT_NAME

    if len(sys.argv) > 1:
        airport_name = sys.argv[1].upper()

    geotiff_path = ensure_geotiff_exists(
        airport_name
    )

    center_lon, center_lat = (
        get_geotiff_center_lon_lat(
            geotiff_path
        )
    )

    print(center_lon, center_lat)

if __name__ == "__main__":
    main()
