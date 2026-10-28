"""Strings of the program and the overlays (menu labels, help lines, system messages ...).

tl/progstr/*.txt: "key<TAB>English" lines, key = <IMAGE>:<vaddr hex> with IMAGE = PROG
(SLPM_861.75) or an overlay name (USER, TURN, ... = BIN/USER.BIN ...); \\n = newline; \\t = label tab (vwf.TAB);
ASCII text ({fw} prefix = written full-width); English "-" = keep the Japanese (a literal minus sign alone: "\\-") (debug output never shown); '#' starts a
comment line. A string without English takes the English of another string with the
same Japanese (translation memory).

English that fits the string's slot (its bytes plus the compiler's zero padding to the
next 4-byte boundary) is written in place. Longer English is moved and every reference is
repointed:
  - 32-bit data words equal to the address (pointer tables) in the same image (and, for a
    program string, in the overlays),
  - lui/addiu (or load/store) pairs in the code that build the address, when that lui
    has no other user (else the pair cannot be changed alone: shorten the English).
Where moved strings go (Pool): the slack of the slots (bytes after the English's NUL of
strings written in place, whole slots of moved strings; referenced strings only, never
array elements), then the tail of an overlay (the file grows up to OVL_LIMIT: the RAM after
every overlay up to the end of the largest one is not used while it is loaded) and, for the
program, the resident free RAM (RESIDENT). Program strings only go to program space
(overlays come and go); overlay strings to their own overlay or the program.
Blockers (the English must fit the slot): an overlay address referenced from the program
(every overlay loads at OVL_BASE: ambiguous), a lui with other users; gp-relative
references only reach gp +- 32 KB (program pool space there). A string with no reference
found must fit its slot. printf conversions must match.

  progstr.py export            -> script/progstr.json (every candidate, with flags)
  progstr.py show [KEY KEY] [--todo]   strings to translate between two keys
  progstr.py stats | check
"""
import collections, json, os, re, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import fmtlimit, iso

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'progstr')
SCRIPT = os.path.join(ROOT, 'script', 'progstr.json')
FMT = re.compile(rb'%[-+ #0]*\d*(?:\.\d+)?[hl]?[diouxXcsfeEgGp%]')


def convs(b):
    """printf conversions; a %s, %d or %u may change its pad width (column layout: fmtlimit measures it)"""
    return [re.sub(rb'\d+([sdu])$', rb'\1', c) for c in FMT.findall(b)]
PROG_BASE, PROG_FOFF = 0x80010000, 0x800
OVL_BASE, OVL_HEAD = 0x80190000, 0x40  # every overlay loads here, header "MWo1" included
OVL_LIMIT = 0x801dfff0                 # end of the free gap after the largest overlay (USER), notes/emu.md
OVERLAYS = ('SETUP', 'USER', 'TURN', 'WAR', 'DINNER', 'END', 'MOVIE')
GP = 0x800f8eec
RAM_LO, RAM_HI = 0x80010000, 0x80200000
# program areas that are not strings: half-width font + code table (rewritten by vwf.py, block
# tables after it)
PROG_SKIP = [(0x800d9c0c, 0x800da50c)]
RECORDS = []
SDATA = (GP - 0x8000, GP + 0x8000)     # small strings near gp: lone characters there are labels
KEY = re.compile(r'(PROG|%s):([0-9a-f]{8})' % '|'.join(OVERLAYS))
JR_RA = 0x03e00008
CODE_START = 0x80010000
NO_STRINGS = ()
STALE = {}
# pointer tables in the program that belong to one overlay: the memory-card messages of SETUP
# (the memory-card code is in SETUP, driven through function pointers in the program)
OVL_TABLES = {'SETUP': [(0x800f1304, 0x800f1388)]}
RESIDENT = []                          # [(va, bytes)] of free program RAM loaded with the game (none yet)
# formats whose arguments are no longer printed (sprintf ignores extra arguments)
DROP_ARGS = set()


def ovl_name(tag):
    return 'BIN/%s.BIN' % tag


class Image:
    """One loaded file: data[foff + (va - base)] for va in [base, end)."""

    def __init__(self, tag, data, base, foff):
        self.tag, self.data, self.base, self.foff = tag, data, base, foff
        self.end = base + min(len(data) - foff, STALE.get(tag, len(data)))
        n = (self.end - base) // 4
        ws = struct.unpack_from('<%dI' % n, data, foff)
        last = max(i for i, w in enumerate(ws) if w == JR_RA)
        self.code_end = base + 4 * last + 8          # code: [code_start, code_end); data around it
        if tag != 'PROG':                            # overlay header: text size (stale code may follow the data)
            self.code_end = base + OVL_HEAD + struct.unpack_from('<I', data, 12)[0]
        self.code_start = CODE_START if tag == 'PROG' else base + OVL_HEAD
        self.data_ranges = [(base, self.code_start), (self.code_end, self.end)]
        self.skip = sorted(PROG_SKIP + RECORDS) if tag == 'PROG' else []

    def f(self, va):
        assert self.base <= va < self.end, '%s %x' % (self.tag, va)
        return va - self.base + self.foff

    def w(self, va):
        return struct.unpack_from('<I', self.data, self.f(va))[0]

    def put(self, va, b):
        o = self.f(va)
        self.data[o:o + len(b)] = b


def images(files):
    """[Image] over {tag: bytearray} (PROG and the overlays)."""
    out = []
    for tag, b in files.items():
        out.append(Image(tag, b, PROG_BASE, PROG_FOFF) if tag == 'PROG' else Image(tag, b, OVL_BASE, 0))
    return out


def _s16(x):
    return x - 0x10000 if x & 0x8000 else x


def _writes(w):
    """Register written by instruction w (None if none / unknown)."""
    op = w >> 26
    if op in (0, 0x10, 0x12):
        return (w >> 11) & 31 if op == 0 else None
    if op in (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F) or 0x20 <= op <= 0x26:
        return (w >> 16) & 31
    if op == 0x03:
        return 31
    return None


def _is_jump(w):
    op = w >> 26
    return op in (2, 3) or (op == 0 and (w & 0x3F) in (8, 9))


def code_pairs(img):
    """({target: [(lui va, user va)]}, {lui va: number of users}, {target: [gp user va]})
    for the image's code. A lui register is followed until it is overwritten or a jump's
    delay slot ends; any other read of it counts as a user too (so such a lui is never
    changed)."""
    pairs, users, gps = collections.defaultdict(list), collections.Counter(), collections.defaultdict(list)
    lui, end_after = {}, None
    for va in range(img.code_start, img.code_end, 4):
        w = img.w(va)
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if rs == 28 and (op == 0x09 or 0x20 <= op <= 0x3F):
            gps[(GP + _s16(imm)) & 0xFFFFFFFF].append(va)
        if op == 0x0F:
            lui[rt] = (imm << 16, va)
        else:
            if rs in lui and (op in (0x09, 0x0D) or 0x20 <= op <= 0x3F):
                t = (lui[rs][0] + (imm if op == 0x0D else _s16(imm))) & 0xFFFFFFFF
                pairs[t].append((lui[rs][1], va))
                users[lui[rs][1]] += 1
                if 0x28 <= op <= 0x3F and rt in lui and rt != rs:     # stores the lui value
                    users[lui[rt][1]] += 2
            else:
                for r in {rs, rt}:
                    if r in lui:
                        users[lui[r][1]] += 2
            wr = _writes(w)
            if wr in lui:
                del lui[wr]
        if end_after is not None and va >= end_after:
            lui.clear()
            end_after = None
        if _is_jump(w):
            end_after = va + 4
    return pairs, users, gps


def data_words(img):
    """{value: [vaddr]} of the aligned words of the image's data pointing into RAM."""
    out = collections.defaultdict(list)
    for lo, hi in img.data_ranges:
        for va in range(lo, hi - 3, 4):
            v = img.w(va)
            if RAM_LO <= v < RAM_HI:
                out[v].append(va)
    return out


class Refs:
    """Every reference to a string of each image, by image tag."""

    def __init__(self, imgs):
        self.imgs = {i.tag: i for i in imgs}
        self.code = {i.tag: code_pairs(i) for i in imgs}
        self.words = {i.tag: data_words(i) for i in imgs}

    def of(self, tag, va):
        """(code pairs [(img tag, lui, user)], data words [(img tag, at)], blockers [text])."""
        code, data, block = [], [], []
        if tag == 'PROG':
            srcs = list(self.imgs)
        else:
            srcs = [tag]
            own = [at for at in self.words['PROG'].get(va, [])
                   if any(lo <= at < hi for lo, hi in OVL_TABLES.get(tag, []))]
            data += [('PROG', at) for at in own]
            if self.code['PROG'][0].get(va) or len(self.words['PROG'].get(va, [])) > len(own):
                block.append('referenced from the program (ambiguous overlay address)')
        for s in srcs:
            for l, u in self.code[s][0].get(va, []):
                code.append((s, l, u))
                if self.code[s][1][l] != 1:
                    block.append('lui %s:%x has other users' % (s, l))
            for at in self.words[s].get(va, []):
                data.append((s, at))
            if self.code[s][2].get(va):
                block.append('gp-relative reference %s:%x' % (s, self.code[s][2][va][0]))
        return code, data, block

    def gp_users(self, tag, va):
        """[(img tag, instruction va)] of the gp-relative references to va."""
        srcs = list(self.imgs) if tag == 'PROG' else [tag]
        return [(s, u) for s in srcs for u in self.code[s][2].get(va, [])]


def slot(img, va):
    """(Japanese bytes, room) of the string at va: room = bytes up to the next 4-byte
    boundary after its NUL (the compiler's padding), never past the next non-zero byte,
    minus one for the NUL."""
    b = img.data
    o = img.f(va)
    assert b[o - 1] == 0 or va % 4 == 0, '%s:%x is not the start of a string' % (img.tag, va)
    e = b.index(b'\0', o)
    k = e
    while k < len(b) and b[k] == 0:
        k += 1
    end = min(k, (va + (e - o) + 1 + 3) // 4 * 4 - va + o)
    return bytes(b[o:e]), end - o - 1


def parse_key(k):
    m = KEY.fullmatch(k)
    return m.group(1), int(m.group(2), 16)


def load_tl(d=TL):
    """{key: English} of every tl/progstr file ('-' entries included)."""
    out = {}
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith('.txt'):
            continue
        path = os.path.join(d, fn)
        for n, line in enumerate(open(path, encoding='utf-8').read().splitlines(), 1):
            if line.strip() and not line.startswith('#'):
                k, tab, v = line.partition('\t')
                if not (tab and KEY.fullmatch(k)):
                    raise ValueError('%s line %d: expected key<TAB>English: %r' % (fn, n, line))
                if k in out:
                    raise ValueError('%s line %d: %s given twice' % (fn, n, k))
                out[k] = v.replace('\\n', '\n').replace('\\t', '\t')
    return out


def load_tl_file(path):
    """Keys of one tl file."""
    return [l.partition('\t')[0] for l in open(path, encoding='utf-8').read().splitlines()
            if l.strip() and not l.startswith('#')]


def resolved(tl, rows):
    """{key: English} to write: explicit English, else translation memory by Japanese;
    '-' entries dropped. rows = export rows (key, jp, ui)."""
    jp = {r['key']: r['jp'] for r in rows}
    mem = {}
    for k, v in tl.items():
        if v != '-' and k in jp:
            mem.setdefault(jp[k], v)
    out = {k: v for k, v in tl.items() if v != '-'}
    for r in rows:
        if r['ui'] and r['key'] not in tl and r['jp'] in mem:
            out[r['key']] = mem[r['jp']]
    return out


def fmt_error(key, en, jp, sites):
    """Error text if the English format can overflow a sprintf target of known size
    (fmtlimit.py): its longest output must fit, or at least not exceed the Japanese's."""
    name = fmtlimit.SHORT_S.get(key, fmtlimit.NAME)
    n, j = fmtlimit.expand(en, name), fmtlimit.expand(jp, name)
    for site, size in sites:
        if size is not None and n > max(size - 1, j):
            return 'progstr %s: sprintf at %s writes up to %d bytes into a %d-byte buffer (Japanese %d)' % (key, site, n, size, j)
    return None


def fmt_unknown(tl):
    """[(key, en bytes, jp bytes, sites)] of formats longer than the Japanese whose target
    size is unknown (check in the emulator, or trace the buffer)."""
    rs = {r['key']: r for r in rows()}
    imgs = images({t: bytearray(b) for t, b in clean_files().items() if t not in NO_STRINGS})
    out = []
    for key, sites in sorted(fmtlimit.limits(imgs).items()):
        en = tl.get(key)
        if en and en != '-' and key in rs and any(s is None for _, s in sites):
            n, j = fmtlimit.expand(encode(en)), fmtlimit.expand(rs[key]['jp'].encode('cp932'))
            if n > j:
                out.append((key, n, j, [x for x, s in sites if s is None]))
    return out


FW = '{fw}'    # English prefix: write the text as full-width Shift-JIS (memory card titles: the BIOS shows no ASCII)
SJ = '{sj}'    # prefix: the rest as Shift-JIS symbols (in-game font: full-width letters draw as blank boxes)


def encode(en):
    if en.startswith(SJ):
        return en[len(SJ):].encode('cp932')
    if en.startswith(FW):
        en.encode('ascii')
        parts = re.split(r'(%[-\d]*[sd])', en[len(FW):])
        return b''.join(t.encode('ascii') if i % 2 else
                        ''.join('\u3000' if c == ' ' else chr(ord(c) + 0xFEE0) for c in t).encode('cp932')
                        for i, t in enumerate(parts))
    return en.replace('\\-', '-').encode('ascii')


class Pool:
    """Free space for moved strings: [va, n] ranges per image (slot slack, freed slots,
    RESIDENT for the program) and the growable tail of each overlay."""

    def __init__(self, imgs, refs, resident=()):
        self.free = collections.defaultdict(list)
        self.free['PROG'] += [[va, n] for va, n in resident]
        self.tail = {i.tag: (i.end + 3) & ~3 for i in imgs if i.tag != 'PROG'}
        self.limit = {t: tail_limit(refs, e) for t, e in self.tail.items()}
        self.grown = {}

    def add(self, tag, va, n):
        if n > 0:
            self.free[tag].append([va, n])

    def merge(self):
        for tag, fr in self.free.items():
            out = []
            for va, n in sorted(fr):
                if out and out[-1][0] + out[-1][1] == va:
                    out[-1][1] += n
                else:
                    out.append([va, n])
            self.free[tag] = out

    def take(self, tag, n, near_gp=False):
        """(space tag, va) of n free bytes for a string of image tag, or None."""
        ok = (lambda va: GP - 0x8000 <= va and va + n <= GP + 0x8000) if near_gp else (lambda va: True)
        for space in [tag, 'PROG'] if tag != 'PROG' else ['PROG']:
            for f in self.free[space]:
                if f[1] >= n and ok(f[0]):
                    va = f[0]
                    f[0] += n; f[1] -= n
                    return space, va
            if space != 'PROG' and not near_gp and self.tail[space] + n <= self.limit[space]:
                va = self.tail[space]
                self.tail[space] += n
                self.grown[space] = self.tail[space]
                return space, va
        return None

    def room(self):
        return {t: sum(n for _, n in fr) for t, fr in self.free.items()}


def tail_limit(refs, end):
    """End of the RAM an overlay ending at `end` may grow into: OVL_LIMIT or the first address
    after `end` that the program's code uses (it belongs to another overlay but the program may
    touch it while this one is loaded)."""
    ts = [t for t in refs.code['PROG'][0] if end <= t < OVL_LIMIT]
    return min(ts + [OVL_LIMIT]) & ~3


def apply(files, clean, resident=(), tl=None):
    """Write tl/progstr into files ({tag: bytearray}; overlays may grow); clean = {tag: bytes};
    resident = [(va, bytes)] of free program RAM. Returns (strings, moved, pool)."""
    cimgs = images({t: bytearray(b) for t, b in clean.items()})
    rs = {r['key']: r for r in survey(cimgs)}
    tl = resolved(load_tl() if tl is None else tl, list(rs.values()))
    refs = Refs(cimgs)
    pool = Pool(cimgs, refs, resident)
    if not tl:
        return 0, 0, pool
    imgs = {i.tag: i for i in images(files)}
    lims = fmtlimit.limits([i for i in cimgs if i.tag not in NO_STRINGS]) if fmtlimit.SPRINTF else {}
    ctag = {i.tag: i for i in cimgs}
    errors, moving = [], []
    for key, en in sorted(tl.items()):
        tag, va = parse_key(key)
        jp, room = slot(ctag[tag], va)
        own = key in rs and rs[key]['kind'] == 'ui'       # referenced: its slot slack is ours
        try:
            b = encode(en)
        except UnicodeEncodeError:
            errors.append('progstr %s: not ASCII: %r' % (key, en))
            continue
        if convs(b) != convs(jp) and not (key in DROP_ARGS and not FMT.findall(b)):
            errors.append('progstr %s: conversions %s, Japanese has %s' % (key, FMT.findall(b), FMT.findall(jp)))
            continue
        err = fmt_error(key, b, jp, lims.get(key, []))
        if err:
            errors.append(err)
            continue
        if len(b) <= room:
            imgs[tag].put(va, b + bytes(room + 1 - len(b)))
            if own:
                pool.add(tag, va + len(b) + 1, room - len(b))
            continue
        code, data, block = refs.of(tag, va)
        if not code and not data and not block:
            errors.append('progstr %s: %d bytes, room %d and no reference to repoint' % (key, len(b), room))
            continue
        if [x for x in block if not x.startswith('gp-relative')]:
            errors.append('progstr %s: %d bytes, room %d; %s' % (key, len(b), room, '; '.join(block)))
            continue
        imgs[tag].put(va, bytes(room + 1))
        pool.add(tag, va, room + 1)
        moving.append((key, tag, b, code, data, refs.gp_users(tag, va)))
    pool.merge()
    placed = {}                                  # (space, English) -> va; gp-reachable ones first, longest first
    for key, tag, b, code, data, gps in sorted(moving, key=lambda m: (not m[5], -len(m[2]))):
        new = next((va for (sp, e), va in placed.items() if e == b and (sp == tag or sp == 'PROG')
                    and (not gps or GP - 0x8000 <= va < GP + 0x8000 - len(b))), None)
        if new is None:
            got = pool.take(tag, len(b) + 1, bool(gps))
            if got is None:
                errors.append('progstr %s: %d bytes, no free space left%s (space)' % (key, len(b), ' near gp' if gps else ''))
                continue
            space, new = got
            if space != 'PROG' and new + len(b) + 1 > OVL_BASE + len(files[space]):     # overlay tail
                files[space] += bytes(new + len(b) + 1 - OVL_BASE - len(files[space]))
                imgs[space] = images({space: files[space]})[0]
            imgs[space].put(new, b + b'\0')
            placed[(space, b)] = new
        _repoint(imgs, ctag, key, new, code, data)
        for s, u in gps:
            uw = ctag[s].w(u)
            assert imgs[s].w(u) == uw, 'progstr %s: gp reference at %s:%x already patched' % (key, s, u)
            imgs[s].put(u, struct.pack('<I', (uw & 0xFFFF0000) | ((new - GP) & 0xFFFF)))
    for tag in pool.grown:
        files[tag] += bytes(-len(files[tag]) % 4)
        ovl_resize(files[tag])
    if errors:
        raise ValueError('\n'.join(errors))
    return len(tl), len(moving), pool


def ovl_resize(b):
    """Grow the overlay header's data size to the file (header: "MWo1", id, load address,
    text size, data size; file = 0x40 + text + data)."""
    t = struct.unpack_from('<I', b, 12)[0]
    struct.pack_into('<I', b, 16, len(b) - OVL_HEAD - t)


def _repoint(imgs, ctag, key, new, code, data):
    """Point the data words and lui/user code pairs of a string at new."""
    for s, at in data:
        imgs[s].put(at, struct.pack('<I', new))
    for s, l, u in code:
        lw, uw = ctag[s].w(l), ctag[s].w(u)
        assert (imgs[s].w(u) == uw), 'progstr %s: code at %s:%x already patched' % (key, s, u)
        hi = (new + 0x8000) >> 16 & 0xFFFF
        lo = (new - (hi << 16)) & 0xFFFF
        if uw >> 26 == 0x0D:                                  # ori: unsigned low half
            hi, lo = new >> 16, new & 0xFFFF
        imgs[s].put(l, struct.pack('<I', (lw & 0xFFFF0000) | hi))
        imgs[s].put(u, struct.pack('<I', (uw & 0xFFFF0000) | lo))


# ---------------------------------------------------------------- survey

JP = re.compile('[぀-ヿ一-鿿！-～　-〿｡-ﾟ]')
JP_FULL = re.compile('[぀-ヿ一-鿿！-～　-〿]')


def _is_text(b):
    i, jp = 0, 0
    while i < len(b):
        c = b[i]
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            if i + 1 >= len(b) or not (0x40 <= b[i + 1] <= 0xFC and b[i + 1] != 0x7F):
                return False
            jp += 1
            i += 2
            continue
        if 0xA1 <= c <= 0xDF:
            jp += 1
        elif not (0x20 <= c < 0x7F or c in (0x0A, 0x1B, 0x09)):
            return False
        i += 1
    return jp > 0


GAIJI_STRINGS = {}
def candidates(img):
    """[(vaddr, bytes)] of the Shift-JIS strings in the image's data (mostly Japanese)."""
    out, b = [], img.data
    for va in GAIJI_STRINGS.get(img.tag, ()):
        out.append((va, bytes(b[img.f(va):b.index(b'\0', img.f(va))])))
    for d_lo, d_hi in img.data_ranges:
        lo = d_lo
        for s_lo, s_hi in [x for x in img.skip if d_lo <= x[0] < d_hi] + [(d_hi, d_hi)]:
            if s_lo > lo:
                out += _scan(img, img.f(lo), img.f(s_lo - 1) + 1)
            lo = max(lo, s_hi)
    return sorted(out)


def _scan(img, o, hi):
    out, b = [], img.data
    while o < hi:
        if b[o] == 0:
            o += 1
            continue
        e = b.find(b'\0', o)
        if e < 0:
            break
        # a string starts after a NUL or, right after a pointer word, at a 4-aligned address
        starts = [o] if b[o - 1] == 0 else []
        starts += [p for p in range(o + 1, e) if (p - img.foff + img.base) % 4 == 0]
        for p in starts:
            s = bytes(b[p:e])
            if _is_text(s):
                t = s.decode('cp932', 'replace')
                fw = len(JP.findall(t))
                if '�' not in t and (fw >= 2 or JP_FULL.search(t)) and (fw * 2 >= len(t.strip(" ")) * 0.5 or FMT.search(s)):
                    out.append((p - img.foff + img.base, s))
                break
        o = e + 1
    return out


_FMT_S = re.compile(r'%[-+ #0]*\d*[hl]?[dsxuc]')
# likely debug output (never shown): workers still see these, flagged
# (S5: padded button labels start with spaces and multi-line texts end with \n: not debug signs here)
DEBUG = re.compile(r'Algo|－.*処理|処理$|\*\*\*|===|<<|>>|\$\$|->|Phase|Buffer|Error|[:：]%d')


def _junk(t):
    """One kanji + ASCII bytes: an instruction or a number that looks like text."""
    rest = _FMT_S.sub('', t)
    # labels like ' 金', '金:%d', '残  %3d' are text: only stray letters make it an instruction
    return (len(JP.findall(t)) == 1 and len(rest) > 1 and all(c.isascii() for c in rest if not JP.match(c))
            and any(c.isalpha() and c.isascii() for c in rest))


GRID = re.compile('あいうえお|アイウエオ')       # name-entry kana grids (names phase)


def _array(t, va, tag):
    """A string without a reference that looks like real text (an array element);
    lone kanji outside the program are data words."""
    return va % 4 == 0 and (len(t) > 1 or SDATA[0] <= va < SDATA[1]) and all(JP_FULL.match(c) or c in ' \n%d' for c in t)


def survey(imgs):
    """[row] of every candidate string: key, tag, va, room, code, data, block, jp (text),
    ui (to translate), debug (flag), kind (ui / noref / junk)."""
    refs = Refs(imgs)
    rows = []
    for img in imgs:
        if img.tag in NO_STRINGS:
            continue
        for va, s in candidates(img):
            jp, room = slot(img, va)
            code, data, block = refs.of(img.tag, va)
            t = jp.decode('cp932')
            gp = any('gp-relative' in x for x in block)
            if code or data or gp:
                kind = 'junk' if _junk(t) else 'ui'
            elif _array(t, va, img.tag):
                kind = 'grid' if GRID.search(t) else 'array'
                block = block + ['no reference (element of a fixed-size array?)']
            else:
                kind = 'noref'
            rows.append({'key': '%s:%08x' % (img.tag, va), 'tag': img.tag, 'va': va, 'room': room,
                         'code': len(code), 'data': len(data), 'block': block, 'jp': t,
                         'ui': kind in ('ui', 'array'), 'debug': kind == 'ui' and bool(DEBUG.search(t)),
                         'kind': kind})
    return rows


def disc_files(disc, recs=None):
    """{tag: bytes} of the program and the overlays from the disc."""
    recs = recs or disc.records()
    out = {'PROG': disc.file(iso.EXE_NAME, recs)}
    for t in OVERLAYS:
        out[t] = disc.file(ovl_name(t), recs)
    return out


def clean_files():
    import setup
    return disc_files(iso.Disc(setup.CLEAN))


def export():
    rows = survey(images({t: bytearray(b) for t, b in clean_files().items()}))
    os.makedirs(os.path.dirname(SCRIPT), exist_ok=True)
    json.dump(rows, open(SCRIPT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    return rows


def rows():
    if not os.path.exists(SCRIPT):
        export()
    return json.load(open(SCRIPT, encoding='utf-8'))


def _order(key):
    tag, va = parse_key(key)
    return (('PROG',) + OVERLAYS).index(tag), va


def show(lo=None, hi=None, todo=False):
    """Print the strings to translate in [lo, hi] (keys), address order:
    key  room  M (movable if the English is longer) / F (fixed: must fit room)  D (debug?)  Japanese  => English"""
    tl = load_tl()
    rs = [r for r in rows() if r['ui']]
    res = resolved(tl, rs)
    for r in rs:
        if lo and _order(r['key']) < _order(lo) or hi and _order(r['key']) > _order(hi):
            continue
        en = tl.get(r['key'])
        if todo and en is not None:
            continue
        mark = '' if en is not None else '[tm] ' + res[r['key']] if r['key'] in res else ''
        print('%s\t%d\t%s%s\t%s%s' % (r['key'], r['room'], 'F' if r['block'] else 'M', ' D' if r['debug'] else '',
                                    r['jp'].replace('\n', '\\n'), '\t=> ' + (en.replace('\n', '\\n') if en is not None else mark) if en is not None or mark else ''))


def stats():
    tl = load_tl()
    rs = rows()
    ui = [r for r in rs if r['ui']]
    res = resolved(tl, ui)
    done = sum(r['key'] in tl or r['key'] in res for r in ui)
    print('candidates %d (%s)' % (len(rs), ', '.join('%s %d' % kv for kv in sorted(collections.Counter(r['kind'] for r in rs).items()))))
    for tag in ('PROG',) + OVERLAYS:
        t = [r for r in ui if r['tag'] == tag]
        print('  %-8s ui %4d  fixed %4d  chars %5d' % (tag, len(t), sum(bool(r['block']) for r in t), sum(len(r['jp']) for r in t)))
    print('ui strings %d: done %d (%d explicit, %d kept Japanese "-"), todo %d; debug-flagged %d'
          % (len(ui), done, sum(r['key'] in tl and tl[r['key']] != '-' for r in ui), sum(tl.get(r['key']) == '-' for r in ui),
             len(ui) - done, sum(r['debug'] for r in ui)))
    keys = {r['key'] for r in ui}
    stray = [k for k in tl if k not in keys]
    if stray:
        print('keys not in the ui list:', ' '.join(stray[:20]))


_W = None


def px(t, jp=False):
    """Pixel width of the widest line: English with the VWF font (tools/pixfont.py),
    Japanese 16 per full-width and 8 per half-width character (jp: ASCII too, the old 8x16 font)."""
    global _W
    if t.startswith(FW):
        return 16 * len(t) - 16 * len(FW)
    if _W is None:
        import pixfont
        _W = {c: g[1] for c, g in pixfont.glyphs().items()}
    return max(sum((8 if jp else _W.get(c, 8)) if c.isascii() else 8 if '\uff61' <= c <= '\uff9f' else 16 for c in line)
               for line in t.split('\n'))


def wide(tl=None):
    """[(key, en px, jp px, en, jp)] of English wider than its Japanese (labels may be cut)."""
    tl = load_tl() if tl is None else tl
    out = []
    for r in rows():
        en = tl.get(r['key'])
        if r['ui'] and en and en != '-' and px(en) > px(r['jp'], True):
            out.append((r['key'], px(en), px(r['jp'], True), en, r['jp']))
    return out


def check(only=None):
    """Dry-run apply on the clean files; prints errors (only: report just these keys). True if clean."""
    # a partial set has little slot slack yet: free space is only judged over the whole set (build)
    keep = lambda line: only is None or any(k in line for k in only) and not line.endswith('(space)')
    clean = clean_files()
    files = {t: bytearray(b) for t, b in clean.items()}
    try:
        n, moved, pool = apply(files, clean, RESIDENT)
        print('progstr: %d strings OK (%d moved); free left %s; overlays grown %s' % (
            n, moved, pool.room(), {t: '+%d' % (e - OVL_BASE - len(clean[t])) for t, e in pool.grown.items()}))
    except ValueError as e:
        errs = [x for x in str(e).split('\n') if keep(x)]
        if only is None or errs:
            print('\n'.join(errs))
            return False
        print('progstr: your strings OK')
    u = [x for x in fmt_unknown(resolved(load_tl(), [r for r in rows() if r['ui']])) if keep(x[0])]
    if u:
        print('%d formats longer than the Japanese with an unknown sprintf target (check):' % len(u))
        for k, n, j, sites in u:
            print('  %s %d > %d bytes  sprintf at %s' % (k, n, j, ' '.join(sites)))
    w = [x for x in wide() if keep(x[0])]
    if w:
        print('%d strings wider than the Japanese (may be cut or wrapped; shorten labels where possible):' % len(w))
        for k, a, b, en, jp in w:
            print('  %s %d > %d px  %s  (%s)' % (k, a, b, en.replace('\n', '\\n'), jp.replace('\n', '\\n')))
    return True


if __name__ == '__main__':
    a = sys.argv[1:]
    if a == ['export']:
        print(len(export()), 'strings ->', SCRIPT)
    elif a and a[0] == 'show':
        todo = '--todo' in a
        a = [x for x in a[1:] if x != '--todo']
        show(a[0] if a else None, a[1] if len(a) > 1 else None, todo)
    elif a == ['stats']:
        stats()
    elif a and a[0] == 'check':
        only = set(load_tl_file(a[1])) if len(a) > 1 else None
        sys.exit(0 if check(only) else 1)
    else:
        print(__doc__)
