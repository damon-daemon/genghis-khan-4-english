"""Chunk files for the progstr translation workers: notes/chunks/pNN.txt.

Line: key<TAB>room<TAB>F/M<TAB>jp px<TAB>Japanese; hint lines '# gl: English' (notes/glossary.txt) and
'# msg: English' (a MESSAGE.CK segment with the same Japanese, already translated).
usage: pchunks.py NN FIRST_KEY LAST_KEY   (keys inclusive, address order)
"""
import os, re, sys, unicodedata
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import progstr, tl, text

ROOT = progstr.ROOT


def norm(s):
    s = re.sub(r'\{[0-9a-f]+\}|\\n|[\s　]', '', s)
    return unicodedata.normalize('NFKC', s)


def hints():
    gl = {}
    for ln in open(os.path.join(ROOT, 'notes', 'glossary.txt'), encoding='utf-8'):
        f = ln.rstrip('\n').split('\t')
        if len(f) >= 2 and not ln.startswith('#'):
            gl.setdefault(norm(f[0]), f[1])
    msg = {}
    blocks, tls = tl.load_blocks(), tl.load_tls()
    for b in range(len(blocks)):
        jps = tl.jp_block(blocks, b)
        for k, en in tls.get(b, {}).items():
            if k in jps:
                j = norm(text.seg_str(jps[k]))
                if j and en != '~':
                    msg.setdefault(j, en)
    return gl, msg


def main():
    nn, lo, hi = sys.argv[1:4]
    gl, msg = hints()
    out = []
    for r in progstr.rows():
        if not r['ui'] or progstr._order(r['key']) < progstr._order(lo) or progstr._order(r['key']) > progstr._order(hi):
            continue
        out.append('%s\t%d\t%s\t%d\t%s' % (r['key'], r['room'], 'F' if r['block'] else 'M',
                                           progstr.px(r['jp'], True), r['jp'].replace('\n', '\\n')))
        j = norm(r['jp'])
        if j in gl:
            out.append('# gl: ' + gl[j])
        elif j in msg and len(msg[j]) < 80:
            out.append('# msg: ' + msg[j])
    path = os.path.join(ROOT, 'notes', 'chunks', 'p%s.txt' % nn)
    open(path, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    print(path, sum(not l.startswith('#') for l in out), 'strings', sum(l.startswith('#') for l in out), 'hints')


if __name__ == '__main__':
    main()
