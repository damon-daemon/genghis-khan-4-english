"""Proportional Latin font in the text renderer (notes/render.md; same engine as S6's vwf.py).

putc 0x8004e348 draws single-byte characters through draw_char 0x8004de64 with the half-width
font set up at init (0x8002e4e0: 128 sorted u16 codes HALF_CODES, 8x16 1bpp glyphs HALF_GLYPHS,
0x20-0x5F + kana, no lower case) and advances by half a cell (8 px). This patch
  - replaces the glyphs with ' '..'~' from tools/pixfont.py (95 x 16 B) followed by their
    advance widths (WIDTHS) and one byte that keeps the width across the draw call (SAVED);
    msgfile.TABLE_AT (relocated block tables) starts right after them;
  - rewrites draw_char's single-byte branch (DRAW_HALF): glyph = font + (code - 0x20) * 16 for
    0x20..0x7E, nothing drawn for other single bytes; the binary search over HALF_CODES is no
    longer used, so the code table (0x100 B) holds the word-wrap wrapper;
  - rewrites the single-byte path of putc (PUTC_SINGLE .. PUTC_END): auto-wrap test and advance
    use the width of the character instead of 8 (other single bytes: DEFAULT_W);
  - fixes the 4bpp blit for odd x (fix_blit): it picked the nibble from the glyph column alone;
  - word wrap (wrap_hook): render_string 0x8004fb00 calls putc for each byte (PRINT_LOOP,
    s0 = the byte's address). That call goes through the wrapper: at a space it measures the
    next word (up to a byte <= 0x20; ESC..'C' colour codes skipped; 2-byte Shift-JIS = cell
    advance, other single bytes = their width) and sends a newline instead of the space when the
    word would end past the wrap width: the s16 before the wrapper (wrap_limit) if >= 0, else
    the bitmap width (GP_WRAP, < 0 = no wrap).
  - column snap (snap, session 14): Japanese tables are rows padded with spaces to fixed byte
    columns (8 px per byte). In a line that holds a double space, a byte that ends a run of
    spaces (>= 2, or one before a digit) moves x to its byte column * 8 (ESC colour codes not
    counted) unless x is already past it, so padded rows line up again with the proportional font. render_string's
    bitmap-clear call (CLEAR_CALL) goes through a stub that keeps the string start (START).
    A TAB byte (0x09, `\t` in tl/progstr) draws nothing and moves x to the next multiple of
    32 px: "Nation\t%s" puts the value after the 2-kanji label box like the Japanese.
  - line breaker (break_lines, session 16): 0x8002a790(src, dst, n) copied src and put a newline
    every n bytes (13 kanji = 26 for the event dialogue), cutting English words. Rewritten as a
    word wrap at (n & 0xff) * 8 px with the font widths; with n & CAPPED (set for the event dialogue,
    which splits pages into 128-byte slots at obj+0x304) at most LINE_B bytes per line; newlines kept, output = input length.
    The event dialogue (0x80073a3c) now pages every 3 lines like the build-time wrap (EVENT_LINES).
  - longest line (line_cells, session 16): 0x8002852c(str) returned the longest line in bytes;
    callers size boxes / centre text with it * 8 -> now ceil((longest line px + CELL_PAD) / 8);
    label boxes (general detail 'City', 'Loyal') put the value right after the box.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, pixfont

EXE_BASE = 0x80010000 - 0x800
HALF_GLYPHS = 0x800d9c0c              # gp-0x7768, 0x800 B
HALF_CODES = 0x800da40c               # gp-0x7764, 128 u16 (0x100 B) -> wrapper
HALF_N = 128
ASCII = list(range(0x20, 0x7F))       # 95 codes
WIDTHS = HALF_GLYPHS + 16 * len(ASCII)   # u8 width of 0x20..0x7E, then the saved width
SAVED = WIDTHS + len(ASCII)
FONT_END = SAVED + 1                  # = msgfile.TABLE_AT
DEFAULT_W = 8                         # other single-byte codes
DRAW_HALF, DRAW_RET = 0x8004e0d0, 0x8004e1f8   # draw_char single-byte branch .. return 1
BLIT = 0x8004dcc0                     # blit(glyph, x, y, w, 0x10(sp) = h)
GP_FONT = -0x7768
PUTC_SINGLE, PUTC_END = 0x8004e544, 0x8004e5e4   # rewritten range; PUTC_END = common exit
DRAW_CHAR = 0x8004de64
GP_WRAP, GP_X, GP_Y, GP_ADV_Y, GP_STATE = -0x7750, -0x7742, -0x7740, -0x7f8a, -0x7724
GP_ADV_X = -0x7f8c
PUTC = 0x8004e348
PRINT_LOOP = 0x8004fbcc                 # jal 0x8004fc34 (j putc) in render_string's loop


def word(exe, va):
    return struct.unpack_from('<I', exe, va - EXE_BASE)[0]


def put(exe, va, data):
    exe[va - EXE_BASE:va - EXE_BASE + len(data)] = data


def draw_half():
    """At DRAW_HALF: v1 = code, s0 = x, s1 = y, t1 = glyph height (gp-0x7f8e)."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(DRAW_HALF, [
        ('andi', 'v1', 'v1', 0xff),
        ('addiu', 'v1', 'v1', -0x20),
        ('sltiu', 'at', 'v1', len(ASCII)),
        ('beqz', 'at', DRAW_RET),
        ('lw', 'a0', GP_FONT, 'gp'),
        ('sll', 'v1', 'v1', 4),
        ('addu', 'a0', 'a0', 'v1'),
        ('sw', 't1', 0x10, 'sp'),
        ('addu', 'a1', 's0', 'zero'),
        ('addu', 'a2', 's1', 'zero'),
        ('jal', BLIT),
        ('addiu', 'a3', 'zero', 8),
        ('b', DRAW_RET),
        ('nop',),
    ])


def hook():
    hi, lo = asm.hi, asm.lo
    return asm.assemble(PUTC_SINGLE, [
        ('addiu', 't0', 'a2', -0x20),
        ('sltiu', 'at', 't0', len(ASCII)),
        ('beqz', 'at', 'store'),
        ('addiu', 't1', 'zero', DEFAULT_W),
        ('lui', 't1', hi(WIDTHS)),
        ('addu', 't1', 't1', 't0'),
        ('lbu', 't1', lo(WIDTHS), 't1'),
        ('nop',),
        'store:',
        ('lui', 'at', hi(SAVED)),
        ('sb', 't1', lo(SAVED), 'at'),
        ('lh', 'v1', GP_WRAP, 'gp'),
        ('lh', 'v0', GP_X, 'gp'),
        ('bltz', 'v1', 'draw'),
        ('addu', 'v0', 'v0', 't1'),           # x + width (load delay: v0 ready here)
        ('slt', 'at', 'v1', 'v0'),
        ('beqz', 'at', 'draw'),
        ('nop',),
        ('lh', 'v1', GP_Y, 'gp'),
        ('lh', 'v0', GP_ADV_Y, 'gp'),
        ('sh', 'zero', GP_X, 'gp'),
        ('addu', 'v0', 'v1', 'v0'),
        ('sh', 'v0', GP_Y, 'gp'),
        'draw:',
        ('lh', 'a0', GP_X, 'gp'),
        ('lh', 'a1', GP_Y, 'gp'),
        ('jal', DRAW_CHAR),
        ('nop',),
        ('bnez', 'v0', 'adv'),
        ('nop',),
        ('b', PUTC_END),
        ('sb', 'zero', GP_STATE, 'gp'),
        'adv:',
        ('lui', 'at', hi(SAVED)),
        ('lbu', 'v1', lo(SAVED), 'at'),
        ('lh', 'v0', GP_X, 'gp'),
        ('nop',),
        ('addu', 'v0', 'v0', 'v1'),
        ('sh', 'v0', GP_X, 'gp'),
    ])


# 4bpp blit loops (shadow pass at x+1 in s2, main pass at x in t8): nibble choice (P)
# and byte step (Q) test column & 1; patched to (column + x) & 1.
BLIT_LOOPS = [(0x8004d78c, 0x8004d7dc, 's2'), (0x8004da04, 0x8004da54, 't8')]
BLIT_P = [0x30e80001, 0x04e10004, 0x30c600ff, 0x11000002, 0x00000000, 0x2508fffe]
BLIT_Q = [0x04e10004, 0x30e60001, 0x10c00002, 0x00000000, 0x24c6fffe]


def fix_blit(exe):
    for p, q, xr in BLIT_LOOPS:
        assert [word(exe, p + 4 * i) for i in range(6)] == BLIT_P, hex(p)
        assert [word(exe, q + 4 * i) for i in range(5)] == BLIT_Q, hex(q)
        put(exe, p, asm.assemble(p, [('addu', 't0', 'a3', xr), ('andi', 't0', 't0', 1),
                                     ('andi', 'a2', 'a2', 0xff), ('nop',), ('nop',), ('nop',)]))
        put(exe, q, asm.assemble(q, [('addu', 'a2', 'a3', xr), ('andi', 'a2', 'a2', 1),
                                     ('nop',), ('nop',), ('nop',)]))


WRAPPER = HALF_CODES + 4                # wrap_limit (s16) at HALF_CODES


def wrap_limit(wrapper_at=WRAPPER):
    """Address of the wrapper's line width (s16; < 0 = the bitmap width GP_WRAP)."""
    return wrapper_at - 4


def wrapper(at):
    hi, lo = asm.hi, asm.lo
    lim = wrap_limit(at)
    return asm.assemble(at, [
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 'a0', 'at', 'nonspace'),
        ('lui', 't2', hi(lim)),
        ('lh', 't2', lo(lim), 't2'),
        ('nop',),
        ('bgez', 't2', 'limit'),
        ('lui', 't5', hi(WIDTHS)),
        ('lh', 't2', GP_WRAP, 'gp'),
        ('nop',),
        ('bltz', 't2', 'out'),
        'limit:',
        ('addiu', 't5', 't5', lo(WIDTHS)),
        ('lh', 't4', GP_X, 'gp'),
        ('lbu', 't1', 0, 't5'),              # width of ' '
        ('addiu', 't3', 's0', 1),
        ('addu', 't4', 't4', 't1'),
        'loop:',
        ('lbu', 't0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't0', 'at', 'esc'),
        ('sltiu', 'at', 't0', 0x21),
        ('bnez', 'at', 'done'),
        ('sltiu', 'at', 't0', 0x7F),
        ('beqz', 'at', 'high'),
        ('addu', 't1', 't5', 't0'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'loop'),
        ('addu', 't4', 't4', 't1'),
        'high:',
        ('addiu', 't1', 't0', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('beqz', 'at', 'lead'),
        ('lh', 't1', GP_ADV_X, 'gp'),
        ('b', 'loop'),
        ('addiu', 't4', 't4', DEFAULT_W),
        'lead:',
        ('addiu', 't3', 't3', 1),            # skip the trail byte
        ('b', 'loop'),
        ('addu', 't4', 't4', 't1'),
        'esc:',
        ('lbu', 't0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('beqz', 't0', 'done'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't0', 'at', 'esc'),
        ('nop',),
        ('b', 'loop'),
        ('nop',),
        'done:',
        ('slt', 'at', 't2', 't4'),
        ('beqz', 'at', 'out'),
        ('nop',),
        ('addiu', 'a0', 'zero', 0x0A),
        'out:',
        ('j', PUTC),
        ('nop',),
        'nonspace:',
        ('j', SNAP),
        ('nop',),
    ])


CLEAR_CALL, CLEAR_THUNK = 0x8004fbbc, 0x8004fc2c   # jal in render_string before the loop
START = HALF_CODES + 2 * HALF_N - 16    # u32 string start, then the start stub (12 B)
STUB = START + 4
SNAP = None                             # set by apply(): after draw_half() in draw_char's branch
TAB, TAB_PX = 0x09, 32                  # label tab: label boxes are 2 kanji wide


def snap(at):
    """At a non-space a0 (byte at s0): column snap (see the module doc), then j PUTC.
    Snaps at the end of a run of >= 2 spaces, or of one space before a digit ("Local 28") or
    an SJIS lead byte ("Local \x81~"),
    in a line that holds a double space. Bytes before the string start may be read: the
    scan only counts bytes from the start, so a stray space there never snaps."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('lh', 't1', GP_X, 'gp'),
        ('addiu', 'at', 'zero', TAB),
        ('bne', 'a0', 'at', 'notab'),
        ('addiu', 't1', 't1', TAB_PX - 1),        # TAB: x to the next TAB_PX, nothing drawn
        ('addiu', 'at', 'zero', -TAB_PX),
        ('and', 't1', 't1', 'at'),
        ('jr', 'ra'),
        ('sh', 't1', GP_X, 'gp'),
        'notab:',
        ('lui', 't5', hi(START)),
        ('lw', 't3', lo(START), 't5'),            # p = string start
        ('lbu', 't1', -1, 's0'),
        ('lbu', 't9', -2, 's0'),
        ('addiu', 't0', 'zero', 0x20),
        ('bne', 't1', 't0', 'putc'),              # previous byte not a space
        ('addiu', 't2', 'a0', -0x30),
        ('beq', 't9', 't0', 'scan'),              # run of >= 2 spaces
        ('sltiu', 't2', 't2', 10),
        ('srl', 't1', 'a0', 7),                   # SJIS lead byte (the x in a Trade column)
        ('or', 't2', 't2', 't1'),
        ('beqz', 't2', 'putc'),                   # one space, not before a digit / SJIS char
        'scan:',
        ('addu', 't4', 't3', 'zero'),             # line start (also the delay slot: t4 free at putc)
        ('addu', 't6', 'zero', 'zero'),           # double space seen in this line
        ('addu', 't7', 'zero', 'zero'),           # previous byte
        ('addu', 't8', 'zero', 'zero'),           # ESC bytes in this line
        'loop:',
        ('sltu', 'at', 't3', 's0'),
        ('beqz', 'at', 'after'),
        ('lbu', 't1', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('addiu', 'at', 'zero', 0x0A),
        ('bne', 't1', 'at', 'notnl'),
        ('addiu', 'at', 'zero', 0x1B),
        ('addu', 't4', 't3', 'zero'),
        ('addu', 't6', 'zero', 'zero'),
        ('b', 'prev'),
        ('addu', 't8', 'zero', 'zero'),
        'notnl:',
        ('bne', 't1', 'at', 'notesc'),
        ('addiu', 'at', 'zero', 0x43),            # (delay slot; at is reset at loop)
        'escl:',                                  # ESC .. 'C' (colour code)
        ('lbu', 't1', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('bne', 't1', 'at', 'escl'),
        ('addiu', 't8', 't8', 1),
        ('addiu', 't8', 't8', 1),
        'notesc:',
        ('bne', 't1', 't0', 'prev'),
        ('nop',),
        ('bne', 't7', 't0', 'prev'),
        ('nop',),
        ('addiu', 't6', 'zero', 1),
        'prev:',
        ('b', 'loop'),
        ('addu', 't7', 't1', 'zero'),
        'after:',
        ('beqz', 't6', 'putc'),
        ('subu', 't1', 's0', 't4'),
        ('subu', 't1', 't1', 't8'),
        ('lh', 't2', GP_X, 'gp'),
        ('sll', 't1', 't1', 3),
        ('slt', 'at', 't2', 't1'),
        ('beqz', 'at', 'putc'),
        ('nop',),
        ('sh', 't1', GP_X, 'gp'),
        'putc:',
        ('j', PUTC),
        ('nop',),
    ])


def start_stub(at):
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('lui', 'at', hi(START)),
        ('j', CLEAR_THUNK),
        ('sw', 's0', lo(START), 'at'),
    ])


def wrap_hook(exe):
    assert word(exe, PRINT_LOOP) == 0x0c013f0d, 'render_string putc call'
    code = wrapper(WRAPPER)
    assert WRAPPER + len(code) <= START, 'wrapper too long (%d bytes)' % len(code)
    put(exe, wrap_limit(), b'\xff\xff\0\0')
    put(exe, WRAPPER, code)
    put(exe, PRINT_LOOP, asm.assemble(PRINT_LOOP, [('jal', WRAPPER)]))
    assert word(exe, CLEAR_CALL) == 0x0c013f0b, 'render_string clear call'
    put(exe, START, b'\0' * 4 + start_stub(STUB))
    put(exe, CLEAR_CALL, asm.assemble(CLEAR_CALL, [('jal', STUB)]))


BREAK, BREAK_END = 0x8002a790, 0x8002a900   # line breaker(src, dst, n bytes), whole function
LINE_B = 40                                 # bytes per line: 3 lines + 2 newlines + NUL <= 128
EVENT_LINES = (0x80073abc, 0x24060004)      # addiu a2, zero, 4: lines per event dialogue page
EVENT_WIDTH = (0x80073860, 0x2406001a)      # addiu a2, zero, 26: the event dialogue's line bytes
CAPPED = 0x100                              # flag in n: apply LINE_B
CENTRE = (0x80028404, 0x8002840c, 0x8002844c)   # 0x800283c4: jal line_cells, offset code, its end
CELL_PAD = 6                                # px: label boxes sized by line_cells keep a gap before the value
CELLS, CELLS_END = 0x8002852c, 0x80028590   # longest line(str) in bytes, whole function


def break_lines(at):
    """t9 limit px, t8 widths, a3 space width, t6/t7 px/bytes of the line so far; at a space
    (or a line start, t1 = 1) the next word is measured (t4 px, t5 bytes)."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('addiu', 't0', 'zero', LINE_B),     # byte cap only with the CAPPED flag
        ('andi', 'at', 'a2', CAPPED),
        ('bnez', 'at', 'capped'),
        ('andi', 't9', 'a2', 0xFF),
        ('addiu', 't0', 'zero', 0x7FFF),
        'capped:',
        ('sll', 't9', 't9', 3),
        ('lui', 't8', hi(WIDTHS)),
        ('addiu', 't8', 't8', lo(WIDTHS)),
        ('b', 'linestart'),
        ('lbu', 'a3', 0, 't8'),
        'loop:',
        ('lbu', 'v0', 0, 'a0'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beqz', 'v0', 'end'),
        ('nop',),
        ('beq', 'v0', 'at', 'newline'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 'v0', 'at', 'space'),
        ('nop',),
        'copy:',
        ('sb', 'v0', 0, 'a1'),
        ('addiu', 'a0', 'a0', 1),
        ('b', 'loop'),
        ('addiu', 'a1', 'a1', 1),
        'newline:',
        ('sb', 'v0', 0, 'a1'),
        ('addiu', 'a0', 'a0', 1),
        ('addiu', 'a1', 'a1', 1),
        'linestart:',
        ('addu', 't3', 'a0', 'zero'),
        ('b', 'measure'),
        ('addiu', 't1', 'zero', 1),
        'space:',
        ('addiu', 't3', 'a0', 1),
        ('addu', 't1', 'zero', 'zero'),
        'measure:',
        ('addu', 't4', 'zero', 'zero'),
        ('addu', 't5', 'zero', 'zero'),
        'mloop:',
        ('lbu', 'v1', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 'v1', 'at', 'mesc'),
        ('sltiu', 'at', 'v1', 0x21),
        ('bnez', 'at', 'mdone'),
        ('sltiu', 'at', 'v1', 0x7F),
        ('beqz', 'at', 'mhigh'),
        ('addu', 't2', 't8', 'v1'),
        ('lbu', 't2', -0x20, 't2'),
        ('addiu', 't5', 't5', 1),
        ('b', 'mloop'),
        ('addu', 't4', 't4', 't2'),
        'mhigh:',                            # 2-byte Shift-JIS: one cell
        ('addiu', 't3', 't3', 1),
        ('addiu', 't5', 't5', 2),
        ('b', 'mloop'),
        ('addiu', 't4', 't4', 16),
        'mesc:',                             # ESC .. 'C': bytes, no width
        ('lbu', 'v1', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('beqz', 'v1', 'mdone'),
        ('addiu', 't5', 't5', 1),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 'v1', 'at', 'mesc'),
        ('nop',),
        ('b', 'mloop'),
        ('addiu', 't5', 't5', 1),
        'mdone:',
        ('bnez', 't1', 'setline'),
        ('addu', 't2', 't6', 'a3'),
        ('addu', 't2', 't2', 't4'),
        ('slt', 'at', 't9', 't2'),
        ('bnez', 'at', 'brk'),
        ('addu', 'a2', 't7', 't5'),
        ('slt', 'at', 'a2', 't0'),
        ('beqz', 'at', 'brk'),
        ('addiu', 't7', 'a2', 1),
        ('b', 'copy'),
        ('addu', 't6', 't2', 'zero'),
        'brk:',
        ('addiu', 'v0', 'zero', 0x0A),
        'setline:',
        ('addu', 't6', 't4', 'zero'),
        ('bnez', 't1', 'loop'),
        ('addu', 't7', 't5', 'zero'),
        ('b', 'copy'),
        ('nop',),
        'end:',
        ('jr', 'ra'),
        ('sb', 'zero', 0, 'a1'),
    ])


def line_cells(at):
    """v0 = widest line px so far, a1 = this line's px; returns ceil(v0 / 8). Bytes are measured
    with their low 7 bits (Shift-JIS is rare in English lines and only approximated); < 0x20
    (ESC, TAB) no width."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('addu', 'v0', 'zero', 'zero'),
        ('addu', 'a1', 'zero', 'zero'),
        'loop:',
        ('lbu', 'v1', 0, 'a0'),
        ('addiu', 'a0', 'a0', 1),
        ('beqz', 'v1', 'eol'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 'v1', 'at', 'eol'),
        ('andi', 't2', 'v1', 0x7F),
        ('sltiu', 'at', 't2', 0x20),
        ('bnez', 'at', 'loop'),
        ('lui', 't3', hi(WIDTHS - 0x20)),
        ('addu', 't2', 't2', 't3'),
        ('lbu', 't2', lo(WIDTHS - 0x20), 't2'),
        ('b', 'loop'),
        ('addu', 'a1', 'a1', 't2'),
        'eol:',
        ('slt', 'at', 'v0', 'a1'),
        ('beqz', 'at', 'keep'),
        ('nop',),
        ('addu', 'v0', 'a1', 'zero'),
        'keep:',
        ('bnez', 'v1', 'loop'),
        ('addu', 'a1', 'zero', 'zero'),
        ('addiu', 'a1', 'v0', 7 + CELL_PAD),
        ('jr', 'ra'),
        ('srl', 'v0', 'a1', 3),
    ])


def centre_offset(at):
    """Tail of 0x800283c4 after its line_cells call (s1 = box width, s0 = 2 centre / 1 right):
    centred text uses the real px (a1 - 7 - CELL_PAD) instead of cells * 8, which put 5-6 letter
    button labels against the left border; right-aligned text keeps cells * 8. Offset < 0 -> 0."""
    return asm.assemble(at, [
        ('sll', 'v0', 'v0', 3),
        ('bnez', 't0', 'div'),
        ('subu', 'v0', 's1', 'v0'),
        ('addiu', 'v0', 'a1', -(7 + CELL_PAD)),
        ('subu', 'v0', 's1', 'v0'),
        'div:',
        ('div', 'v0', 's0'),
        ('mflo', 'v0'),
        ('bgtz', 'v0', 'out'),
        ('nop',),
        ('addu', 'v0', 'zero', 'zero'),
        'out:',
    ])


def line_patches(exe):
    assert word(exe, BREAK) == 0x27bdffe8 and word(exe, BREAK + 8) == 0xafbf0010, 'line breaker'
    code = break_lines(BREAK)
    assert BREAK + len(code) <= BREAK_END, 'break_lines too long (%d bytes)' % len(code)
    put(exe, BREAK, code + b'\0' * (BREAK_END - BREAK - len(code)))
    assert word(exe, CELLS) == 0x00001021 and word(exe, CELLS_END - 4) == 0, 'longest line'
    code = line_cells(CELLS)
    assert CELLS + len(code) <= CELLS_END, 'line_cells too long (%d bytes)' % len(code)
    put(exe, CELLS, code + b'\0' * (CELLS_END - CELLS - len(code)))
    assert word(exe, CENTRE[0]) == 0x0c00a14b and word(exe, CENTRE[1]) == 0x000210c0, 'centre offset'
    put(exe, CENTRE[0] + 4, asm.assemble(CENTRE[0] + 4, [('addiu', 't0', 's0', -2)]))
    code = centre_offset(CENTRE[1])
    assert CENTRE[1] + len(code) <= CENTRE[2], 'centre_offset too long'
    put(exe, CENTRE[1], code + asm.assemble(0, [('nop',)]) * ((CENTRE[2] - CENTRE[1] - len(code)) // 4))
    assert word(exe, EVENT_LINES[0]) == EVENT_LINES[1], 'event dialogue lines'
    put(exe, EVENT_LINES[0], struct.pack('<I', EVENT_LINES[1] - 1))
    assert word(exe, EVENT_WIDTH[0]) == EVENT_WIDTH[1], 'event dialogue width'
    put(exe, EVENT_WIDTH[0], struct.pack('<I', EVENT_WIDTH[1] | CAPPED))


def apply(exe):
    """Patch the program (bytearray) in place."""
    codes = struct.unpack_from('<%dH' % HALF_N, exe, HALF_CODES - EXE_BASE)
    assert codes[0] == 0x20 and codes[64] == 0xA0, 'half-width code table'
    assert word(exe, DRAW_HALF) == 0x306200ff and word(exe, DRAW_RET) == 0x24020001, 'draw_char'
    g = pixfont.glyphs()
    glyphs = b''.join(g[chr(c)][0] for c in ASCII)
    widths = bytes(g[chr(c)][1] for c in ASCII)
    assert max(widths) <= 8
    put(exe, HALF_GLYPHS, glyphs + widths + bytes([DEFAULT_W]))
    global SNAP
    code = draw_half()
    SNAP = DRAW_HALF + len(code)
    code += snap(SNAP)
    assert DRAW_HALF + len(code) <= DRAW_RET, 'draw_char branch + snap too long (%d bytes)' % len(code)
    put(exe, DRAW_HALF, code + b'\0' * (DRAW_RET - DRAW_HALF - len(code)))
    code = hook()
    assert PUTC_SINGLE + len(code) <= PUTC_END, 'putc hook too long (%d bytes)' % len(code)
    put(exe, PUTC_SINGLE, code + b'\0' * (PUTC_END - PUTC_SINGLE - len(code)))
    fix_blit(exe)
    wrap_hook(exe)
    line_patches(exe)


if __name__ == '__main__':
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)
    SNAP = DRAW_HALF + len(draw_half())
    for at, code in ((DRAW_HALF, draw_half()), (SNAP, snap(SNAP)), (PUTC_SINGLE, hook()),
                     (WRAPPER, wrapper(WRAPPER)), (STUB, start_stub(STUB)),
                     (BREAK, break_lines(BREAK)), (CELLS, line_cells(CELLS))):
        print('%d bytes' % len(code))
        for i in md.disasm(code, at):
            print('%08x  %-8s %s' % (i.address, i.mnemonic, i.op_str))
