"""Name pools of DATA/NEWNAME.CK: names for newly founded countries and new towns.

The file is a list of 0x800-byte sections (read one at a time, EXE 0x800a4290): u16 count, then
zero-padded name slots — 19 bytes in the country pools (sections before 0x15000), 21 bytes in
the town pools. English replaces each name in its slot (ASCII + zeros), so the file keeps its
size and layout.

tl/newname.txt: "offset<TAB>English" (offset hex = slot start), made from notes/newname.txt
(JP<TAB>English, one line per distinct name) by `newname.py gen`.

  newname.py gen | check
"""
import os, struct, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'newname.txt')
NOTES = os.path.join(ROOT, 'notes', 'newname.txt')
NAME = 'DATA/NEWNAME.CK'
TOWNS = 0x15000                          # first town pool


def slots(d):
    """[(section, offset, slot size)]"""
    out, o, sec = [], 0, 0
    while o < len(d):
        n, size = struct.unpack_from('<H', d, o)[0], 19 if o < TOWNS else 21
        out += [(sec, o + 2 + i * size, size) for i in range(n)]
        o = (o + 2 + n * size + 0x7ff) // 0x800 * 0x800
        sec += 1
    return out


def gen():
    d = open(os.path.join(ROOT, 'extract', NAME), 'rb').read()
    en = {}
    for ln in open(NOTES, encoding='utf-8').read().splitlines():
        f = ln.split('\t')
        if len(f) >= 2 and not ln.startswith('#'):
            en[f[0]] = f[1]
    lines, errs, cur = [], [], None
    for sec, o, size in slots(d):
        if sec != cur:
            lines.append('# %s pool %d (%d chars max)' % ('country' if o < TOWNS else 'town', sec, size - 1))
            cur = sec
        jp = d[o:o + size].split(b'\0')[0].decode('cp932')
        if jp not in en:
            errs.append('%05x: no English' % o)
            continue
        lines.append('%05x\t%s' % (o, en[jp]))
    open(TL, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('%s: %d lines' % (TL, len(lines)))
    return errs


def apply(clean):
    d, errs = bytearray(clean), []
    size = {o: n for _, o, n in slots(clean)}
    for ln in open(TL, encoding='utf-8').read().splitlines():
        if not ln.strip() or ln.startswith('#'):
            continue
        at, _, en = ln.partition('\t')
        o = int(at, 16)
        b = en.encode('ascii')
        if o not in size or len(b) >= size[o]:
            errs.append('%s "%s": %s' % (at, en, 'not a slot' if o not in size else '%d > %d' % (len(b), size[o] - 1)))
            continue
        d[o:o + size[o]] = b.ljust(size[o], b'\0')
    if errs:
        sys.exit('\n'.join(errs))
    return bytes(d)


if __name__ == '__main__':
    if sys.argv[1:] == ['gen']:
        print('\n'.join(gen()) or 'all names have English')
    apply(open(os.path.join(ROOT, 'extract', NAME), 'rb').read())
    print('ok')
