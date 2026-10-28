"""Find where overlay BIN files sit in a RAM dump: ovlfind.py ram.bin [...]"""
import glob, os, sys
ovl = {os.path.basename(p): open(p, 'rb').read() for p in sorted(glob.glob('extract/BIN/*.BIN'))}
for r in sys.argv[1:]:
    ram = open(r, 'rb').read()
    hits = []
    for n, d in ovl.items():
        for off in range(0, len(d) - 64, 4096):          # probe 64-byte chunks
            i = ram.find(d[off:off + 64])
            if i >= 0:
                hits.append('%s@%08x(+%x)' % (n, 0x80000000 + i - off, off)); break
    print(os.path.basename(r), ' '.join(hits) or '-')
