"""Dump the officer-name piece tables of TURN (notes/namegen.md): table, entry key, JP, English.
usage: .venv/bin/python tools/dev/pieces.py > notes/namegen_pieces.txt"""
import collections, glob, os, struct
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
t = open(os.path.join(ROOT, 'extract/BIN/TURN.BIN'), 'rb').read()
p = open(os.path.join(ROOT, 'extract/SLPM_861.75'), 'rb').read()
tl = {}
for f in glob.glob(os.path.join(ROOT, 'tl/progstr/*.txt')):
    for l in open(f, encoding='utf-8'):
        k, _, v = l.rstrip('\n').partition('\t')
        tl[k] = v


def key(a):
    return ('TURN:%08x' if a >= 0x80190000 else 'PROG:%08x') % a


def jp(a):
    b, o = (t, a - 0x80190000) if a >= 0x80190000 else (p, a - 0x80010000 + 0x800)
    return b[o:b.index(b'\0', o)].decode('cp932')


tabs, users = [], collections.defaultdict(set)
for i in range(0x22c88, 0x22e00, 8):
    n, ptr = struct.unpack_from('<II', t, i)
    ents = [struct.unpack_from('<I', t, ptr - 0x80190000 + 4 * j)[0] for j in range(n)]
    tabs.append((0x80190000 + i, ents))
    for a in ents:
        users[a].add(0x80190000 + i)
for ta, ents in tabs:
    print('## T%03x (%d)' % (ta & 0xfff, len(ents)))
    for a in ents:
        sh = ' shared:' + ','.join('T%03x' % (u & 0xfff) for u in sorted(users[a]) if u != ta) if len(users[a]) > 1 else ''
        print('%s\t%s\t%r%s' % (key(a), jp(a), tl.get(key(a)), sh))
