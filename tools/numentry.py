"""Number-entry dialog (L1 MAX / R1 MIN): digit cursor pitch (issue #1).

The dialog draws its digit cursor (8-px box, rect draw 0x8001b938: a3 = x, 0x10(sp) = y 8,
0x14(sp) = h 9) at x = 0x30 + 8 * digit, the Japanese half-width digit pitch. The English font
draws digits DIGIT_W + SPACING = 6 px apart (tools/pixfont.py), so the cursor drifted right of the
digit it edits. Now x = 0x30 + 6 * digit; spare load/branch delay slots hold the extra
instructions (same code and fix as the Reppuuden translation, RT1 0x8015a0a4).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asm

EXE_BASE = 0x80010000 - 0x800

# (va, clean word, new instruction)
PATCHES = [
    (0x800582e4, 0x000210c0, ('sll', 's1', 'v0', 1)),        # sll v0,v0,3      -> s1 = 2k
    (0x800582ec, 0x00000000, ('addu', 's1', 's1', 'v0')),    # nop (load delay) -> s1 = 3k
    (0x800582f4, 0x24510030, ('addiu', 's1', 's1', 0x18)),   # addiu s1,v0,0x30 (jalr delay slot)
    (0x80058318, 0x02203821, ('addu', 'a3', 's1', 's1')),    # move a3,s1       -> x = 0x30 + 6k
]


def apply(exe):
    """Patch the program (bytearray) in place; returns the number of instructions."""
    for va, old, new in PATCHES:
        o = va - EXE_BASE
        assert struct.unpack_from('<I', exe, o)[0] == old, 'numentry %08x' % va
        exe[o:o + 4] = asm.assemble(va, [new])
    return len(PATCHES)
