"""Name generator patches (notes/namegen.md).

EXE:
  - name setters (plain strcpy into a 17-byte name field): SET_NAME 0x80079d08 (officer, person+0x1a),
    0x800b79b0 (consort, +2), 0x8006e094 (record +0, gets the same generated consort name)
    -> bounded copy: at most 16 characters + NUL.
  - BASE 0x800a4078 (appends `count` random katakana as raw Shift-JIS) -> random romaji syllables
    (onset from ONSETS + vowel), at most 2 * count + 1 characters (about the Japanese byte length),
    first letter capitalised when it starts the string or follows a space.
TURN (overlay, code only; its strings are progstr's):
  - the officer generator's setter call 0x801aa714 -> NORM.
  - Japan generator: a child takes the father's surname by Shift-JIS byte slicing (first 1 or 2
    kanji) -> father's name up to and including its first space (no space: random surname).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm

EXE_BASE = 0x80010000 - 0x800
OVL_BASE = 0x80190000
STRCPY, STRCAT, RAND = 0x80035040, 0x80034f20, 0x80089c24

SET_NAME, SET_NAME_END = 0x80079d08, 0x80079d48
SETTERS = (SET_NAME, 0x800b79b0, 0x8006e094)            # identical 0x40-byte functions
NAME_MAX = 16

BASE, BASE_END = 0x800a4078, 0x800a4220
BASE_CODE = 68 * 4
NORM = BASE + BASE_CODE
SYLLABLES, SYLLABLES_END = 0x800eede4, 0x800eee28     # old katakana table (0x44 B)
ONSETS = ['', 'l', 'k', 's', 't', 'n', 'h', 'm', 'r', 'g', 'd', 'b', 'sh', 'ch', 'j', 'z']
VOWELS = b'aeiou'

JP_SURNAME, JP_SURNAME_END = 0x801aa894, 0x801aa91c   # after strcpy(sp+0x20, father's name)
JP_FALLBACK, JP_GIVEN = 0x801aa958, 0x801aa920        # random surname / append the given name
GEN_SET = 0x801aa714                                  # jal SET_NAME(person name, generated name)


def set_name(at=SET_NAME):
    return asm.assemble(at, [
        ('addu', 'v0', 'a0', 'zero'),
        ('beqz', 'a1', 'end'),
        ('addiu', 't1', 'a0', NAME_MAX),
        'copy:',
        ('lbu', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('beq', 'a0', 't1', 'end'),
        ('nop',),
        ('sb', 't0', 0, 'a0'),
        ('bnez', 't0', 'copy'),
        ('addiu', 'a0', 'a0', 1),
        ('jr', 'ra'),
        ('nop',),
        'end:',
        ('jr', 'ra'),
        ('sb', 'zero', 0, 'a0'),
    ])


def base():
    """BASE(ctx, out, count, flag); onsets (2-byte cells) and vowels at SYLLABLES."""
    cons = SYLLABLES
    vow = cons + 2 * len(ONSETS)
    hi, lo = asm.hi, asm.lo
    code = asm.assemble(BASE, [
        ('addiu', 'sp', 'sp', -0x30),
        ('sw', 'ra', 0x24, 'sp'),
        ('sw', 's0', 0x10, 'sp'),
        ('sw', 's1', 0x14, 'sp'),
        ('sw', 's2', 0x18, 'sp'),
        ('sw', 's3', 0x1c, 'sp'),
        ('sw', 'a1', 0x20, 'sp'),
        ('addu', 's1', 'a2', 'zero'),
        ('addu', 's0', 'a1', 'zero'),
        'end:',                                  # s0 = end of the string so far
        ('lbu', 't0', 0, 's0'),
        ('addiu', 's0', 's0', 1),
        ('bnez', 't0', 'end'),
        ('nop',),
        ('addiu', 's0', 's0', -1),
        ('addu', 's2', 's0', 'zero'),            # s2 = start of our part
        ('sll', 't0', 's1', 1),
        ('addu', 's3', 's2', 't0'),
        ('addiu', 's3', 's3', 1),                # s3 = limit for the end pointer
        'loop:',
        ('beqz', 's1', 'done'),
        ('addiu', 's1', 's1', -1),
        ('jal', RAND),
        ('addiu', 'a0', 'zero', len(ONSETS)),
        ('lui', 't1', hi(cons)),
        ('addiu', 't1', 't1', lo(cons)),
        ('sll', 't0', 'v0', 1),
        ('addu', 't1', 't1', 't0'),
        ('lbu', 't0', 0, 't1'),
        ('lbu', 't2', 1, 't1'),
        ('addu', 't3', 's0', 'zero'),
        ('sb', 't0', 0, 't3'),
        ('sltu', 'at', 'zero', 't0'),
        ('addu', 't3', 't3', 'at'),
        ('sb', 't2', 0, 't3'),
        ('sltu', 'at', 'zero', 't2'),
        ('addu', 't3', 't3', 'at'),
        ('sw', 't3', 0x28, 'sp'),
        ('jal', RAND),
        ('addiu', 'a0', 'zero', len(VOWELS)),
        ('lw', 't3', 0x28, 'sp'),
        ('lui', 't1', hi(vow)),
        ('addu', 't1', 't1', 'v0'),
        ('lbu', 't0', lo(vow), 't1'),
        ('addiu', 't4', 't3', 1),
        ('sb', 't0', 0, 't3'),
        ('sltu', 'at', 's3', 't4'),
        ('bnez', 'at', 'skip'),                  # too long: drop this syllable
        ('nop',),
        ('addu', 's0', 't4', 'zero'),
        'skip:',
        ('b', 'loop'),
        ('sb', 'zero', 0, 's0'),
        'done:',                                 # capitalise at the string start or after a space
        ('lw', 't3', 0x20, 'sp'),
        ('lbu', 'v0', 0, 's2'),
        ('beq', 's2', 't3', 'cap'),
        ('lbu', 't0', -1, 's2'),
        ('addiu', 't1', 'zero', 0x20),
        ('bne', 't0', 't1', 'ret'),
        ('nop',),
        'cap:',
        ('sltiu', 'at', 'v0', 0x61),
        ('bnez', 'at', 'ret'),
        ('addiu', 'v0', 'v0', -0x20),
        ('sb', 'v0', 0, 's2'),
        'ret:',
        ('lw', 'ra', 0x24, 'sp'),
        ('lw', 's0', 0x10, 'sp'),
        ('lw', 's1', 0x14, 'sp'),
        ('lw', 's2', 0x18, 'sp'),
        ('lw', 's3', 0x1c, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 0x30),
    ])
    assert len(code) <= BASE_CODE, len(code)
    return code.ljust(BASE_CODE, b'\0')


def syllables():
    out = b''.join(o.encode().ljust(2, b'\0') for o in ONSETS) + VOWELS
    assert SYLLABLES + len(out) <= SYLLABLES_END
    return out.ljust(SYLLABLES_END - SYLLABLES, b'\0')


def norm():
    """NORM(dest, src): fix the case of concatenated pieces in src, shorten, then j SET_NAME."""
    return asm.assemble(NORM, [
        ('addu', 't5', 'a1', 'zero'),
        ('lbu', 't0', 0, 't5'),
        'case:',                                 # t0 = previous char, t1 = this one
        ('addiu', 't5', 't5', 1),
        ('lbu', 't1', 0, 't5'),
        ('sltiu', 't2', 't0', 0x61),             # previous not lowercase
        ('beqz', 't1', 'length'),
        ('addiu', 't3', 't1', -0x41),
        ('bnez', 't2', 'next'),
        ('sltiu', 't3', 't3', 26),               # this one uppercase
        ('beqz', 't3', 'next'),
        ('nop',),
        ('addiu', 't1', 't1', 0x20),
        ('sb', 't1', 0, 't5'),
        'next:',
        ('b', 'case'),
        ('addu', 't0', 't1', 'zero'),
        'length:',
        ('subu', 't3', 't5', 'a1'),
        ('sltiu', 't3', 't3', NAME_MAX + 1),
        ('bnez', 't3', 'set'),
        ('addu', 't5', 'a1', 'zero'),
        'space:',                                # first space after the first letter
        ('addiu', 't5', 't5', 1),
        ('lbu', 't0', 0, 't5'),
        ('addiu', 't2', 'zero', 0x20),
        ('beqz', 't0', 'set'),                   # one word: SET_NAME cuts it
        ('nop',),
        ('bne', 't0', 't2', 'space'),
        ('nop',),
        ('addiu', 't2', 'zero', 0x2e),           # "X." + the rest from the space
        ('sb', 't2', 1, 'a1'),
        ('addiu', 't4', 'a1', 2),
        'move:',
        ('lbu', 't0', 0, 't5'),
        ('addiu', 't5', 't5', 1),
        ('sb', 't0', 0, 't4'),
        ('bnez', 't0', 'move'),
        ('addiu', 't4', 't4', 1),
        'set:',
        ('j', SET_NAME),
        ('nop',),
    ])


def jp_surname():
    return asm.assemble(JP_SURNAME, [
        ('addiu', 't0', 'sp', 0x20),
        'scan:',
        ('lbu', 't1', 0, 't0'),
        ('addiu', 't2', 'zero', 0x20),
        ('beqz', 't1', JP_FALLBACK),             # no space: random surname
        ('nop',),
        ('beq', 't1', 't2', 'found'),
        ('nop',),
        ('b', 'scan'),
        ('addiu', 't0', 't0', 1),
        'found:',
        ('addiu', 't3', 'sp', 0x20),
        ('beq', 't0', 't3', JP_FALLBACK),
        ('nop',),
        ('sb', 'zero', 1, 't0'),                 # keep "Surname "
        ('addu', 'a0', 's0', 'zero'),
        ('jal', STRCPY),
        ('addiu', 'a1', 'sp', 0x20),
        ('b', JP_GIVEN),
        ('nop',),
    ])


def put(buf, base, va, data, end=None):
    assert end is None or va + len(data) <= end
    buf[va - base:va - base + len(data)] = data


def word(buf, base, va):
    return struct.unpack_from('<I', buf, va - base)[0]


def apply(exe, turn):
    """Patch the program and the TURN overlay (bytearrays) in place."""
    assert word(exe, EXE_BASE, SET_NAME) == 0x27bdffe8 and word(exe, EXE_BASE, BASE) == 0x27bdffc0, 'EXE'
    assert word(turn, OVL_BASE, JP_SURNAME) == 0x0c00d424 and word(turn, OVL_BASE, JP_FALLBACK) == 0x3c04801b, 'TURN'
    orig = exe[SET_NAME - EXE_BASE:SET_NAME_END - EXE_BASE]
    for at in SETTERS:
        n = SET_NAME_END - SET_NAME
        assert exe[at - EXE_BASE:at - EXE_BASE + n - 8] == orig[:n - 8], 'setter %08x' % at
        put(exe, EXE_BASE, at, set_name(at).ljust(n, b'\0'), at + n)
    assert word(exe, EXE_BASE, SYLLABLES) == 0x45434193, 'kana table'
    code = norm()
    put(exe, EXE_BASE, BASE, base() + code + bytes(BASE_END - NORM - len(code)), BASE_END)
    put(exe, EXE_BASE, SYLLABLES, syllables(), SYLLABLES_END)
    assert word(turn, OVL_BASE, GEN_SET) == 0x0c01e742, 'TURN setter call'
    put(turn, OVL_BASE, GEN_SET, asm.assemble(GEN_SET, [('jal', NORM)]))
    code = jp_surname()
    put(turn, OVL_BASE, JP_SURNAME, code.ljust(JP_SURNAME_END - JP_SURNAME, b'\0'), JP_SURNAME_END)
