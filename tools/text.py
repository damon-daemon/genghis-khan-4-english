"""MESSAGE.CK records <-> translation segments, English encoding and build-time word wrap.

A record (msgfile.Parser tokens) is a chain of structural codes (05 05 05 ends, 01 I/S/J/F) and
segments: maximal runs of text and inline codes (02 expressions = names/numbers, 01 C calls, ESC, 0a)
that hold at least one text token. The English replaces a whole segment; the structure is kept.

English syntax (tl/msg/NN.txt, see tl.py):
  {C6}  {C6005}       call message id 6 (block 0) / 6005 (same block): must be in the Japanese segment
  {021e0203}          expression from the Japanese segment (hex as shown), e.g. a person's name
  \\n                  forced line break; \\p page break; \\s a space kept at a segment edge
  ~                   (whole segment) empty
The game pages a box every PAGE lines (counting newlines, before rendering; a blank line is only an
empty line) -> the English is wrapped here, with the VWF advances (pixfont), at the block's box width.
"""
import os, re, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import msgfile, pixfont

STRUCT = (0x49, 0x53, 0x4a, 0x46)      # 01 I / S / J / F: segment boundaries (with 05 05 05)
WRAP_PX, PAGE = 208, 3                 # court dialogue box 224 px (session 9); event dialogue 26 bytes
LINE_B = 40                            # = vwf.LINE_B: event pages are 128-byte slots (session 16)
# per-block box (px, lines per page); filled in during the emulator pass
BOX = {b: (224, 4, 999) for b in range(12, 19)}   # biographies (ruler select, session 14): no byte cap
NOPAGE = set(range(12, 19))            # boxes that never page: more lines draw outside the frame
EXPR_PX = {0x1e: 64, 0x85: 64, 0x86: 56, 0x6b: 56}   # guessed widths of names etc.; default EXPR_DEF
EXPR_DEF, CALL_PX = 40, 24
EXPR_B, CALL_B = 16, 8                  # byte guesses for the expanded size (buffer check)
WRAP_EXPR_B = 12                       # line wrap: typical name size, the runtime breaker caps the
                                       # real bytes (vwf.break_lines), a long name costs a line
EXPAND_MAX = 254                        # interpreter output buffer 0x8016ee70 (overflow flag at 0xfe)
_TOK = re.compile(r'\{C(\d+)\}|\{(02[0-9a-f]+)\}')
_W = None


def is_struct(v):
    return v[:3] == msgfile.END or (v[:1] == b'\x01' and v[1] in STRUCT)


def items(rec):
    """Record -> [('code', bytes) | ('seg', [tokens])]; None if the record does not parse."""
    try:
        toks = msgfile.Parser(rec).tokens()
    except msgfile.ParseError:
        return None
    out, run = [], []

    def flush():
        if any(k == 't' for k, _ in run):
            out.append(('seg', list(run)))
        else:
            out.extend(('code', v) for _, v in run)
        run.clear()
    for k, v in toks:
        if k == 'c' and is_struct(v):
            flush()
            out.append(('code', v))
        else:
            run.append((k, v))
    flush()
    return out


def join(its):
    return b''.join(v if k == 'code' else b''.join(x for _, x in v) for k, v in its)


def inline(tokens):
    """The {C..}/{02..} tags a segment offers (list, with repeats)."""
    out = []
    for k, v in tokens:
        if k == 'c' and v[:2] == b'\x01\x43':
            out.append('{C%d}' % struct.unpack_from('<H', v, 2)[0])
        elif k == 'c' and v[:1] == b'\x02':
            out.append('{%s}' % v.hex())
    return out


def seg_str(tokens):
    """Readable Japanese of a segment (kana as in setup.readable; calls as {C..})."""
    import setup
    s, kata = '', False
    for k, v in tokens:
        if k == 't':
            s += setup.kana(v.decode('cp932', 'replace'), kata)
        elif v == b'\n':
            s += '\\n'
        elif v[:2] == b'\x01\x43':
            s += '{C%d}' % struct.unpack_from('<H', v, 2)[0]
        elif v[:1] == b'\x1b':
            kata = v[1:2] == b'K' or (kata and v[1:2] not in b'H')
            if v[1:2] == b'k':
                s += '{reading:}'
        else:
            s += '{%s}' % v.hex()
    return s


def segments(rec):
    """[tokens] of every segment of a record ([] if unparseable)."""
    return [v for k, v in items(rec) or [] if k == 'seg']


# ---------------------------------------------------------------- English
def widths():
    global _W
    if _W is None:
        _W = {c: w for c, (_, w) in pixfont.glyphs().items()}
    return _W


def tag_px(t):
    if t.startswith('{C'):
        return CALL_PX
    return EXPR_PX.get(int(t[3:5], 16), EXPR_DEF)


def word_px(w):
    W = widths()
    return sum(tag_px(m.group()) if m.group().startswith('{') else W.get(m.group(), 8)
               for m in re.finditer(r'\{[^}]*\}|.', w))


def word_b(w):
    """Printed bytes of a word (names etc. guessed)."""
    return sum((CALL_B if m.group(1) else WRAP_EXPR_B) for m in _TOK.finditer(w)) + len(_TOK.sub('', w))


def wrap(en, px=WRAP_PX, maxb=LINE_B):
    """English -> list of pages, each a list of lines (words wrapped at px; \\n and \\p kept)."""
    pages = []
    for p in en.split('\\p'):
        lines = []
        for para in p.strip(' ').split('\\n'):
            cur, x, nb = [], 0, 0
            for w in para.split(' '):
                add = word_px(w) + (widths()[' '] if cur else 0)
                addb = word_b(w) + (1 if cur else 0)
                if cur and (x + add > px or nb + addb > maxb):
                    lines.append(' '.join(cur)); cur, x, nb = [w], word_px(w), word_b(w)
                else:
                    cur.append(w); x += add; nb += addb
            lines.append(' '.join(cur))
        pages.append(lines)
    return pages


def layout(en, box=(WRAP_PX, PAGE)):
    """Wrapped English as the game string: lines joined by \\n; \\p pads the page with empty lines
    (the box pages every box[1] lines, a blank line is just an empty line)."""
    pages = wrap(en, *box[:1], *box[2:])
    for p in pages[:-1]:
        p += [''] * (-len(p) % box[1])
    return '\n'.join(ln for p in pages for ln in p)


def count(en, box=(WRAP_PX, PAGE)):
    """(lines, pages) the box shows."""
    pg = wrap(en, *box[:1], *box[2:])
    return sum(map(len, pg)), sum(-(-len(p) // box[1]) for p in pg)


def encode(en, box=(WRAP_PX, PAGE)):
    """English segment -> segment bytes (after the check); box None = no wrap (block 0 words)."""
    if en == '~':
        return b''
    s = en.replace('\\s', '\x00')
    s = layout(s, box) if box else s.replace('\\p', '\n\n').replace('\\n', '\n')
    s = s.replace('\x00', ' ')
    out, pos = bytearray(), 0
    for m in _TOK.finditer(s):
        out += s[pos:m.start()].encode('ascii')
        if m.group(1):
            out += b'\x01\x43' + struct.pack('<H', int(m.group(1)))
        else:
            out += bytes.fromhex(m.group(2))
        pos = m.end()
    out += s[pos:].encode('ascii')
    return bytes(out)


def check(tokens, en):
    """Errors of one English segment against its Japanese tokens."""
    if en == '~':
        return []
    errs = []
    plain = _TOK.sub('', en).replace('\\n', '').replace('\\p', '').replace('\\s', '')
    bad = sorted(set(c for c in plain if not (' ' <= c <= '~') or c in '{}\\'))
    if bad:
        errs.append('characters not allowed: %s' % ''.join(bad))
    have = inline(tokens)
    for t in set(m.group() for m in _TOK.finditer(en)):
        n = len(re.findall(re.escape(t), en))
        if n > have.count(t):
            errs.append('%s used %d times, Japanese has %d' % (t, n, have.count(t)))
    if '\n' in en or '\\n\\n' in en:
        errs.append('use \\p for a page break')
    return errs


def expanded(en):
    """Rough printed size in bytes (names expanded)."""
    s = layout(en)
    n = sum(CALL_B if m.group(1) else EXPR_B for m in _TOK.finditer(s))
    return n + len(_TOK.sub('', s))
