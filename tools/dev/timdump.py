"""Dump the TIM images packed in a GRP*.CK file (back-to-back TIMs, CLUT 4/8 bpp) to PNGs.

usage: .venv/bin/python tools/dev/timdump.py extract/GRP/BUTTON.CK [outdir=build/gfx] [--first-clut]
Prints offset, bpp, size, clut count per TIM; stops at the first non-TIM header.
"""
import os, struct, sys
from PIL import Image


def c15(v):
    r, g, b = v & 31, (v >> 5) & 31, (v >> 10) & 31
    return (r << 3, g << 3, b << 3, 0 if v == 0 else 255)


def parse(d, o):
    magic, flag = struct.unpack_from('<II', d, o)
    if magic != 0x10 or (flag & ~0xb) not in (0,) or (flag & 7) > 3:
        return None
    bpp = flag & 3
    p = o + 8
    pal = []
    if flag & 8:
        ln, x, y, w, h = struct.unpack_from('<IHHHH', d, p)
        cl = struct.unpack_from('<%dH' % (w * h), d, p + 12)
        pal = [cl[i:i + w] for i in range(0, w * h, w)]
        p += ln
    ln, x, y, w, h = struct.unpack_from('<IHHHH', d, p)
    pix = d[p + 12:p + ln]
    return dict(off=o, end=p + ln, bpp=bpp, w=w, h=h, x=x, y=y, pal=pal, pix=pix)


def to_img(t, clut=0):
    w16, h = t['w'], t['h']
    if t['bpp'] == 0:
        W = w16 * 4
        idx = [(b >> s) & 15 for b in t['pix'] for s in (0, 4)]
    elif t['bpp'] == 1:
        W = w16 * 2
        idx = list(t['pix'])
    else:
        W = w16
        v = struct.unpack('<%dH' % (len(t['pix']) // 2), t['pix'])
        im = Image.new('RGBA', (W, h)); im.putdata([c15(x) for x in v[:W * h]]); return im
    pal = [c15(v) for v in t['pal'][clut]] if t['pal'] else [(i * 17,) * 3 + (255,) for i in range(16)]
    im = Image.new('RGBA', (W, h)); im.putdata([pal[i] if i < len(pal) else (255, 0, 255, 255) for i in idx[:W * h]])
    return im


def main():
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('-') else 'build/gfx'
    d = open(src, 'rb').read()
    name = os.path.splitext(os.path.basename(src))[0]
    os.makedirs(out, exist_ok=True)
    o, n = 0, 0
    while o + 8 <= len(d):
        t = parse(d, o)
        if not t or t['end'] > len(d) or not t['w'] or not t['h']:
            q = o + 4                                     # skip padding / junk to the next plausible TIM
            while q + 8 <= len(d) and not (d[q:q + 4] == b'\x10\0\0\0' and d[q + 4] in (8, 9, 2, 3)
                                           and d[q + 5:q + 8] == b'\0\0\0'):
                q += 4
            if q + 8 > len(d):
                break
            print('    skip 0x%x-0x%x' % (o, q))
            o = q
            continue
        print('%3d 0x%06x bpp%d %dx%d vram(%d,%d) cluts %d' % (n, o, (4, 8, 16, 24)[t['bpp']], t['w'], t['h'],
                                                            t['x'], t['y'], len(t['pal'])))
        to_img(t).save(os.path.join(out, '%s_%03d.png' % (name, n)))
        o, n = (t['end'] + 3) & ~3, n + 1


if __name__ == '__main__':
    main()
