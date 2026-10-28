"""Translation files for DATA/MESSAGE.CK: show, apply, check, stats; used by build.py.

tl/msg/NN.txt holds the English (no Japanese) of block NN, one record per segment (text.py):

    # comment (a line starting with #)
    @<id>            segment 0 of message <id> (= block * 1000 + index)
    @<id>.<n>        segment n (a record holds alternatives / switch cases, one segment each)
    English text, possibly over several physical lines (joined with spaces).

Inline syntax: see text.py ({C6} calls, {021e0203} names/numbers, \\n, \\p, \\s, ~ = empty).
The game wraps nothing by itself: build.py wraps every block but block 0 (its entries are words
inserted into other sentences) at the block's box width (text.BOX, default 224 px x 3 lines).

Translation memory: a segment without English takes the English of a segment with the same
Japanese (explicit English always wins).

usage:
    tl.py show  NN [from] [to] [--todo]   # Japanese segments (from/to = record indexes)
    tl.py apply NN batch.txt              # validate a batch, merge it into tl/msg/NN.txt
    tl.py check [NN]                      # validate everything in tl/ (and the block sizes)
    tl.py stats
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
import msgfile, setup, text

TL_MSG = os.path.join(setup.TL, 'msg')
_REC = re.compile(r'@(\d+)(?:\.(\d+))?\s*$')


def load_blocks():
    """Japanese record lists (from extract/ or the clean disc)."""
    if os.path.exists(msgfile.JP) and os.path.exists(msgfile.EXE):
        return [msgfile.records(b) for b in msgfile.blocks()]
    import iso
    disc = iso.Disc(setup.CLEAN)
    recs = disc.records()
    return [msgfile.records(b) for b in msgfile.blocks(disc.file(msgfile.NAME, recs),
                                                        disc.file(iso.EXE_NAME, recs))]


def jp_block(blocks, b):
    """{(id, n): segment tokens} of block b."""
    out = {}
    for i, r in enumerate(blocks[b]):
        for n, seg in enumerate(text.segments(r)):
            out[(b * 1000 + i, n)] = seg
    return out


def box(b):
    return None if b == 0 else text.BOX.get(b, (text.WRAP_PX, text.PAGE))


# ---------------------------------------------------------------- tl files
def tl_path(b):
    return os.path.join(TL_MSG, '%02d.txt' % b)


def parse_tl(path):
    """-> {(id, n): English}"""
    out, key, buf = {}, None, []

    def flush():
        if key is not None:
            out[key] = ' '.join(x.strip() for x in buf if x.strip())
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if ln.startswith('#'):
            continue
        m = _REC.match(ln)
        if m:
            flush()
            key, buf = (int(m.group(1)), int(m.group(2) or 0)), []
            if key in out:
                raise ValueError('%s: @%d.%d twice' % (path, *key))
        elif key is not None:
            buf.append(ln)
        elif ln.strip():
            raise ValueError('%s: text before the first @ line: %r' % (path, ln))
    flush()
    return out


def write_tl(b, recs):
    os.makedirs(TL_MSG, exist_ok=True)
    with open(tl_path(b), 'w', encoding='utf-8') as f:
        for (mid, n) in sorted(recs):
            f.write('@%d%s\n%s\n\n' % (mid, '.%d' % n if n else '', recs[(mid, n)]))


def load_tls(only=None):
    return {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLK) if only is None or b == only}


def memory(jps, tls):
    """{Japanese string: English} from every explicit segment."""
    tm = {}
    for b, recs in tls.items():
        for k, en in recs.items():
            if k in jps.get(b, {}):
                tm.setdefault(text.seg_str(jps[b][k]), en)
    return tm


def warn(b, k, toks, en):
    out = []
    if en == '~':
        return out
    bx = box(b)
    if bx:
        lines, pages = text.count(en, bx)
        jl = text.seg_str(toks).replace('\\n\\n', '\\n').count('\\n') + 1
        if b in text.NOPAGE and lines > bx[1]:
            out.append('%d lines > %d (box does not page)' % (lines, bx[1]))
        elif pages > -(-jl // bx[1]) + 1:
            out.append('%d pages (Japanese %d lines)' % (pages, jl))
    if text.expanded(en) > text.EXPAND_MAX:
        out.append('~%d bytes printed > %d' % (text.expanded(en), text.EXPAND_MAX))
    return ['@%d.%d: %s' % (*k, w) for w in out]


# ---------------------------------------------------------------- build
def english_blocks(blocks, tls=None):
    """Japanese record lists -> (record lists with the English segments, translated count, errors)."""
    tls = tls if tls is not None else load_tls()
    jps = {b: jp_block(blocks, b) for b in range(len(blocks))}
    tm = memory(jps, tls)
    out, errs, done = [], [], 0
    for b, recs in enumerate(blocks):
        en_b = tls.get(b, {})
        errs += ['@%d.%d: no such segment' % k for k in en_b if k not in jps[b]]
        new = []
        for i, r in enumerate(recs):
            its = text.items(r)
            if its is None:
                new.append(r); continue
            n = 0
            for j, (kind, v) in enumerate(its):
                if kind != 'seg':
                    continue
                key = (b * 1000 + i, n)
                n += 1
                en = en_b.get(key, tm.get(text.seg_str(v)))
                if en is None:
                    continue
                es = text.check(v, en)
                if es:
                    errs += ['@%d.%d: %s' % (*key, e) for e in es]
                    continue
                its[j] = ('code', text.encode(en, box(b)))
                done += 1
            new.append(text.join(its))
        out.append(new)
    return out, done, errs


# ---------------------------------------------------------------- commands
def show(b, a=0, z=None, todo=False):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    tls = load_tls()
    recs = tls[b]
    tm = memory({c: jp_block(blocks, c) for c in tls if tls[c]}, tls)
    calls = set()
    for (mid, n), toks in sorted(jp.items()):
        i = mid % 1000
        if i < a or (z is not None and i > z):
            continue
        s = text.seg_str(toks)
        if todo and ((mid, n) in recs or s in tm):
            continue
        print('@%d%s\n%s\n' % (mid, '.%d' % n if n else '', s))
        calls.update(int(m.group(1)) for m in re.finditer(r'\{C(\d+)\}', s))
    if calls:
        print('# calls:')
        for c in sorted(calls):
            print('#   {C%d} = %s' % (c, legend(blocks, tls, c, tm=tm)[:240]))


def legend(blocks, tls, c, depth=0, tm=None):
    """What call c prints: its alternatives (English if translated, else Japanese); a record without
    text of its own shows where it branches (-> C4 = ...)."""
    b = c // 1000
    segs = jp_block(blocks, b)
    en = tls.get(b, {})
    if tm is None:
        tm = memory({c: jp_block(blocks, c) for c in tls if tls[c]}, tls)
    alts = [en.get(k) or tm.get(text.seg_str(t), text.seg_str(t)) for k, t in sorted(segs.items()) if k[0] == c]
    if alts:
        return ' | '.join(alts)
    if depth > 1 or c % 1000 >= len(blocks[b]):
        return '?'
    outs = []
    for kind, v in msgfile.Parser(blocks[b][c % 1000]).tokens():
        if kind == 'c' and v[:1] == b'\x01' and v[1] in (0x43, 0x4a):
            t = int.from_bytes(v[2:4], 'little')
            if 'C%d' % t not in outs:
                outs.append('C%d' % t)
    if not outs:
        ex = sorted(set('{%s}' % v.hex() for kind, v in msgfile.Parser(blocks[b][c % 1000]).tokens()
                        if kind == 'c' and v[:1] == b'\x02'))
        return 'a name/value: ' + ' | '.join(ex) if ex else '(nothing)'
    return 'branches to ' + ', '.join('%s = %s' % (o, legend(blocks, tls, int(o[1:]), depth + 1, tm)[:60])
                                      for o in outs)


def apply(b, batch):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    new = parse_tl(batch)
    errs = []
    for k, en in new.items():
        if k not in jp:
            errs.append('@%d.%d: no such segment in block %d' % (*k, b))
        else:
            errs += ['@%d.%d: %s' % (*k, x) for x in text.check(jp[k], en)]
    if errs:
        sys.exit('\n'.join(errs) + '\n%d errors, nothing written' % len(errs))
    for k, en in new.items():
        for w in warn(b, k, jp[k], en):
            print('warning ' + w)
    recs = parse_tl(tl_path(b))
    recs.update(new)
    write_tl(b, recs)
    print('block %d: %d segments merged (%d in file)' % (b, len(new), len(recs)))


def check(only=None):
    blocks = load_blocks()
    tls = load_tls(only)
    out, done, errs = english_blocks(blocks, tls)
    for b, recs in tls.items():
        jp = jp_block(blocks, b)
        for k, en in sorted(recs.items()):
            if k in jp:
                for w in warn(b, k, jp[k], en):
                    print('warning ' + w)
    try:
        sp = msgfile.split_blocks(out)
        print('%d blocks after the split' % len(sp))
    except ValueError as e:
        errs.append(str(e))
    print('\n'.join(errs))
    print('%d segments translated, %d errors' % (done, len(errs)))
    return not errs


def stats():
    blocks = load_blocks()
    tls = load_tls()
    jps = {b: jp_block(blocks, b) for b in range(msgfile.NBLK)}
    tm = memory(jps, tls)
    tot = done = 0
    for b in range(msgfile.NBLK):
        n = len(jps[b])
        d = sum(1 for k, t in jps[b].items() if k in tls[b] or text.seg_str(t) in tm)
        tot, done = tot + n, done + d
        print('block %02d: %5d / %5d' % (b, d, n))
    print('total   : %5d / %5d (%.1f%%)' % (done, tot, 100.0 * done / max(tot, 1)))


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == 'show':
        nums = [int(x) for x in a[2:] if not x.startswith('--')]
        show(int(a[1]), *(nums + [0, None])[:2], todo='--todo' in a)
    elif a[0] == 'apply':
        apply(int(a[1]), a[2])
    elif a[0] == 'check':
        sys.exit(0 if check(int(a[1]) if len(a) > 1 else None) else 1)
    elif a[0] == 'stats':
        stats()
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
