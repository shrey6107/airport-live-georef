import os
import pymupdf
import re
from rasterio.control import GroundControlPoint
from rasterio.transform import from_gcps
import sys
import threading
import requests
import numpy as np
import rasterio
from rasterio.plot import reshape_as_image
from pyproj import Transformer

from PyQt6 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

airport_name = "KSFO"

def download_apd():
    if os.path.exists(f"{airport_name}.pdf"):
        print(f"{airport_name} already downloaded")
        return
    url = f"https://www.flightaware.com/resources/airport/{airport_name}/APD/AIRPORT+DIAGRAM/pdf"
    response = requests.get(url)

    if response.status_code == 200:
        with open(f"{airport_name}.pdf", "wb") as file:
            file.write(response.content)
    else:
        print("Error downloading pdf")
    return

def get_coords_north_flag():
    doc = pymupdf.open(airport_name + ".PDF")
    page = doc[0]

    coords_dict = {}

    words = page.get_text("words")
    # each item: (x0, y0, x1, y1, "word", block_no, line_no, word_no)

    for w in words:
        x0, y0, x1, y1, word, *_ = w
        if "°" in word and "'" in word:
            coords_dict[word] = (x0, y0, x1, y1)
    print("Coords Dict: ",coords_dict)

    doc = pymupdf.open(airport_name + ".PDF")
    page = doc[0]
    drawings = page.get_drawings()

    north_up_flag = False
    for drawing in drawings:
        x0, y0, x1, y1 = drawing['rect']

        length = y1 - y0
        width = x1 - x0
        if (10.65 <= length <= 10.68) and (4.88 <= width <= 4.91):
            print("IN:", (y1-y0))
            north_up_flag = True
    print("North Flag Up:", north_up_flag)

    return coords_dict, north_up_flag

def get_gridlines(coords_dict, north_up_flag):
    page = pymupdf.open(airport_name + ".PDF")[0]
    paths = page.get_drawings()
    largest_rectangle = 0
    for path in paths:
        drawing_type = path.get('items')[0][0]
        if drawing_type == "qu":

            x0, y0, x1, y1 = path.get('rect')
            rect_area = (x1 - x0) * (y1 - y0)
            if rect_area > largest_rectangle:
                largest_rectangle = rect_area
                X0, Y0, X1, Y1 = x0, y0, x1, y1

    vertical_pts = []
    horizontal_pts = []

    for path in paths:
        if path["items"][0][0] == "l":

            x0, y0, x1, y1 = path.get('rect')

            if Y0 <= y0 <= Y0 + 0.5 and X0 < x0 < X1:  # Top Scan
                # print("V", path['rect'])
                vertical_pts.append(x0)
            elif Y1 - 0.5 <= y1 <= Y1 and X0 < x1 < X1:  # Bottom Scan
                vertical_pts.append(x1)

            if X0 <= x0 <= X0 + 0.5 and Y0 < y0 < Y1:  # Left Scan
                # print("H", path['rect'])
                horizontal_pts.append(y0)
            elif X1 - 0.5 <= x1 <= X1 and Y0 < y1 < Y1:  # Right Scan
                horizontal_pts.append(y1)
    print("Vertical Pts: ", vertical_pts)
    print("Horizontal Pts: ", horizontal_pts)

    grid_x_dict = {}
    grid_y_dict = {}

    for k, v in coords_dict.items():
        west = "W"
        north = "N"

        if not north_up_flag:
            west = "N"
            north = "W"

        if west in k:
            x0, _, x1, _ = v
            for pts in vertical_pts:
                if x0 < pts < x1:
                    grid_x_dict[k] = pts
        if north in k:
            _, y0, _, y1 = v
            for pts in horizontal_pts:
                if y0 < pts < y1:
                    grid_y_dict[k] = pts

    if not north_up_flag:
        grid_x_dict, grid_y_dict = grid_y_dict, grid_x_dict  # Swap

    print("grid_x_dict: ", grid_x_dict)
    print("grid_y_dict: ", grid_y_dict)

    return grid_x_dict, grid_y_dict

def parse_coord(label: str) -> float:

    m = re.match(r"^\s*(\d+(?:\.\d+)?)°\s*(\d+(?:\.\d+)?)'\s*([NSEW])\s*$", label)
    if not m:
        raise ValueError(f"Could not parse coordinate label: {label}")

    deg = float(m.group(1))
    minutes = float(m.group(2))
    hemi = m.group(3).upper()

    value = deg + minutes / 60.0

    if hemi in ("W", "S"):
        value = -value

    return value

def georeferencing(x_dict, y_dict, north_up_flag):
    final_x_dict = {}
    final_y_dict = {}

    for k, v in x_dict.items():
        converted_val = parse_coord(k)
        final_x_dict[converted_val] = v

    for k, v in y_dict.items():
        converted_val = parse_coord(k)
        final_y_dict[converted_val] = v

    pdf = airport_name + ".PDF"
    temp_png = "temp.png"
    output_tif = airport_name + "_affine.tif"
    dpi = 300
    scale = dpi / 72.0

    doc = pymupdf.open(pdf)
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)
    pix.save(temp_png)
    doc.close()

    with rasterio.open(temp_png) as src:
        data = src.read()
        profile = src.profile.copy()

    scaled_x = {lon: x * scale for lon, x in final_x_dict.items()}
    scaled_y = {lat: y * scale for lat, y in final_y_dict.items()}

    gcps = []
    for lon, x in scaled_x.items():
        for lat, y in scaled_y.items():
            row = y
            col = x
            if not north_up_flag:
                row = x
                col = y
            gcps.append(
                GroundControlPoint(
                    row=row,
                    col=col,
                    x=lon,  # world x = longitude
                    y=lat  # world y = latitude
                )
            )

    transform = from_gcps(gcps)

    profile.update(
        driver="GTiff",
        crs="EPSG:4326",
        transform=transform
    )

    with rasterio.open(output_tif, "w", **profile) as dst:
        dst.write(data)

    print("Transform:", transform)

def get_center_lat_lon():
    with rasterio.open(f"{airport_name}_affine.tif") as src:
        center_row = src.height // 2
        center_col = src.width // 2

        x, y = src.xy(center_row, center_col)
        return x.item(), y.item()

if os.path.exists(f"{airport_name}_affine.tif"):
    print("Affine file exists")
else:
    download_apd()
    coords_dict, north_flag = get_coords_north_flag()
    x_dict, y_dict = get_gridlines(coords_dict, north_flag)
    georeferencing(x_dict, y_dict, north_flag)
    print(f"Successfully created {airport_name}_affine.tif")
lon, lat = get_center_lat_lon()

TIFF_PATH = f"{airport_name}_affine.tif"

LAT0 = lat
LON0 = lon
RADIUS_MI = 2
API_URL = f"https://api.adsb.lol/v2/lat/{LAT0}/lon/{LON0}/dist/{RADIUS_MI}"

UPDATE_MS = 1200
MAX_LABELS = 25

def fetch_adsb():
    r = requests.get(API_URL, headers={"accept": "application/json"}, timeout=10)
    r.raise_for_status()
    return r.json()

def extract_ground(data):
    pts = []
    labels = []

    for ac in data.get("ac", []):
        lat = ac.get("lat")
        lon = ac.get("lon")
        alt = ac.get("alt_baro")
        track = ac.get("track")

        if lat is None or lon is None:
            continue

        flight = (ac.get("flight") or "").strip()
        reg = (ac.get("r") or "").strip()
        hexid = (ac.get("hex") or "").strip()
        label = flight or reg or hexid or "UNKNOWN"

        alt_text = f"ALT: {alt}"
        trk_text = f"TRK: {track}"

        pts.append((lon, lat))
        labels.append((label, alt_text, trk_text))

    if not pts:
        return np.empty((0, 2), dtype=float), []

    return np.array(pts, dtype=float), labels

def normalize_to_rgb_uint8(img):

    if img.shape[0] >= 3:
        bg = reshape_as_image(img[:3])  # -> (rows, cols, 3)
        if bg.dtype != np.uint8:
            bg = np.clip(bg, 0, 255).astype(np.uint8)
        return bg

    band = img[0].astype(np.float32)
    mn = np.nanmin(band)
    mx = np.nanmax(band)

    if mx - mn < 1e-9:
        band8 = np.zeros_like(band, dtype=np.uint8)
    else:
        band8 = (255 * (band - mn) / (mx - mn)).astype(np.uint8)

    return np.dstack([band8, band8, band8])


def numpy_to_qpixmap(arr):

    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"Expected (H, W, 3) RGB image, got shape {arr.shape}")

    arr = np.ascontiguousarray(arr)
    h, w, _ = arr.shape
    bytes_per_line = 3 * w

    qimg = QtGui.QImage( arr.data,
        w,
        h,
        bytes_per_line,
        QtGui.QImage.Format.Format_RGB888,
    ).copy()

    return QtGui.QPixmap.fromImage(qimg)

class AirportLiveWindow(QtWidgets.QMainWindow):
    def __init__(self, tiff_path: str):
        super().__init__()
        self.setWindowTitle("Live Airport Surface Traffic")

        self.ds = rasterio.open(tiff_path)
        img = self.ds.read()
        transform = self.ds.transform
        bounds = self.ds.bounds

        self.left = bounds.left
        self.right = bounds.right
        self.bottom = bounds.bottom
        self.top = bounds.top

        # Transformer for ADS-B lon/lat -> TIFF CRS
        self.to_map = Transformer.from_crs("EPSG:4326", self.ds.crs, always_xy=True)

        # Convert raster to displayable RGB
        self.bg = normalize_to_rgb_uint8(img)

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
            transform.a,  # m11
            transform.d,  # m12
            transform.b,  # m21
            transform.e,  # m22
            transform.c,  # dx
            transform.f,  # dy
        )
        self.img_item.setTransform(qt_transform)

        self.plot.addItem(self.img_item)

        self.plot.setXRange(self.left, self.right, padding=0)
        self.plot.setYRange(self.bottom, self.top, padding=0)

        self.scatter = pg.ScatterPlotItem(
            size=10,
            pen=None,
            brush=pg.mkBrush(255, 0, 0, 200)
        )
        self.plot.addItem(self.scatter)

        self.label_items = []

        # Fullscreen toggle
        self._fullscreen = False
        self.shortcut = QtGui.QShortcut(QtGui.QKeySequence("F"), self)
        self.shortcut.activated.connect(self.toggle_fullscreen)

        # Timer
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.kick_update)
        self.timer.start(UPDATE_MS)

    def toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    def kick_update(self):
        threading.Thread(target=self._fetch_and_queue, daemon=True).start()

    def _fetch_and_queue(self):
        try:
            data = fetch_adsb()
            lonlat, labels = extract_ground(data)

            if lonlat.size:
                xs, ys = self.to_map.transform(lonlat[:, 0], lonlat[:, 1])
                xy = np.column_stack([xs, ys])
            else:
                xy = np.empty((0, 2), dtype=float)

            if len(labels) > MAX_LABELS:
                labels = labels[:MAX_LABELS]
                xy = xy[:MAX_LABELS]

            QtCore.QMetaObject.invokeMethod(
                self,
                "_apply",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(object, xy),
                QtCore.Q_ARG(object, labels),
            )

        except Exception as e:
            print("Update error:", e)

    @QtCore.pyqtSlot(object, object)
    def _apply(self, xy, labels):
        # Update scatter points
        self.scatter.setData(
            [{"pos": (float(x), float(y))} for x, y in xy]
        )

        # Remove old labels
        for item in self.label_items:
            self.plot.removeItem(item)
        self.label_items.clear()

        # Add new labels
        for (x, y), (lab, alt_text, trk_text) in zip(xy, labels):
            text = f"{lab}\n{alt_text}\n{trk_text}"
            t = pg.TextItem(
                text=text,
                anchor=(0, 1),
                color=(255, 255, 0),
                fill=pg.mkBrush(0, 0, 0, 100),
                border=pg.mkPen(255, 255, 255, 180),
            )
            t.setPos(float(x), float(y))
            self.plot.addItem(t)
            self.label_items.append(t)

app = QtWidgets.QApplication(sys.argv)
win = AirportLiveWindow(TIFF_PATH)
win.resize(1440, 900)
win.show()
sys.exit(app.exec())