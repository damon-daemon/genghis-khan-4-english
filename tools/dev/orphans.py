"""List dialogue segments whose last box page holds a short orphan line (one or two words).

  orphans.py [MAXPX]   -> per block: '@id' + the English, '# orphan: <last line>' (default MAXPX 80)
The box pages every PAGE lines; a segment whose wrapped page has 3k+1 lines shows its last line alone.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import text, tl


def orphans(maxpx=80):
    out = []
    for b, recs in sorted(tl.load_tls().items()):
        bx = tl.box(b)
        if b == 0 or b in text.NOPAGE:
            continue
        for k, en in sorted(recs.items()):
            for pg in text.wrap(en, *bx[:1], *bx[2:]):
                if len(pg) > 1 and len(pg) % bx[1] == 1 and text.word_px(pg[-1]) < maxpx:
                    out.append((b, k, en, pg[-1]))
                    break
    return out


if __name__ == '__main__':
    found = orphans(int(sys.argv[1]) if len(sys.argv) > 1 else 80)
    last = None
    for b, k, en, ln in found:
        if b != last:
            print('### block %02d' % b)
            last = b
        print('@%d%s\n%s\n# orphan: %s\n' % (k[0], '.%d' % k[1] if k[1] else '', en, ln))
    print('# %d segments' % len(found))
