"""Redraw the Japanese text baked into the TIM images (GRP*/*.CK TIM chains, tools/tim.py).

Ported from the Sangokushi 6 PUK gfx.py. Lettering comes from the stroke glyphs of
tools/latinfont.py (original drawings), rendered with antialiasing at any size and
mapped to the nearest colour of the image's own CLUT (palettes, sizes and the TIM count
never change), or for tiny 4bpp icons from the pixel glyphs of tools/tinyfont.py.

  plate(): light text with a dark shadow/outline on a textured plate (menu bars, tiles,
    buttons): the old lettering and its shadow are erased by inpainting from the plate
    around them, then the English is drawn in the original's fill and shadow colours.
  clear(): text on transparency (the box is cleared, then redrawn).
  banner(): big outlined lettering on transparency.

tl/gfx.txt: "FILE.tim[.spot]<TAB>English" ('|' = line break). FILE is the disc path
without .CK (GRP3/ILGTMNUS); spot = index into the item's box list in ITEMS (multi-label
images). Items not in ITEMS are a plate over the whole image.
"""
import collections, os, random, sys
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
import latinfont, tim, tinyfont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'gfx.txt')
SS = 4
EXT = '.CK'

def luma(c):
    return (c[0] * 299 + c[1] * 587 + c[2] * 114) // 1000

class Canvas:
    """Palette helpers around a tim.Texture."""
    def __init__(self, tex):
        self.t = tex
        self.w, self.h, self.pal = tex.w, tex.h, tex.pal
        self.cache = {}

    def get(self, x, y):
        return self.t.get(x, y)

    def idx(self, x, y):
        return self.t.idx(x, y)

    def put(self, x, y, i):
        self.t.put(x, y, i)

    def index(self, rgb, alpha=0x80):
        """Nearest palette entry to an RGB colour (alpha 0 = the transparent colour)."""
        key = tuple(int(round(v)) for v in rgb[:3]) + (int(round(alpha)),)
        if key not in self.cache:
            self.cache[key] = min(range(len(self.pal)), key=lambda i: sum((a - b) ** 2 for a, b in zip(self.pal[i][:3], key[:3]))
                                  + 6 * (self.pal[i][3] - key[3]) ** 2)
        return self.cache[key]

# ---------------------------------------------------------------- lettering

def text_masks(text, cap, widths, condense=1.0, spacing=None, descender=None):
    """Coverage images (PIL 'L', SS times the final size) of text drawn with each
    stroke width in widths (px, final scale): wider strokes give the outline layers.
    Returns (images, (w, h), (left, top) of the cap box) at final scale."""
    stroke = widths[0]
    s = (cap - stroke) / latinfont.C
    spacing = stroke * 0.45 if spacing is None else spacing
    pad = max(widths) / 2 + 1
    xs, x = [], pad
    for c in text:
        w, _ = latinfont.GLYPHS[c]
        xs.append(x)
        x += (w * s * condense + stroke + spacing) if c != ' ' else cap * 0.32
    width = x - spacing + pad
    if descender is None:
        descender = any(c in 'gjpqy,;' for c in text)
    height = cap + stroke + 2 * pad + (3 * s if descender else 0)
    base = pad + cap - stroke / 2
    out = []
    for sw in widths:
        im = Image.new('L', (int(width * SS) + 1, int(height * SS) + 1), 0)
        dr = ImageDraw.Draw(im)
        r = sw * SS / 2
        for c, x0 in zip(text, xs):
            for st in latinfont.GLYPHS[c][1]:
                pts = [((x0 + stroke / 2 + px * s * condense) * SS, (base - py * s) * SS) for px, py in st]
                for a, b in zip(pts, pts[1:]):
                    dr.line([a, b], fill=255, width=max(1, round(sw * SS)))
                for px, py in pts:
                    dr.ellipse((px - r, py - r, px + r, py + r), fill=255)
        out.append(im)
    return out, (width, height), (pad, pad)

def fit(lines, cap, box_w, box_h, widths_of, min_cap=5.0, min_condense=0.7, spacing=None):
    """Shrink (condense first, then cap) until every line fits box_w and all lines box_h.
    -> (cap, condense, [(masks, (w, h), (px, py))])"""
    condense = 1.0
    while True:
        r = [text_masks(l, cap, widths_of(cap), condense, spacing(cap) if callable(spacing) else spacing, descender=False)
             for l in lines]
        lh = cap + widths_of(cap)[-1]
        tw = max(m[1][0] for m in r) - 2 * (max(widths_of(cap)) / 2 + 1)
        if (tw <= box_w and lh * len(lines) + (len(lines) - 1) * 1 <= box_h + 1) or cap <= min_cap:
            if tw > box_w + 0.5 or lh * len(lines) > box_h + 3:
                raise ValueError('%r does not fit %dx%d' % ('|'.join(lines), box_w, box_h))
            return cap, condense, r
        if condense > min_condense and lh * len(lines) + (len(lines) - 1) * 1 <= box_h + 1:
            condense *= 0.96            # too wide: condense first
        else:
            cap *= 0.95

# ---------------------------------------------------------------- plates

def plate_style(cv, box, mode='light', grow=2, sens=(24, 0.4), amin=0x40, th=None):
    """-> (text mask set, fill rgb, outline rgb, text alpha) for light text with a dark
    shadow (mode 'light') or dark text on a light plate ('dark'). th = fixed luma threshold
    of the lettering (when it covers too much of the box for the median to be the plate)."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    L = {p: luma(cv.get(*p)) for p in px}
    A = {p: cv.get(*p)[3] for p in px}
    vals = sorted(L[p] for p in px if A[p] > amin)
    med = vals[len(vals) // 2]
    spread = max(6, vals[len(vals) * 2 // 3] - vals[len(vals) // 3])
    if th is not None:
        core = {p for p in px if (L[p] >= th if mode == 'light' else L[p] <= th) and A[p] > amin}
        med = sorted(L[p] for p in px if p not in core)[(len(px) - len(core)) // 2]
    elif mode == 'light':
        hi = vals[int(len(vals) * 0.97)]
        th = med + max(sens[0], (hi - med) * sens[1])
        core = {p for p in px if L[p] >= th and A[p] > amin}
    else:
        lo = vals[int(len(vals) * 0.03)]
        th = med - max(sens[0], (med - lo) * sens[1])
        core = {p for p in px if L[p] <= th and A[p] > amin}
    if not core:
        raise ValueError('no lettering found in %r' % (box,))
    ring = collections.Counter()
    for (x, y) in core:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                q = (x + dx, y + dy)
                if q in L and q not in core and ((L[q] < med) if mode == 'light' else (L[q] > med)):
                    ring[q] += 1
    fillc = sorted((cv.get(*p) for p in core), key=luma)
    fill = fillc[int(len(fillc) * 0.8)] if mode == 'light' else fillc[len(fillc) // 5]
    outc = sorted((cv.get(*p) for p in ring), key=luma)
    outline = (outc[len(outc) // 10] if mode == 'light' else outc[len(outc) * 3 // 4]) if outc else None
    mask = set(core) | set(ring)
    for _ in range(grow):
        mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if (x + dx, y + dy) in L and abs(L[x + dx, y + dy] - med) > spread}
    mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (x + dx, y + dy) in L}
    return mask, fill[:3], outline[:3] if outline else None, fill[3]

def erase(cv, box, mask, seed=0, sample=False):
    """Inpaint masked pixels from the outside in (onion peel): each pass fills the pixels
    with the most known neighbours by copying a random known pixel within 2 px, so the
    background's local colours and grain continue under the text. sample: copy random
    unmasked pixels from anywhere in the box instead (plain noisy stone under big lettering)."""
    x0, y0, x1, y1 = box
    rnd = random.Random(seed)
    todo = {p for p in mask if x0 <= p[0] <= x1 and y0 <= p[1] <= y1}
    if sample:
        src = [cv.idx(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if (x, y) not in todo]
        for p in sorted(todo):
            cv.put(p[0], p[1], rnd.choice(src))
        return
    near = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if dx or dy]
    def known(x, y):
        return [(x + dx, y + dy) for dx, dy in near
                if x0 <= x + dx <= x1 and y0 <= y + dy <= y1 and (x + dx, y + dy) not in todo]
    while todo:
        cand = {p: known(*p) for p in sorted(todo)}
        best = max(len(v) for v in cand.values())
        if best == 0:
            break
        need = max(1, min(best, 8))
        layer = [p for p, v in cand.items() if len(v) >= need]
        for p in layer:
            cv.put(p[0], p[1], cv.idx(*rnd.choice(cand[p])))
        todo -= set(layer)

def draw(cv, box, text, fill, outline, alpha=0x80, cap=None, align='center', bold=0.14, lw=1.0,
         vshift=0, hshift=0, shadow=None, min_cap=5.0, sharp=0.0):
    """English (lines split on '|') in box, fill + outline colours. shadow=(dx, dy): the
    outline colour is a drop shadow of the letters instead of a ring round them. sharp
    (0-1) pushes antialiased edges towards ink or background (crisper small text)."""
    x0, y0, x1, y1 = box
    lines = text.split('|')
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    ring = outline is not None and not shadow
    widths_of = lambda c: [max(1.0, c * bold)] + ([max(1.0, c * bold) + 2 * lw] if ring else [])
    cap = cap or min(bh / len(lines) * 0.62, 22)
    cap = min(cap, bh / len(lines) * 0.75)
    sx, sy = shadow or (0, 0)
    cap, condense, rs = fit(lines, cap, bw - 2 - sx, bh - sy, widths_of, min_cap)
    lh = cap + widths_of(cap)[-1] + (1 if len(lines) > 1 else 0)
    total = lh * len(lines) - (1 if len(lines) > 1 else 0)
    top = y0 + (bh - sy - total) / 2 + vshift
    def cover(v):
        if sharp:
            v = min(1.0, max(0.0, (v - 0.5) * (1 + 3 * sharp) + 0.5))
        return v
    for k, (masks, (tw, th), (px, py)) in enumerate(rs):
        size = (int(tw) + 1, int(th) + 1)
        layers = [m.resize(size, Image.BOX) for m in masks]
        inkw = tw - 2 * px
        ox = {'center': x0 + (bw - sx - inkw) / 2, 'left': x0 + 1, 'right': x1 - sx - inkw}[align] - px + hshift
        oy = top + k * lh - py + (widths_of(cap)[-1] - widths_of(cap)[0]) / 2
        ox, oy = int(round(ox)), int(round(oy))
        get = lambda l, x, y: cover(l.getpixel((x, y)) / 255) if 0 <= x < size[0] and 0 <= y < size[1] else 0
        for y in range(-1, size[1] + sy + 1):
            for x in range(-1, size[0] + sx + 1):
                tx, ty = ox + x, oy + y
                if not (x0 <= tx <= x1 and y0 <= ty <= y1):
                    continue
                c = get(layers[0], x, y)
                if shadow:
                    e = get(layers[0], x - sx, y - sy) if outline else 0
                else:
                    e = get(layers[1], x, y) if ring else 0
                if c <= 0.04 and e <= 0.08:
                    continue
                base = cv.get(tx, ty)
                col = base[:3]
                if e > 0:
                    col = tuple(p + (q - p) * min(1, e * 1.3) for p, q in zip(col, outline))
                if c > 0:
                    col = tuple(p + (q - p) * min(1, c * 1.15) for p, q in zip(col, fill))
                a = base[3] + (max(alpha, base[3]) - base[3]) * max(c, e)
                cv.put(tx, ty, cv.index(col, a))

def clear(cv, box, text, dark=False, amin=0x60, outline=True, colours=None, **kw):
    """Text on transparency: fill/outline = bright/dark opaque colours of the box (or
    colours=(fill, outline) rgb), which is cleared to the transparent colour and redrawn."""
    x0, y0, x1, y1 = box
    inside = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if colours:
        fill, line = colours
    else:
        opq = sorted((cv.get(x, y) for x, y in inside if cv.get(x, y)[3] >= amin), key=luma)
        fill, line = opq[len(opq) * 19 // 20], opq[len(opq) // 10]
        if dark:
            fill, line = opq[len(opq) // 20], opq[len(opq) * 9 // 10]
    t = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    if cv.pal[t][3]:
        t = cv.index((0, 0, 0))         # no transparent colour (opaque full-screen image): black
    for x, y in inside:
        cv.put(x, y, t)
    if text:
        draw(cv, box, text, fill[:3], line[:3] if outline else None, 0x80, **kw)

def plate(cv, text, box=None, mode='light', erase_box=None, style=None, colours=None, sample=False, **kw):
    """colours = (fill rgb, outline rgb): override the measured ones (e.g. a tile whose
    lettering is too thin to measure well)."""
    box = box or (1, 1, cv.w - 2, cv.h - 2)
    if mode == 'clear':
        return clear(cv, box, text, colours=colours, **kw)
    boxes = erase_box if isinstance(erase_box, list) else [erase_box or box]
    mask, fill, outline, alpha = plate_style(cv, boxes[0], mode, **(style or {}))
    for b in boxes[1:]:
        mask |= plate_style(cv, b, mode, **(style or {}))[0]
    if colours:
        fill, outline = colours
    for b in boxes:
        erase(cv, b, mask, sample=sample)
    if text and '^' in text:
        # date column: left part in the first col px, the rest left-aligned after it
        col = kw.pop('col', 50)
        left, right = text.split('^')
        kw['align'] = 'left'
        draw(cv, (box[0] + 2, box[1], box[0] + col - 1, box[3]), left, fill, outline, alpha, **kw)
        draw(cv, (box[0] + col + 2, box[1], box[2], box[3]), right, fill, outline, alpha, **kw)
    elif text:
        kw.pop('col', None)
        draw(cv, box, text, fill, outline, alpha, **kw)

# ---------------------------------------------------------------- pixel icons

def pixel(cv, text, box, mode='dark', font=None, ink=None, shadow=None, grow=0, vshift=0, hshift=0, also=(), th=None, grey=False, outline=None, flat=None, noise=None):
    """Tiny icon lettering (tinyfont): the old glyph = pixels of the ink palette index (the
    darkest colour in the box, or the brightest for mode 'light'; ink= forces it) plus those
    of the shadow index; they are inpainted, then text is drawn in the same index, centred.
    shadow = (palette index, dx, dy) drop shadow. font: 'mid' / 'small' (default: mid when
    it fits). also = more palette indexes to erase; th = erase every pixel darker (mode
    'dark') or brighter than this luma; grey = erase every grey pixel (lettering and its
    outline on a coloured plate); outline = palette index of a 1 px ring round the text;
    flat = fill the whole box with this palette index instead of inpainting; noise = fill it
    with random picks from these palette indexes (grainy plates)."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    rgb = lambda c: cv.index(c[:3], *c[3:]) if isinstance(c, tuple) else c     # (r, g, b) -> palette index
    ink = rgb(ink)
    if shadow:
        shadow = (rgb(shadow[0]),) + tuple(shadow[1:])
    if noise:
        noise = [rgb(c) for c in noise]
    if ink is None:
        used = collections.Counter(cv.idx(*p) for p in px)
        key = lambda i: luma(cv.pal[i])
        ink = (min if mode == 'dark' else max)((i for i in used if cv.pal[i][3]), key=key)
    kill = {ink} | ({shadow[0]} if shadow else set()) | set(also)
    mask = {p for p in px if cv.idx(*p) in kill}
    if grey:
        mask |= {p for p in px if max(cv.get(*p)[:3]) - min(cv.get(*p)[:3]) < 24}
    if th is not None:
        mask |= {p for p in px if (luma(cv.get(*p)) <= th if mode == 'dark' else luma(cv.get(*p)) >= th)}
    for _ in range(grow):
        mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if x0 <= x + dx <= x1 and y0 <= y + dy <= y1}
    if isinstance(flat, tuple):
        flat = cv.index(flat[:3], *flat[3:])
    if noise:
        rnd = random.Random(x0 * 7 + y0)
        for p in px:
            cv.put(p[0], p[1], rnd.choice(noise))
    elif flat is not None:
        for p in px:
            cv.put(p[0], p[1], flat)
    else:
        erase(cv, box, mask)
    if not text:
        return
    lines = text.split('|')
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    sx, sy = (shadow[1], shadow[2]) if shadow else (0, 0)
    if outline is not None:
        bw, bh, x0, y0 = bw - 2, bh - 2, x0 + 1, y0 + 1
    fonts = {'mid': tinyfont.MID, 'small': tinyfont.SMALL}
    for name in ([font] if font else ['mid', 'small']):
        f = fonts[name]
        rs = [tinyfont.render(l, f) for l in lines]
        # lowercase descenders hang below the cap height (7): count them only when present
        hs = [h for _, h, _ in rs]
        total = sum(hs) + len(lines) - 1
        if max(w for w, _, _ in rs) + sx <= bw and total + sy <= bh + (2 if name == 'mid' else 0):
            break
    else:
        raise ValueError('%r does not fit %dx%d' % (text, bw, bh))
    cap = 7 if f is tinyfont.MID else 5
    lh = cap + 1
    top = y0 + (bh - sy - (lh * len(lines) - 1)) // 2 + vshift
    for k, (w, h, ink_px) in enumerate(rs):
        ox = x0 + (bw - sx - w + 1) // 2 + hshift
        oy = top + k * lh
        if outline is not None:
            ring = {(x + dx, y + dy) for (x, y) in ink_px for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            for (x, y) in ring:
                tx, ty = ox + x, oy + y
                if box[0] <= tx <= x1 and box[1] <= ty <= y1:
                    cv.put(tx, ty, outline)
        if shadow:
            for (x, y) in ink_px:
                tx, ty = ox + x + sx, oy + y + sy
                if x0 <= tx <= x1 and y0 <= ty <= y1:
                    cv.put(tx, ty, shadow[0])
        for (x, y) in ink_px:
            tx, ty = ox + x, oy + y
            if x0 <= tx <= x1 and y0 <= ty <= y1 + 1:
                cv.put(tx, ty, ink)

def icons(n, w=16, h=16, inset=2, **kw):
    """A strip of n icons of w x h (frame inset px)."""
    return [dict(kw, kind='pixel', box=(k * w + inset, inset, k * w + w - 1 - inset, h - 1 - inset)) for k in range(n)]

# ---------------------------------------------------------------- engraved banners

def engrave(cv, text, cap=20, bold=0.18, bevel=1.6, rim=1.0, edge=1.0, colours=None, min_cap=10):
    """Big lettering on transparency in the HEX/SSLG.S6 style (crisp, no antialiasing): dark
    stroke, light bevel (bright up-left, grey down-right), green rim, black edge. The whole
    image is cleared first. colours = (core, bevel light, bevel dark, rim, edge) rgb."""
    core_c, hi_c, lo_c, rim_c, edge_c = colours or ((8, 8, 8), (230, 230, 230), (164, 172, 156), (82, 115, 82), (8, 8, 8))
    t = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    for y in range(cv.h):
        for x in range(cv.w):
            cv.put(x, y, t)
    condense = 1.0
    while True:
        sw = max(2.0, cap * bold)
        widths = [sw, sw + 2 * bevel, sw + 2 * (bevel + rim), sw + 2 * (bevel + rim + edge)]
        masks, (tw, th), _ = text_masks(text, cap, widths, condense, descender=False)
        if tw <= cv.w - 2 or cap <= min_cap:
            break
        if condense > 0.8:
            condense *= 0.97
        else:
            cap *= 0.96
    if tw > cv.w:
        raise ValueError('%r: %d px wide, image %d' % (text, tw, cv.w))
    size = (int(tw) + 1, int(th) + 1)
    layers = [m.resize(size, Image.BOX) for m in masks]
    on = [[[layers[k].getpixel((x, y)) >= 128 for x in range(size[0])] for y in range(size[1])] for k in range(4)]
    ox, oy = (cv.w - size[0]) // 2, (cv.h - size[1]) // 2
    idx = [cv.index(c) for c in (core_c, hi_c, lo_c, rim_c, edge_c)]
    for y in range(size[1]):
        for x in range(size[0]):
            tx, ty = ox + x, oy + y
            if not (0 <= tx < cv.w and 0 <= ty < cv.h):
                continue
            if on[0][y][x]:
                i = idx[0]
            elif on[1][y][x]:
                # bevel: lit when the stroke lies down-right of it
                lit = any(on[0][y + d][x + d] for d in (1, 2) if y + d < size[1] and x + d < size[0])
                i = idx[1] if lit else idx[2]
            elif on[2][y][x]:
                i = idx[3]
            elif on[3][y][x]:
                i = idx[4]
            else:
                continue
            cv.put(tx, ty, i)

# ---------------------------------------------------------------- glowing lettering

def glow(cv, text, box, th=150, cap=12, bold=0.1, maxd=4.5, align='center', hshift=0, vshift=0, min_cap=7.0, spacing=0.17, solid=0.45, soft=False):
    """Bright lettering with a soft halo on a flat dark ground (title menu): the original's
    halo is measured as the mean colour by distance from its core (luma >= th) pixels, the
    box is cleared to the ground colour, and the English core gets the same halo."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    core = [p for p in px if luma(cv.get(*p)) >= th]
    if not core:
        raise ValueError('no lettering found in %r' % (box,))
    def dist(cs):
        d = {}
        for (x, y) in px:
            m = min(((x - a) ** 2 + (y - b) ** 2 for a, b in cs if abs(x - a) <= maxd and abs(y - b) <= maxd), default=None)
            d[x, y] = m ** 0.5 if m is not None else None
        return d
    d0 = dist(core)
    buckets = collections.defaultdict(list)
    for p, v in d0.items():
        buckets[None if v is None or v > maxd else round(v * 2) / 2].append(cv.get(*p))
    mean = lambda cs: tuple(sum(c[k] for c in cs) / len(cs) for k in range(3))
    ground = collections.Counter(c[:3] for c in buckets.pop(None)).most_common(1)[0][0]
    halo = {k: mean(v) for k, v in buckets.items()}
    corec = sorted((c for c in buckets[0]), key=luma)[len(buckets[0]) * 3 // 5][:3]
    halo[0] = corec
    lines = text.split('|')
    if cap == 'auto':
        # zoom frames: fit the English to the original lettering's own box
        xs, ys = [p[0] for p in core], [p[1] for p in core]
        cap = (max(ys) - min(ys) + 2) / len(lines) * 0.62
        min_cap = min(min_cap, cap * 0.6)
        m = max(2, (max(xs) - min(xs)) // 6)      # English runs longer: a bit wider than the original
        x0, x1 = max(x0, min(xs) - m), min(x1, max(xs) + m)
        y0, y1 = min(ys) - 1, max(ys) + 1
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    widths_of = lambda c: [max(0.6, c * bold)]
    sp = lambda c: c * spacing      # letter gap, relative to the cap height
    cap, condense, rs = fit(lines, cap, bw - 2, bh, widths_of, min_cap, spacing=sp)
    lh = cap * (1.3 if len(lines) > 1 else 1) + widths_of(cap)[0] + 1
    top = y0 + (bh - (lh * (len(lines) - 1) + cap + widths_of(cap)[0])) / 2 + vshift
    cov = {}
    for k, (masks, (tw, tht), (ppx, ppy)) in enumerate(rs):
        size = (int(tw) + 1, int(tht) + 1)
        m = masks[0].resize(size, Image.BOX)
        inkw = tw - 2 * ppx
        ox = {'center': x0 + (bw - inkw) / 2, 'left': x0 + 1, 'right': x1 - inkw}[align] - ppx + hshift
        oy = top + k * lh - ppy
        ox, oy = int(round(ox)), int(round(oy))
        for y in range(size[1]):
            for x in range(size[0]):
                v = m.getpixel((x, y)) / 255
                if v > 0.04 and (ox + x, oy + y) in d0:
                    cov[ox + x, oy + y] = max(cov.get((ox + x, oy + y), 0), v)
    new_core = [p for p, v in cov.items() if v >= solid]
    d1 = dist(new_core)
    keys = sorted(halo)
    def halo_at(v):
        k = min(keys, key=lambda q: abs(q - v))
        return halo[k]
    for p in px:
        v = d1[p] if p in d1 else None
        col = ground if v is None or v > maxd else halo_at(v)
        c = cov.get(p, 0)
        if c >= solid:
            col = corec
        if soft and 0 < c < solid:
            col = tuple(a + (b - a) * c for a, b in zip(col, corec))
        cv.put(p[0], p[1], cv.index(col))

# ---------------------------------------------------------------- zoom frames

FILE_FN = None          # path -> clean file bytes (set by apply()/preview(); zoom() reads its source image)

def zoom(cv, text, box, src, cells, scale, centre):
    """A zoom-animation frame drawn from another (already translated) image: the source cells
    (x, y, w, h, dx, dy) are pasted on black at (dx, dy) to make the full-size frame, which is
    scaled by `scale` about `centre` into box and mapped to this palette. text is unused
    (the source image carries the English)."""
    f, t = src
    data = FILE_FN(f + EXT)
    srctex = redraw(data, src, load_tl()[src])[0]
    x0, y0, x1, y1 = box
    w, h = x1 - x0 + 1, y1 - y0 + 1
    full = Image.new('RGB', (w, h))
    im = srctex.image().convert('RGB')
    for sx, sy, sw, sh, dx, dy in cells:
        full.paste(im.crop((sx, sy, sx + sw, sy + sh)), (dx, dy))
    cx, cy = centre
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    small = full.resize((sw, sh), Image.LANCZOS if scale < 1 else Image.NEAREST)
    out = Image.new('RGB', (w, h))
    out.paste(small, (round(cx - cx * scale), round(cy - cy * scale)))
    for y in range(h):
        for x in range(w):
            cv.put(x0 + x, y0 + y, cv.index(out.getpixel((x, y))))

def core(cv, text, box, src, cell, th=150, ink=None, ground=None):
    """2-colour copy of another image's glow lettering (the bright core, luma >= th, of the
    translated source cell (x, y)), drawn over it by the game: ink/ground palette indexes
    (default: the box's two colours, ground = the most common). text is unused."""
    f, t = src
    srctex = redraw(FILE_FN(f + EXT), src, load_tl()[src])[0]
    x0, y0, x1, y1 = box
    used = collections.Counter(cv.idx(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)).most_common()
    ground = used[0][0] if ground is None else ground
    ink = next(i for i, _ in used if i != ground) if ink is None else ink
    sx, sy = cell
    for y in range(y1 - y0 + 1):
        for x in range(x1 - x0 + 1):
            cv.put(x0 + x, y0 + y, ink if luma(srctex.get(sx + x, sy + y)) >= th else ground)

# ---------------------------------------------------------------- items

# (file, tim): [box options per spot]; a spot's options go to plate() ('kind': 'banner' etc.)
MENU = {'shadow': (1, 1), 'cap': 11, 'bold': 0.12, 'sharp': 0.5, 'style': {'grow': 1}}
def menu_bars(rows, cols=2, w=192, h=20):
    """The 384x200 main-menu images: 2 columns x 10 bars of 192x20."""
    return [dict(MENU, box=(c * w + 4, r * h + 3, c * w + w - 6, r * h + h - 4),
                 erase_box=(c * w + 3, r * h + 2, c * w + w - 5, r * h + h - 3)) for r in range(rows) for c in range(cols)]

TILE = {'cap': 9, 'bold': 0.13, 'shadow': (1, 1), 'style': {'grow': 1}}
def tiles(cols, rows, w=48, h=40, **kw):
    """Atlases of 48x40 label tiles (frame 4 px): spot = row*cols + column."""
    return [dict(TILE, box=(c * w + 4, r * h + 5, c * w + w - 5, r * h + h - 7), **kw) for r in range(rows) for c in range(cols)]

def buttons(**kw):
    """160x160: 4 big square buttons of 80x80 (register menus)."""
    return [dict({'cap': 13, 'bold': 0.13, 'style': {'grow': 1}}, box=(c * 80 + 12, r * 80 + 12, c * 80 + 67, r * 80 + 67), **kw)
            for r in range(2) for c in range(2)]

ITEMS = {
    # title menu: 4 columns (normal, lit, normal, lit) x 2 rows of 192x24
    ('GRP3/ILGTMNUS', 0): [{'kind': 'glow', 'box': (c * 192, r * 24, c * 192 + 191, r * 24 + 23)}
                          for r in range(2) for c in range(4)],
    # scenario titles zooming in: 8 frames (row-major) of 192x48, two lines each; the last frame is the lit
    # title-menu lines (ILGTMNUS cells 5, 7) at y 2 / 22, the others that frame scaled about (96, 23)
    ('GRP3/IMENUM', 0): [{'kind': 'zoom', 'box': (c * 192, r * 48, c * 192 + 191, r * 48 + 47), 'src': ('GRP3/ILGTMNUS', 0),
                          'cells': [(192, 24, 192, 24, 0, 2), (576, 24, 192, 24, 0, 22)], 'centre': (96, 23),
                          'scale': w / 175} for (r, c), w in zip([(r, c) for r in range(4) for c in range(2)],
                                                                (41, 69, 85, 105, 122, 140, 157, 175))],
    # scenario lines on the magenta key colour (2 colours), halves of 192x24: drawn over the lit menu lines
    # (cells 5, 7 of image 0) after the zoom, = their glow core pixel for pixel
    ('GRP3/ILGTMNUS', 3): [{'kind': 'core', 'box': (c * 192, 0, c * 192 + 191, 23), 'src': ('GRP3/ILGTMNUS', 0),
                            'cell': (192 + c * 384, 24)} for c in range(2)],
    # war command labels: 10 commands x (normal white, dim grey) tiles of 32x16, black drop shadow
    ('GRP/WINST', 14): [{'kind': 'pixel', 'box': (k * 32 + 2, 2, k * 32 + 30, 14), 'font': 'small', 'ink': 15 if k % 2 == 0 else 12,
                         'shadow': (1, 1, 1), 'also': [0, 1, 11, 12, 13, 14, 15]} for k in range(20)],
    # policy icons 48x48: white label with a black ring under the picture
    **{('GRP/MAINCG2', k): [{'kind': 'pixel', 'box': (2, 33, 45, 43), 'font': 'small', 'ink': (255, 255, 255),
                             'shadow': ((0, 0, 0), 1, 1), 'noise': [(98, 106, 123), (115, 123, 139)]}] for k in range(44, 49)},
    # list screens, Square button label = current column mode: 0 detailed (all columns), 1 brief;
    # stroke index 7 with a ring of index 1 on a plain tile (6 / 5)
    ('GRP/BUTTON', 12): [{'kind': 'pixel', 'box': (k * 16, 0, k * 16 + 15, 15), 'font': 'small', 'ink': 7,
                          'outline': 1, 'flat': 6 if k == 0 else 5} for k in range(2)],
    # road pieces strip: 'remove' label at the right end (transparent ground)
    ('GRP/MAINCG2', 38): [{'kind': 'pixel', 'box': (220, 15, 255, 31), 'font': 'mid', 'ink': 10, 'outline': 15, 'flat': 0}],
    # feast: stamina bar label (8bpp 32x16, stone frame)
    ('GRP2/TDINNER', 4): [{'kind': 'pixel', 'box': (2, 1, 29, 14), 'font': 'mid', 'ink': (246, 156, 8),
                           'shadow': ((0, 0, 106), 1, 1), 'noise': [(65, 65, 65), (57, 57, 82), (41, 41, 41)]}],
    # vertical tab banners 36x104: area map (light on stone), road building / road info (dark on wood)
    ('GRP/MAINCG3', 16): [{'box': (5, 6, 30, 97), 'cap': 9, 'bold': 0.2, 'sharp': 0.6, 'style': {'grow': 1}}],
    **{('GRP/MAINCG3', k): [{'mode': 'dark', 'box': (5, 6, 30, 97), 'cap': 9, 'bold': 0.2, 'sharp': 0.6, 'style': {'grow': 1}}]
       for k in (17, 18)},
    # item sheet: 'number owned' label in the bottom-right box (count drawn by the game to its right)
    **{('GRP/ITEMLIST', k): [{'box': (194, 149, 246, 165), 'erase_box': (193, 148, 250, 166), 'align': 'left', 'cap': 9,
                              'bold': 0.14, 'sharp': 0.6, 'shadow': (1, 1), 'style': {'grow': 1}}] for k in (0, 1)},
    # ruler-select vertical gold banner
    ('GRP3/ILGTMNUS', 2): [{'box': (7, 7, 28, 80), 'erase_box': (5, 5, 30, 90), 'cap': 11, 'bold': 0.2, 'sharp': 0.6, 'style': {'grow': 3, 'sens': (16, 0.3)}}],
    # bold black lettering on grey-blue stone: 2 rows of 24
    **{('GRP/INITBTN', k): [{'mode': 'dark', 'box': (2, r * 24 + 3, w - 3, r * 24 + 20), 'sample': True, 'cap': 12, 'bold': 0.2,
                             'sharp': 1.0, 'colours': ((0, 0, 0), None), 'style': {'th': 60, 'grow': 0}}
                            for r in range(2)] for k, w in ((0, 160), (1, 192))},
}

def load_tl():
    """-> {(file, tim): {spot: text}}"""
    out = collections.defaultdict(dict)
    if os.path.exists(TL):
        for line in open(TL, encoding='ascii').read().splitlines():
            if line.strip() and not line.startswith('#'):
                k, _, v = line.partition('\t')
                parts = k.split('.')
                out[parts[0], int(parts[1])][int(parts[2]) if len(parts) > 2 else 0] = v
    return out

def redraw(data, key, spots):
    """-> (edited texture, clean texture) for one image of a file."""
    offs = tim.chain(data)
    tex = tim.Texture(data, offs[key[1]])
    cv = Canvas(tex)
    boxes = ITEMS.get(key, [{}])
    for spot, text in sorted(spots.items()):
        if spot >= len(boxes):
            raise ValueError('no spot %d' % spot)
        o = dict(boxes[spot])
        kind = o.pop('kind', 'plate')
        if kind == 'plate':
            plate(cv, text, **o)
        elif kind == 'pixel':
            pixel(cv, text, **o)
        elif kind == 'core':
            core(cv, text, **o)
        elif kind == 'zoom':
            zoom(cv, text, **o)
        elif kind == 'glow':
            glow(cv, text, **o)
        elif kind == 'engrave':
            engrave(cv, text, **o)
        else:
            raise ValueError(kind)
    return tex, tim.Texture(data, offs[key[1]])

def apply(file_fn):
    """-> {disc path: new bytes}; file_fn(path) = clean file bytes (path like 'GRP3/ILGTMNUS.CK')."""
    global FILE_FN
    FILE_FN = file_fn
    by_file = collections.defaultdict(dict)
    for (f, t), spots in load_tl().items():
        by_file[f][t] = spots
    out = {}
    for f, images in sorted(by_file.items()):
        path = f + EXT
        clean = file_fn(path)
        buf = bytearray(clean)
        for t, spots in sorted(images.items()):
            try:
                redraw(clean, (f, t), spots)[0].store(buf)
            except ValueError as err:
                raise ValueError('gfx %s.%d: %s' % (f, t, err))
        assert len(buf) == len(clean)
        out[path] = bytes(buf)
    return out

def preview(out, prefixes=None, scale=2, root=os.path.join(ROOT, 'extract')):
    """Before/after sheet of the tl/gfx.txt items (dev; Koei data, keep out of git)."""
    global FILE_FN
    FILE_FN = lambda path: open(os.path.join(root, path), 'rb').read()
    cells = []
    for (f, t), spots in sorted(load_tl().items()):
        k = '%s.%d' % (f, t)
        if prefixes and not any(k.startswith(p) for p in prefixes):
            continue
        d = open(os.path.join(root, f + EXT), 'rb').read()
        try:
            b, a = redraw(d, (f, t), spots)
        except ValueError as err:
            print('%s: %s' % (k, err))
            continue
        pair = [x.image().resize((x.w * scale, x.h * scale), Image.NEAREST) for x in (a, b)]
        cells.append((k, pair))
    W = max([1200] + [a.width * 2 + 8 for _, (a, b) in cells])
    x = y = rowh = 0
    pos = []
    for lab, (a, b) in cells:
        cw = a.width + b.width + 8
        if x + cw > W:
            x, y, rowh = 0, y + rowh + 16, 0
        pos.append((x, y))
        x += cw + 10
        rowh = max(rowh, a.height)
    sheet = Image.new('RGBA', (W, y + rowh + 16), (60, 0, 60, 255))
    dr = ImageDraw.Draw(sheet)
    for (lab, (a, b)), (px, py) in zip(cells, pos):
        dr.text((px, py), lab, fill=(255, 255, 0, 255))
        sheet.alpha_composite(a, (px, py + 12))
        sheet.alpha_composite(b, (px + a.width + 8, py + 12))
    sheet.save(out)
    print(out, len(cells))

if __name__ == '__main__':
    # gfx.py preview out.png [key prefixes...] [--scale N]
    a = sys.argv[3:]
    sc = 2
    if '--scale' in a:
        k = a.index('--scale'); sc = int(a[k + 1]); del a[k:k + 2]
    preview(sys.argv[2], a, sc)
