import sys
import threading

import numpy as np
import rasterio
import requests

from pyproj import Transformer
from rasterio.plot import reshape_as_image

from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from georeferencing import ensure_geotiff_exists, get_geotiff_center_lon_lat

# -----------------------------------------------------------------------------
# Performance config
# -----------------------------------------------------------------------------
pg.setConfigOptions(useOpenGL=True)

HTTP_SESSION = requests.Session()


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
AIRPORT_NAME = "KCID"

DPI = 220
ADS_B_RADIUS_MI = 2
UPDATE_MS = 1200
MAX_LABELS = 110

DISPLAY_FIELDS = [
    ("ALT", "alt_baro"),
    ("TRK", "track"),
    ("V/S", "baro_rate"),
]

# -----------------------------------------------------------------------------
# ADS-B helpers
# -----------------------------------------------------------------------------
def build_adsb_url(center_lat: float, center_lon: float, radius_mi: int) -> str:

    return (
        f"https://api.adsb.lol/v2/"
        f"lat/{center_lat}/"
        f"lon/{center_lon}/"
        f"dist/{radius_mi}"
    )


def fetch_adsb(api_url: str) -> dict:

    response = HTTP_SESSION.get(
        api_url,
        headers={"accept": "application/json"},
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def extract_aircraft_points(
    data: dict,
) -> tuple[np.ndarray, list[list[str]], list]:

    points = []
    labels = []
    vertical_rates = []

    for aircraft in data.get("ac", []):

        lat = aircraft.get("lat")
        lon = aircraft.get("lon")

        if lat is None or lon is None:
            continue

        flight = (aircraft.get("flight") or "").strip()
        registration = (aircraft.get("r") or "").strip()
        hexid = (aircraft.get("hex") or "").strip()

        identifier = flight or registration or hexid or "UNK"

        lines = [identifier]

        for display_name, aircraft_key in DISPLAY_FIELDS:

            value = aircraft.get(aircraft_key)

            if value is None:
                continue

            if isinstance(value, str) and not value.strip():
                continue

            lines.append(f"{display_name}: {value}")

        points.append((lon, lat))
        labels.append(lines)
        vertical_rates.append(aircraft.get("baro_rate"))

    if not points:

        return (
            np.empty((0, 2), dtype=float),
            [],
            [],
        )

    return (
        np.array(points, dtype=float),
        labels,
        vertical_rates,
    )


# -----------------------------------------------------------------------------
# Qt helpers
# -----------------------------------------------------------------------------
def normalize_to_rgb_uint8(img: np.ndarray) -> np.ndarray:

    if img.shape[0] >= 3:

        rgb = reshape_as_image(img[:3])

        return np.clip(rgb, 0, 255).astype(np.uint8)

    band = img[0].astype(np.float32)

    mn = np.nanmin(band)
    mx = np.nanmax(band)

    if mx - mn < 1e-9:
        band8 = np.zeros_like(band, dtype=np.uint8)

    else:
        band8 = (255 * (band - mn) / (mx - mn)).astype(np.uint8)

    return np.dstack([band8, band8, band8])


def numpy_to_qpixmap(arr: np.ndarray) -> QtGui.QPixmap:

    arr = np.ascontiguousarray(arr)

    height, width, _ = arr.shape

    bytes_per_line = 3 * width

    qimg = QtGui.QImage(
        arr.data,
        width,
        height,
        bytes_per_line,
        QtGui.QImage.Format.Format_RGB888,
    ).copy()

    return QtGui.QPixmap.fromImage(qimg)


# -----------------------------------------------------------------------------
# Main window
# -----------------------------------------------------------------------------
class AirportLiveWindow(QtWidgets.QMainWindow):

    def __init__(self, tiff_path: str, adsb_api_url: str):

        super().__init__()

        self.setWindowTitle("Live Airport Surface Traffic")

        self.adsb_api_url = adsb_api_url

        self.ds = rasterio.open(tiff_path)

        image = self.ds.read()

        transform = self.ds.transform
        bounds = self.ds.bounds

        self.to_map = Transformer.from_crs(
            "EPSG:4326",
            self.ds.crs,
            always_xy=True,
        )

        self.bg = normalize_to_rgb_uint8(image)

        central = QtWidgets.QWidget()

        self.setCentralWidget(central)

        layout = QtWidgets.QVBoxLayout(central)

        layout.setContentsMargins(0, 0, 0, 0)

        self.glw = pg.GraphicsLayoutWidget()

        layout.addWidget(self.glw)

        self.plot = self.glw.addPlot()

        self.plot.setAspectLocked(True)

        self.plot.hideButtons()

        self.plot.showAxes(False)

        pixmap = numpy_to_qpixmap(self.bg)

        self.img_item = QtWidgets.QGraphicsPixmapItem(pixmap)

        qt_transform = QtGui.QTransform(
            transform.a,
            transform.d,
            transform.b,
            transform.e,
            transform.c,
            transform.f,
        )

        self.img_item.setTransform(qt_transform)

        self.plot.addItem(self.img_item)

        self.plot.setXRange(bounds.left, bounds.right, padding=0)
        self.plot.setYRange(bounds.bottom, bounds.top, padding=0)

        # ---------------------------------------------------------------------
        # Scatter
        # ---------------------------------------------------------------------
        self.scatter = pg.ScatterPlotItem(
            size=10,
            pen=None,
        )

        self.plot.addItem(self.scatter)

        # ---------------------------------------------------------------------
        # Reusable brushes
        # ---------------------------------------------------------------------
        self.brush_red = pg.mkBrush(255, 0, 0, 200)
        self.brush_blue = pg.mkBrush(0, 120, 255, 220)
        self.brush_green = pg.mkBrush(0, 255, 0, 220)

        # ---------------------------------------------------------------------
        # Prevent overlapping update threads
        # ---------------------------------------------------------------------
        self.update_in_progress = False

        # ---------------------------------------------------------------------
        # Reusable labels
        # ---------------------------------------------------------------------
        self.label_items = []

        for _ in range(MAX_LABELS):

            label = pg.TextItem(
                anchor=(0, 1),
                color=(255, 255, 0),
                fill=pg.mkBrush(0, 0, 0, 100),
                border=pg.mkPen(255, 255, 255, 180),
            )

            label.hide()

            self.plot.addItem(label)

            self.label_items.append(label)

        # ---------------------------------------------------------------------
        # Fullscreen toggle
        # ---------------------------------------------------------------------
        self._fullscreen = False

        self.shortcut = QtGui.QShortcut(
            QtGui.QKeySequence("F"),
            self,
        )

        self.shortcut.activated.connect(self.toggle_fullscreen)

        # ---------------------------------------------------------------------
        # Timer
        # ---------------------------------------------------------------------
        self.timer = QtCore.QTimer(self)

        self.timer.timeout.connect(self.kick_update)

        self.timer.start(UPDATE_MS)

        self.kick_update()

    def toggle_fullscreen(self) -> None:

        self._fullscreen = not self._fullscreen

        if self._fullscreen:
            self.showFullScreen()

        else:
            self.showNormal()

    def kick_update(self) -> None:

        if self.update_in_progress:
            return

        self.update_in_progress = True

        threading.Thread(
            target=self._fetch_and_queue,
            daemon=True,
        ).start()

    def _fetch_and_queue(self) -> None:

        try:

            data = fetch_adsb(self.adsb_api_url)

            lonlat, labels, vertical_rates = extract_aircraft_points(data)

            if lonlat.size:

                xs, ys = self.to_map.transform(
                    lonlat[:, 0],
                    lonlat[:, 1],
                )

                xy = np.column_stack([xs, ys])

            else:
                xy = np.empty((0, 2), dtype=float)

            if len(labels) > MAX_LABELS:

                labels = labels[:MAX_LABELS]
                xy = xy[:MAX_LABELS]
                vertical_rates = vertical_rates[:MAX_LABELS]

            QtCore.QMetaObject.invokeMethod(
                self,
                "_apply_aircraft_update",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(object, xy),
                QtCore.Q_ARG(object, labels),
                QtCore.Q_ARG(object, vertical_rates),
            )

        except Exception as exc:

            print("Update error:", exc)

        finally:

            self.update_in_progress = False

    @QtCore.pyqtSlot(object, object, object)
    def _apply_aircraft_update(
        self,
        xy: np.ndarray,
        labels: list[list[str]],
        vertical_rates: list,
    ) -> None:

        spots = []

        for (x, y), vs in zip(xy, vertical_rates):

            brush = self.brush_red

            if vs is not None and vs < 0:
                brush = self.brush_blue

            elif vs is not None and vs > 0:
                brush = self.brush_green

            spots.append({
                "pos": (float(x), float(y)),
                "brush": brush,
            })

        self.scatter.setData(spots)

        # Reuse label objects
        for i, label_item in enumerate(self.label_items):

            if i >= len(xy):

                label_item.hide()

                continue

            x, y = xy[i]

            text = "\n".join(labels[i])

            label_item.setText(text)

            label_item.setPos(float(x), float(y))

            label_item.show()

def main() -> None:

    geotiff_path = ensure_geotiff_exists(
        AIRPORT_NAME
    )

    center_lon, center_lat = (
        get_geotiff_center_lon_lat(
            geotiff_path
        )
    )

    adsb_api_url = build_adsb_url(
        center_lat=center_lat,
        center_lon=center_lon,
        radius_mi=ADS_B_RADIUS_MI,
    )

    app = QtWidgets.QApplication(sys.argv)

    window = AirportLiveWindow(
        geotiff_path,
        adsb_api_url,
    )

    window.resize(1440, 900)

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()