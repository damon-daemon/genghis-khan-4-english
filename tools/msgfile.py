"""DATA/MESSAGE.CK: 44 message blocks (Koei message script).

File layout: block b sits at the u32 file offset table 0x800efeb0[b] (EXE) and is u16 0x800edbd8[b] bytes
long; blocks are sector-aligned in the original file. Block = u16 count, count u16 offsets (block-relative),
then the records. Message id = block * 1000 + index (interpreter 0x80099fd4). Block 0 is resident at the
script buffer 0x8016ef70; other blocks load at 0x8016ef70 + 0x4778 (one at a time).

Record = segments, each ending in 05 05 05. Interpreter 0x8009bed0 (text loop) / 0x8009a7f8 (control):
  01 'I' cond      if cond false skip to after the next 05 05 05, else print the segment and end
  01 'S' expr      skip expr segments
  01 'C' u16       call message id (return goes back into the *current buffer*: only block 0 or same block)
  01 'J' u16       jump to message id
  01 'F'           (no operand)
  02 ...           expression printed as text (names, numbers)
  1b x             ESC: H = half-width kana shown as hiragana (default), K = katakana, k = ?, W/C = passed on
  08 09 0a         passed to the renderer (0a = newline)
  0xa6-0xdf        half-width kana -> full-width hira/kata; other bytes >= 0x20: SJIS (2 bytes) / ASCII
cond := cmp (06 ('&'|'|') cmp)* ; cmp := expr [04 op expr] (op = ! = < > { }) ; expr := term (03 op term)*
term := 02 v args(v) | ('%'|digit) digit* ; args per var: see ARGS.
"""
import os, struct, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JP = os.path.join(ROOT, 'extract', 'DATA', 'MESSAGE.CK')
EXE = os.path.join(ROOT, 'extract', 'SLPM_861.75')
NBLK = 44
SIZE_TAB, OFF_TAB = 0x800edbd8, 0x800efeb0

# argument kinds per variable (jump table 0x800d1fd8): r = "02 nn" (2 bytes), e = expression
ARGS = {v: '' for v in range(0x1e)}
for _k, _vs in (('', '3c 7c 7d'), ('e', '7a 7b 91'), ('re', '46 6e 6f 70 74 75 78 84'),
                ('rr', '27 28 29 2a 2b 2c 2d 2e 2f 30 31 32 33 3a 3b 5a 5b 5c 61 62 63 7f 80 82 83 93 94 95 96')):
    for _v in _vs.split():
        ARGS[int(_v, 16)] = _k
for _v in range(0x1e, 0x97):
    ARGS.setdefault(_v, 'r')
END = b'\x05\x05\x05'


NAME = 'DATA/MESSAGE.CK'              # ISO path


def exe_tables(x=None):
    """(file offsets, sizes) of the 44 blocks from the EXE image (bytes; default: extract/)."""
    x = x if x is not None else open(EXE, 'rb').read()
    f = lambda va: va - 0x80010000 + 0x800
    return (list(struct.unpack_from('<%dI' % NBLK, x, f(OFF_TAB))),
            list(struct.unpack_from('<%dH' % NBLK, x, f(SIZE_TAB))))


def blocks(d=None, exe=None):
    """Block bytes from MESSAGE.CK (bytes; default: extract/) and the EXE tables."""
    d = d if d is not None else open(JP, 'rb').read()
    offs, sizes = exe_tables(exe)
    return [d[o:o + n] for o, n in zip(offs, sizes)]


def records(blk):
    n = struct.unpack_from('<H', blk, 0)[0]
    offs = struct.unpack_from('<%dH' % n, blk, 2)
    ends = list(offs[1:]) + [len(blk)]
    return [blk[a:b] for a, b in zip(offs, ends)]


class ParseError(Exception):
    pass


class Parser:
    """Splits a record into tokens: ('t', bytes) text, ('c', bytes) code (kept verbatim)."""

    def __init__(self, b):
        self.b, self.i = b, 0

    def peek(self, k=0):
        return self.b[self.i + k] if self.i + k < len(self.b) else None

    def take(self, n=1):
        s = self.b[self.i:self.i + n]
        if len(s) < n:
            raise ParseError('eof at %#x' % self.i)
        self.i += n
        return s

    def term(self):
        c = self.peek()
        if c == 2:
            v = self.take(2)[1]
            for k in ARGS.get(v, ''):
                if k == 'e':
                    self.expr()
                elif self.peek() != 2:
                    raise ParseError('arg at %#x' % self.i)
                else:
                    self.take(2)
        elif c == 0x25 or (c is not None and 0x30 <= c <= 0x39):
            self.take()
            k = 0
            while k < 10 and self.peek() is not None and 0x30 <= self.peek() <= 0x39:
                self.take(); k += 1
        else:
            raise ParseError('term %r at %#x' % (c, self.i))

    def expr(self):
        self.term()
        while self.peek() == 3:
            self.take(2)
            self.term()

    def cmp(self):
        self.expr()
        if self.peek() == 4:
            self.take(2)
            self.expr()

    def cond(self):
        self.cmp()
        while self.peek() == 6:
            self.take(2)
            self.cmp()

    def tokens(self):
        out, txt = [], bytearray()

        def code(start):
            if txt:
                out.append(('t', bytes(txt))); txt.clear()
            out.append(('c', self.b[start:self.i]))

        while self.i < len(self.b):
            s, c = self.i, self.b[self.i]
            if c == 5 and self.b[self.i:self.i + 3] == END:
                self.i += 3; code(s)
            elif c == 1:
                op = self.take(2)[1]
                if op == 0x49:
                    self.cond()
                elif op == 0x53:
                    self.expr()
                elif op in (0x43, 0x4a):
                    self.take(2)
                elif op == 0x46:
                    pass
                else:
                    raise ParseError('cmd %02x at %#x' % (op, s))
                code(s)
            elif c == 2:
                self.expr(); code(s)
            elif c == 0x1b:
                self.take(2); code(s)
            elif c in (8, 9, 0x0a):
                self.i += 1; code(s)
            elif c < 0x20:
                raise ParseError('ctl %02x at %#x' % (c, s))
            elif 0x81 <= c <= 0x9f or 0xe0 <= c <= 0xfc:
                txt += self.take(2)
            else:
                txt += self.take()
        if txt:
            out.append(('t', bytes(txt)))
        return out


# ---- build path -------------------------------------------------------------------------------------------
ALIGN = 2048                          # blocks start on sectors (as in the original file)
BLOCK0_MAX = 0x4778                   # resident slot 0x8016ef70; other blocks load at +0x4778
BLOCK_MAX = 0x3800                    # 0x801736e8 + 0x3800 = 0x80176ee8 (live data from 0x80177b70)
MAX_BLOCKS = 65                       # ids are u16: block * 1000 + index
TABLE_AT, TABLE_END = 0x800da25c, 0x800da40c   # relocated tables (> 44 blocks): after vwf.py's font (vwf.FONT_END)
LOADER_OFF = (0x80099f64, 0x80099f6c)  # lui/addiu of the u32 offset table (block != 0 path)
LOADER_SIZE = (0x80099f88, 0x80099f94)  # lui/addiu of the u16 size table (block != 0 path)
EXE_BASE = 0x80010000 - 0x800         # va - file offset


def build_block(recs):
    """Records (bytes) -> block bytes: u16 count, count u16 offsets, records."""
    head = 2 + 2 * len(recs)
    offs, pos = [], head
    for r in recs:
        offs.append(pos); pos += len(r)
    if pos > 0xffff:
        raise ValueError('block too big for u16 offsets: %d' % pos)
    return struct.pack('<H%dH' % len(recs), len(recs), *offs) + b''.join(recs)


def refs(rec):
    """[(op, id)] of the C (0x43) / J (0x4a) commands in a record ([] for unparseable records)."""
    try:
        toks = Parser(rec).tokens()
    except ParseError:
        return []
    return [(v[1], struct.unpack_from('<H', v, 2)[0]) for k, v in toks if k == 'c' and v[:1] == b'\x01'
            and v[1] in (0x43, 0x4a)]


def retarget(rec, new_id):
    """Rewrite C/J operands through new_id {old id: new id}."""
    try:
        toks = Parser(rec).tokens()
    except ParseError:
        return rec
    out = []
    for k, v in toks:
        if k == 'c' and v[:1] == b'\x01' and v[1] in (0x43, 0x4a):
            t = struct.unpack_from('<H', v, 2)[0]
            v = v[:2] + struct.pack('<H', new_id.get(t, t))
        out.append(v)
    return b''.join(out)


def stub(i):
    return b'\x01J' + struct.pack('<H', i) + END


def _callees(b, recs, i):
    """Same-block C targets of record i, transitively (indexes)."""
    seen, todo = set(), [i]
    while todo:
        for op, t in refs(recs[todo.pop()]):
            if op == 0x43 and t // 1000 == b and t % 1000 not in seen:
                seen.add(t % 1000); todo.append(t % 1000)
    seen.discard(i)
    return seen


def split_blocks(blocks, limit=BLOCK_MAX):
    """blocks: list of record lists. Blocks over the limit move records (from the end) to overflow blocks
    44.. and keep a J stub at the old index. A C return does not reload the block, so C targets never move:
    every overflow block gets its own copies of the callees its records need. Returns the new record lists."""
    blocks = [list(r) for r in blocks]
    if len(build_block(blocks[0])) > BLOCK0_MAX:
        raise ValueError('block 0: %d > %d bytes' % (len(build_block(blocks[0])), BLOCK0_MAX))
    over, new_id = [], {}                     # over: [[(source block, moved idx)], {callee ids}] first-fit
    for b in range(1, len(blocks)):
        recs = blocks[b]
        size = len(build_block(recs))
        if size <= limit:
            continue
        called = set()
        for i in range(len(recs)):
            called |= _callees(b, recs, i)
        for i in reversed(range(len(recs))):
            if size <= limit:
                break
            if i in called or len(recs[i]) <= len(stub(0)):
                continue
            need = {b * 1000 + j for j in _callees(b, recs, i)}
            for ob in over:
                if len(build_block([blocks[c][j] for c, j in ob[0] + [(b, i)]] +
                                  [blocks[x // 1000][x % 1000] for x in ob[1] | need])) <= limit:
                    break
            else:
                ob = [[], set()]; over.append(ob)
            ob[0].append((b, i)); ob[1] |= need
            new_id[b * 1000 + i] = None
            size -= len(recs[i]) - len(stub(0))
    if len(blocks) + len(over) > MAX_BLOCKS:
        raise ValueError('%d blocks > %d' % (len(blocks) + len(over), MAX_BLOCKS))
    local = []
    for n, (moved, need) in enumerate(over):
        nb = (len(blocks) + n) * 1000
        new_id.update((b * 1000 + i, nb + k) for k, (b, i) in enumerate(moved))
        local.append({x: nb + len(moved) + k for k, x in enumerate(sorted(need))})
    out = [[retarget(stub(new_id[b * 1000 + i]) if b * 1000 + i in new_id else r, new_id)
            for i, r in enumerate(recs)] for b, recs in enumerate(blocks)]
    for (moved, need), loc in zip(over, local):
        ids = {**new_id, **loc}
        out.append([retarget(blocks[x // 1000][x % 1000], ids) for x in
                    [b * 1000 + i for b, i in moved] + sorted(need)])
    for b, recs in enumerate(out):
        n = len(build_block(recs))
        if n > (BLOCK0_MAX if b == 0 else limit):
            raise ValueError('block %d still %d bytes' % (b, n))
    return out


def build(blocks):
    """Record lists -> (MESSAGE.CK bytes, offsets, sizes)."""
    data, offs, sizes = bytearray(), [], []
    for recs in blocks:
        blk = build_block(recs)
        data += bytes(-len(data) % ALIGN)
        offs.append(len(data)); sizes.append(len(blk))
        data += blk
    data += bytes(-len(data) % ALIGN)
    return bytes(data), offs, sizes


def _set_hilo(exe, at, va):
    """Patch a lui/addiu pair (register fields kept) to load va."""
    lui, add = at[0] - EXE_BASE, at[1] - EXE_BASE
    hi, lo = (va + 0x8000) >> 16, va & 0xffff
    w = struct.unpack_from('<I', exe, lui)[0]
    struct.pack_into('<I', exe, lui, (w & 0xffff0000) | hi)
    w = struct.unpack_from('<I', exe, add)[0]
    struct.pack_into('<I', exe, add, (w & 0xffff0000) | lo)


def patch_exe(exe, offs, sizes):
    """Write the block tables into the EXE (bytearray); > 44 blocks -> tables at TABLE_AT + loader patch."""
    f = lambda va: va - EXE_BASE
    if len(offs) <= NBLK:
        offs = offs + [0] * (NBLK - len(offs)); sizes = sizes + [0] * (NBLK - len(sizes))
        struct.pack_into('<%dI' % NBLK, exe, f(OFF_TAB), *offs)
        struct.pack_into('<%dH' % NBLK, exe, f(SIZE_TAB), *sizes)
        return
    n = len(offs)
    struct.pack_into('<H', exe, f(SIZE_TAB), sizes[0])          # block-0 path still reads the old table
    size_at = TABLE_AT + 4 * n
    if size_at + 2 * n > TABLE_END:
        raise ValueError('block tables do not fit')
    struct.pack_into('<%dI' % n, exe, f(TABLE_AT), *offs)
    struct.pack_into('<%dH' % n, exe, f(size_at), *sizes)
    _set_hilo(exe, LOADER_OFF, TABLE_AT)
    _set_hilo(exe, LOADER_SIZE, size_at)


def show(tokens):
    """Readable form for notes/dumps (not used for building)."""
    s = ''
    for k, v in tokens:
        if k == 't':
            s += v.decode('cp932', 'replace')
        elif v == b'\n':
            s += '\\n'
        else:
            s += '{' + v.hex() + '}'
    return s


if __name__ == '__main__':
    bad = total = 0
    for bi, blk in enumerate(blocks()):
        for ri, r in enumerate(records(blk)):
            total += 1
            try:
                t = Parser(r).tokens()
                assert b''.join(v for _, v in t) == r
            except (ParseError, AssertionError) as e:
                bad += 1
                if bad <= 20:
                    print(bi, ri, e, r[:40].hex(' '))
            if len(sys.argv) > 1 and sys.argv[1] == 'dump':
                print('%d.%d %s' % (bi, ri, show(t)))
    print('records', total, 'bad', bad, file=sys.stderr)
    recs = [records(b) for b in blocks()]
    assert build(recs)[0] == open(JP, 'rb').read(), 'rebuild differs'
    print('rebuild byte-identical', file=sys.stderr)
