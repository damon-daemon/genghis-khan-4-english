"""Latin letters in the name entry screen (notes/tl_plan.md "Name entry").

One screen (program code, object a0/s0) names a new city (field 20 bytes) or a newborn child
(16 bytes). Modes (obj+0x220): 4 = kanji reading index (grid 0x800f01d8, the opening mode),
1 = kanji list, 2 = hiragana 0x800f0240, 3 = katakana 0x800f0300, 5 = symbols 0x800f03c0
(grids = 2 bytes per cell, 10 x 5 cells a page; counts are the bytes "2__\\x1e" at 0x800f1294).
Buttons 0x2711-0x2714 pick modes 4/2/3/5 (labels Kanji/Hira/Kata/Sym, PROG 0x800f0188..).

  - the katakana grid becomes A-Z ' - . and '_' (= space), ASCII cells "X\\0" (full-width
    letters draw as blank boxes); the screen opens on it (mode 3 instead of 4); button 'ABC';
  - append (0x800bf720, always 2 bytes) is rewritten: an ASCII cell is stored as one byte,
    upper case at the start and after a space, lower case otherwise; a 2-byte cell is
    stored only when both bytes fit (the game checked len < max only);
  - backspace (0x800bf7b0, always 2 bytes) cuts the last character (1 or 2 bytes); its caller
    acts from length 1 instead of 2.
Both routines are rewritten in place (0x800bf720-0x800bf834, one caller each).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm

EXE_BASE = 0x80010000 - 0x800
GRID, GRID_END = 0x800f0300, 0x800f03bf          # katakana grid (95 cells) + NUL
COUNT = 0x800f1296                                # its cell count byte (0x5f)
INIT_MODE = 0x800c00f8                            # addiu v0, zero, 4
CODE, CODE_END = 0x800bf720, 0x800bf834           # old append + backspace
APPEND_CALL, BS_CALL, BS_CHECK = 0x800c1070, 0x800c1238, 0x800c122c
REDRAW = 0x800bf6cc
ROWS = ['ABCDEFGHIJ', 'KLMNOPQRST', "UVWXYZ'-._"]
ORIG = {INIT_MODE: 0x24020004, CODE: 0x248501fa, 0x800bf7b0: 0x27bdffe8,
        APPEND_CALL: 0x0c02fdc8, BS_CALL: 0x0c02fdec, BS_CHECK: 0x2c410002, COUNT & ~3: 0x1e5f5f32}


def grid_bytes():
    cells = ''.join(ROWS)
    return b''.join(c.encode() + b'\0' for c in cells), len(cells)


def routines():
    """append(a0 = obj) and bs(a0 = obj); both tail-jump to the name redraw (a0 = obj)."""
    return [
        'append:',
        ('lw', 't0', 0x12f4, 'a0'),           # page
        ('addiu', 't1', 'zero', 100),         # 50 cells of 2 bytes a page
        ('multu', 't0', 't1'),
        ('lw', 't1', 0x12ec, 'a0'),           # cursor
        ('lw', 't0', 0x12e8, 'a0'),           # grid
        ('sll', 't1', 't1', 1),
        ('mflo', 't2'),
        ('addu', 't0', 't0', 't2'),
        ('addu', 't0', 't0', 't1'),
        ('lbu', 't2', 0, 't0'),
        ('lbu', 't3', 1, 't0'),
        ('lw', 't4', 0x210, 'a0'),            # length
        ('lw', 't5', 0x214, 'a0'),            # max
        ('addiu', 'a1', 'a0', 0x1fa),         # name
        ('sltiu', 'at', 't2', 0x80),
        ('bnez', 'at', 'ascii'),
        ('addiu', 't6', 't4', 2),
        ('slt', 'at', 't5', 't6'),            # 2-byte character: both bytes must fit
        ('bnez', 'at', 'done'),
        ('addu', 't6', 'a1', 't4'),
        ('sb', 't2', 0, 't6'),
        ('sb', 't3', 1, 't6'),
        ('b', 'term'),
        ('addiu', 't4', 't4', 2),
        'ascii:',
        ('addiu', 't6', 't2', -0x5f),         # '_' cell = space (t6 = '_' - 'A' = 30: no letter)
        ('bnez', 't6', 'case'),
        ('addiu', 't6', 't2', -0x41),
        ('addiu', 't2', 'zero', 0x20),
        'case:',
        ('sltiu', 'at', 't6', 26),
        ('beqz', 'at', 'one'),
        ('nop',),
        ('beqz', 't4', 'one'),                # upper case first and after a space
        ('addu', 't6', 'a1', 't4'),
        ('lbu', 't6', -1, 't6'),
        ('addiu', 't7', 'zero', 0x20),
        ('beq', 't6', 't7', 'one'),
        ('nop',),
        ('ori', 't2', 't2', 0x20),
        'one:',
        ('slt', 'at', 't4', 't5'),
        ('beqz', 'at', 'done'),
        ('addu', 't6', 'a1', 't4'),
        ('sb', 't2', 0, 't6'),
        ('addiu', 't4', 't4', 1),
        'term:',
        ('addu', 't6', 'a1', 't4'),
        ('sb', 'zero', 0, 't6'),
        ('sw', 't4', 0x210, 'a0'),
        'done:',
        ('j', REDRAW),
        ('nop',),

        'bs:',                                # cut the last character (1 or 2 bytes); length >= 1
        ('addiu', 'a1', 'a0', 0x1fa),
        ('lw', 't4', 0x210, 'a0'),
        ('addu', 't0', 'zero', 'zero'),
        'bl:',
        ('addu', 't1', 't0', 'zero'),         # start of the last character
        ('addu', 't2', 'a1', 't0'),
        ('lbu', 't2', 0, 't2'),
        ('addiu', 't0', 't0', 1),
        ('addiu', 't3', 't2', -0x81),         # Shift-JIS lead byte: 81-9f, e0-fc
        ('sltiu', 'at', 't3', 0x1f),
        ('bnez', 'at', 'two'),
        ('addiu', 't3', 't2', -0xe0),
        ('sltiu', 'at', 't3', 0x1d),
        ('beqz', 'at', 'next'),
        ('nop',),
        'two:',
        ('addiu', 't0', 't0', 1),
        'next:',
        ('slt', 'at', 't0', 't4'),
        ('bnez', 'at', 'bl'),
        ('addu', 't2', 'a1', 't1'),
        ('sb', 'zero', 0, 't2'),
        ('j', REDRAW),
        ('sw', 't1', 0x210, 'a0'),
    ]


def _word(exe, va):
    return struct.unpack_from('<I', exe, va - EXE_BASE)[0]


def _put(exe, va, data, end=None):
    assert end is None or va + len(data) <= end, '%08x overflow' % va
    exe[va - EXE_BASE:va - EXE_BASE + len(data)] = data


def apply(exe):
    """Patch the program (bytearray) in place."""
    for va, w in ORIG.items():
        assert _word(exe, va) == w, 'nameentry %08x' % va
    L = routines()
    code = asm.assemble(CODE, L)
    _put(exe, CODE, code.ljust(CODE_END - CODE, b'\0'), CODE_END)
    lab = asm.labels(CODE, L)
    grid, n = grid_bytes()
    _put(exe, GRID, grid.ljust(GRID_END - GRID, b'\0'), GRID_END)
    exe[COUNT - EXE_BASE] = n
    _put(exe, INIT_MODE, asm.assemble(INIT_MODE, [('addiu', 'v0', 'zero', 3)]))
    _put(exe, APPEND_CALL, asm.assemble(APPEND_CALL, [('jal', lab['append'])]))
    _put(exe, BS_CALL, asm.assemble(BS_CALL, [('jal', lab['bs'])]))
    _put(exe, BS_CHECK, asm.assemble(BS_CHECK, [('sltiu', 'at', 'v0', 1)]))
    return len(code)
