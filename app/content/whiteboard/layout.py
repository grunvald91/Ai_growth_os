"""Offline repair of two-column drawing plans; no image edits or model calls."""
import io
import math

from PIL import Image, ImageDraw, ImageChops

from .contracts import WhiteboardRegion, WhiteboardRegionPlan


def _blank_row(ink, y, x0=0):
    width, height = ink.size
    row = min(height - 1, y * height // 1000)
    return ink.crop((x0, row, width, row + 1)).getbbox() is None


def _blank_column(ink, x, y0=0):
    width, height = ink.size
    column = min(width - 1, x * width // 1000)
    return ink.crop((column, y0 * height // 1000, column + 1, height)).getbbox() is None


def _overlap(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def fallback_regions(scene, ink, reason, coverage=None):
    """Disjoint regions covering the whole image when the plan cannot be used or repaired.

    The video still finishes; the audit marks it for human review because a
    split may reveal a later beat's drawing early or cut through an object.
    """
    count = len(scene.beats)
    method = 'equal_horizontal_bands'
    if count == 2:
        columns = [x for x in range(400, 601) if _blank_column(ink, x)]
        rows = [y for y in range(350, 651) if _blank_row(ink, y)]
        if columns:
            cut = min(columns, key=lambda x: abs(x - 500)); method = 'blank_column'
            boxes = [(0, 0, cut, 1000), (cut, 0, 1000, 1000)]
        elif rows:
            cut = min(rows, key=lambda y: abs(y - 500)); method = 'blank_row'
            boxes = [(0, 0, 1000, cut), (0, cut, 1000, 1000)]
        else:
            boxes = [(0, 0, 1000, 500), (0, 500, 1000, 1000)]
    else:
        # The image prompt asks for stacked horizontal bands. Move each cut to the
        # nearest blank row so a band boundary does not slice through a drawing.
        edges = [round(i * 1000 / count) for i in range(count + 1)]
        reach = 500 // count
        for i in range(1, count):
            blank = [y for y in range(max(edges[i - 1] + 1, edges[i] - reach), min(999, edges[i] + reach) + 1)
                     if _blank_row(ink, y)]
            if blank:
                edges[i] = min(blank, key=lambda y: abs(y - edges[i])); method = 'blank_rows'
        boxes = [(0, edges[i], 1000, edges[i + 1]) for i in range(count)]
    result = WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=i, box=box) for i, box in enumerate(boxes)])
    result.check_for(scene)
    return result, {'operation': 'offline_fallback_region_split', 'method': method, 'reason': reason,
                    'original_ink_coverage': coverage, 'final_ink_coverage': 1.0,
                    'final_regions': result.model_dump(), 'image_unchanged': True, 'provider_calls': 0,
                    'human_review_required': True}


def complete_diagram_regions(scene, image_png, plan):
    with Image.open(io.BytesIO(image_png)) as image:
        if image.width * image.height > 10_000_000:
            raise ValueError('Image exceeds layout bounds')
        ink = image.convert('L').point(lambda value: 255 if value < 220 else 0)
    try:
        plan.check_for(scene)
    except ValueError:
        return fallback_regions(scene, ink, 'beat_without_region')
    if len(scene.beats) != 2:
        # E.g. the last scene with the closing call to action. Overlapping regions of
        # different beats would stop the renderer after every paid call was made.
        if any(first.beat_index != second.beat_index and _overlap(first.box, second.box)
               for index, first in enumerate(plan.regions) for second in plan.regions[index + 1:]):
            return fallback_regions(scene, ink, 'overlap')
        return plan, None
    width, height = ink.size
    covered = Image.new('L', ink.size, 0)
    draw = ImageDraw.Draw(covered)
    overlap = False
    for index, first in enumerate(plan.regions):
        x1,y1,x2,y2=first.box
        draw.rectangle((x1*width//1000,y1*height//1000,math.ceil(x2*width/1000)-1,
                        math.ceil(y2*height/1000)-1),fill=255)
        for second in plan.regions[index+1:]:
            a,b=first.box,second.box
            if first.beat_index != second.beat_index and max(a[0],b[0]) < min(a[2],b[2]) and max(a[1],b[1]) < min(a[3],b[3]):
                overlap=True
    total=ink.histogram()[255]
    coverage=ImageChops.multiply(ink,covered).histogram()[255]/total if total else 1.0
    if not overlap and coverage >= .995:
        return plan, None
    reason = 'overlap' if overlap else 'incomplete_diagram_coverage'
    if not total:
        return fallback_regions(scene, ink, reason, coverage)
    # The image prompt establishes a title and two separate columns. Only
    # repair that layout when actual blank separators confirm it. Never cut
    # a visible object or infer a two-column layout for inseparable artwork.
    headers=[y for y in range(180,301) if ink.crop((0,min(height-1,y*height//1000),width,min(height-1,y*height//1000)+1)).getbbox() is None]
    if not headers:
        return fallback_regions(scene, ink, reason, coverage)
    header=min(headers,key=lambda y:abs(y-200))
    cuts=[x for x in range(450,551) if ink.crop((min(width-1,x*width//1000),header*height//1000,min(width-1,x*width//1000)+1,height)).getbbox() is None]
    if not cuts:
        return fallback_regions(scene, ink, reason, coverage)
    cut=min(cuts,key=lambda x:abs(x-500))
    result=WhiteboardRegionPlan(regions=[
        WhiteboardRegion(beat_index=0,box=(0,0,1000,header)),
        WhiteboardRegion(beat_index=0,box=(0,header,cut,1000)),
        WhiteboardRegion(beat_index=1,box=(cut,header,1000,1000)),
    ])
    result.check_for(scene)
    return result, {'operation':'offline_two_column_region_repair',
        'reason':'overlap' if overlap else 'incomplete_diagram_coverage',
        'original_ink_coverage':coverage,'final_ink_coverage':1.0,
        'original_regions':plan.model_dump(),'final_regions':result.model_dump(),
        'image_unchanged':True,'provider_calls':0}
