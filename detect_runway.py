import pymupdf
import math

page = pymupdf.open('KJFK.PDF')[0]
paths = page.get_drawings()

my_paths = []
ar = []
unique_pts = set()

for x in range(len(paths)-1):
    path = paths[x]
    if path["type"] == "f" and path['fill'] == (0.0, 0.0, 0.0):
        x0, y0, x1, y1 = path['rect']
        area = (x1 - x0) * (y1 - y0)
        if area > 400:
            ar.append([area, path["seqno"]])

    if path.get('seqno') in [243]:
        for y in path['items']:
            pt1, pt2 = y[1], y[2]
            unique_pts.add(pt1)
            unique_pts.add(pt2)

        my_paths.append(path)

print(unique_pts)
unique_pts = list(unique_pts)
x1, y1 = unique_pts[0]
for pts in unique_pts:
    x2, y2 = pts
    distance = math.sqrt((x2-x1)**2 + (y2-y1)**2)
    print(distance)
# ('l', Point(177.80999755859375, 90.989990234375), Point(175.59999084472656, 94.70999145507812))
# ('l', Point(175.59999084472656, 94.70999145507812), Point(331.6300048828125, 187.5699920654297))
# ('l', Point(331.6300048828125, 187.5699920654297), Point(333.8399963378906, 183.8599853515625))
# ('l', Point(333.8399963378906, 183.8599853515625), Point(177.80999755859375, 90.989990234375))