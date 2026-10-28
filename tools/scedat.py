"""Names of the scenario data files DATA/SCEDAT1.CK / SCEDAT2.CK (regions, countries, cities, goods,
technologies, unit types, officers, consorts) and their header texts (scenario title + intro).

SETUP reads each file whole (fixed size 0x5141 / 0x444a, BIN/SETUP.BIN 0x80195bf4) and parses
fixed-size records: a name is a zero-terminated, space-padded field of CAP bytes (regions:
id + 15-byte name + 3 bytes, 0x80196c50). English replaces it in place (ASCII, NUL, spaces),
so the files keep their size and layout. Header: u16, title NUL, intro NUL, spaces up to 0x207
(the parser skips it; 0x80195df0).

tl/scedat.txt: "FILE:offset<TAB>English" (offset hex = start of the name field) and
"FILE:title" / "FILE:intro". Made from notes/glossary.txt by `scedat.py gen` (full English
when it fits CAP-1 characters, else the short column); title/intro lines are kept.

  scedat.py gen      -> tl/scedat.txt (keeps title/intro)
  scedat.py check    field fit report
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'dev'))
from sjscan import scan

TL = os.path.join(ROOT, 'tl', 'scedat.txt')
GLOSSARY = os.path.join(ROOT, 'notes', 'glossary.txt')
FILES = {'SCEDAT1': 'DATA/SCEDAT1.CK', 'SCEDAT2': 'DATA/SCEDAT2.CK'}
HEADER_END = 0x206                       # last byte of the header text area (0x207 = first region id)
CAP = {'regions/seas/islands': 15, 'countries': 19, 'cities': 21, 'goods': 11, 'technologies': 23,
       'unit types': 11, 'people': 17, 'consorts': 17}


def norm(s):
    return s.replace('−', '－')


def glossary(path=GLOSSARY):
    """{section: {jp: (english, short)}}"""
    out, sec = {}, None
    for ln in open(path, encoding='utf-8').read().splitlines():
        if ln.startswith('## '):
            sec = ln[3:].split(' (')[0]
            out.setdefault(sec, {})
            continue
        if not ln.strip() or ln.startswith('#'):
            continue
        f = [x for x in ln.split('\t') if not x.startswith('#')]
        out[sec].setdefault(norm(f[0]), (f[1], f[2] if len(f) > 2 else None))
    return out


def fields(d, gl):
    """[(offset, kind, jp)] of every glossary name in a SCEDAT file, kind = record section."""
    hits = []
    for o, s in scan(d, 1):
        if o <= HEADER_END:
            continue
        for k in range(len(s)):
            try:
                pre, u = s[:k].decode('cp932'), norm(s[k:].decode('cp932'))
            except UnicodeDecodeError:
                continue
            if any(ord(c) > 0xff60 or 0x3000 <= ord(c) < 0xff00 for c in pre):
                break                    # a longer Japanese string, not a field start
            kinds = [sec for sec in CAP if u in gl.get(sec, {})]
            if kinds:
                hits.append([o + k, kinds, u])
                break
    out = []
    for i, (o, kinds, u) in enumerate(hits):
        if len(kinds) > 1:              # same name in two sections: take the neighbours' section
            nxt = next((h[1][0] for h in hits[i + 1:] if len(h[1]) == 1), None)
            prv = next((h[1][0] for h in reversed(hits[:i]) if len(h[1]) == 1), None)
            kinds = [nxt] if nxt in kinds else [prv] if prv in kinds else kinds
        out.append((o, kinds[0], u))
    return out


def check_field(d, o, cap):
    """The clean field must be the name, NUL, spaces up to cap."""
    n = d.index(b'\0', o) - o
    pad = d[o + n + 1:o + cap]
    return n < cap and pad == b' ' * len(pad)


def gen(disc_files=None):
    gl = glossary()
    keep = [l for l in read_lines() if l.split('\t')[0].endswith((':title', ':intro'))]
    lines, errs = [], []
    for name, path in FILES.items():
        d = disc_files[name] if disc_files else open(os.path.join(ROOT, 'extract', path), 'rb').read()
        lines += [l for l in keep if l.startswith(name + ':')]
        kind = None
        for o, k, jp in fields(d, gl):
            if k != kind:
                lines.append('# %s %s (field %d bytes = %d chars)' % (name, k, CAP[k], CAP[k] - 1))
                kind = k
            if not check_field(d, o, CAP[k]):
                errs.append('%s:%05x %s: field is not name+NUL+spaces' % (name, o, k))
            en, short = gl[k][jp]
            if len(en) >= CAP[k] and short:
                en = short
            if len(en) >= CAP[k]:
                errs.append('%s:%05x %s: "%s" %d > %d' % (name, o, k, en, len(en), CAP[k] - 1))
            lines.append('%s:%05x\t%s' % (name, o, en))
    open(TL, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('%s: %d lines' % (TL, len(lines)))
    return errs


def read_lines():
    return open(TL, encoding='utf-8').read().splitlines() if os.path.exists(TL) else []


def load():
    """{file: {offset or 'title'/'intro': English}}"""
    out = {n: {} for n in FILES}
    for ln in read_lines():
        if not ln.strip() or ln.startswith('#'):
            continue
        key, _, en = ln.partition('\t')
        name, _, at = key.partition(':')
        out[name][at if at in ('title', 'intro') else int(at, 16)] = en
    return out


def apply(name, clean):
    """English SCEDAT file (same size) from the clean bytes."""
    d, errs = bytearray(clean), []
    tl = load()[name]
    for at, en in tl.items():
        if isinstance(at, str):
            continue
        n = d.index(b'\0', at) - at
        cap = n + 1
        while at + cap < len(d) and d[at + cap] == 0x20:
            cap += 1
        b = en.encode('ascii')
        if len(b) >= cap:
            errs.append('%s:%05x "%s" %d > %d' % (name, at, en, len(b), cap - 1))
            continue
        d[at:at + cap] = b + b'\0' + b' ' * (cap - len(b) - 1)
    if 'title' in tl or 'intro' in tl:
        title, rest = bytes(d[2:]).split(b'\0', 1)
        intro = rest.split(b'\0', 1)[0]
        b = b''.join(x + b'\0' for x in (tl.get('title', '').encode('ascii') or title,
                                          tl.get('intro', '').encode('ascii') or intro))
        if 2 + len(b) > HEADER_END + 1:
            errs.append('%s header: %d bytes > %d' % (name, len(b), HEADER_END - 1))
        else:
            d[2:HEADER_END + 1] = b + b' ' * (HEADER_END + 1 - 2 - len(b))
    if errs:
        sys.exit('\n'.join(errs))
    assert len(d) == len(clean)
    return bytes(d)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'gen':
        errs = gen()
        print('\n'.join(errs) or 'all names fit')
    elif cmd == 'check':
        for name, path in FILES.items():
            apply(name, open(os.path.join(ROOT, 'extract', path), 'rb').read())
        print('ok')
