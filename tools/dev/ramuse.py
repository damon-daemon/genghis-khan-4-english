"""Show non-zero ranges (granularity 256 B) in RAM dumps between A and B: ramuse.py A B ram..."""
import sys
a, b = int(sys.argv[1], 16) & 0x1fffff, int(sys.argv[2], 16) & 0x1fffff
rams = [open(r, 'rb').read() for r in sys.argv[3:]]
used = [any(any(r[p:p+256]) for r in rams) for p in range(a, b, 256)]
s = None
for i, u in enumerate(used + [not used[-1]]):
    if s is None: s = (i, u); continue
    if u != s[1]:
        print('%08x-%08x %s %d KB' % (0x80000000+a+s[0]*256, 0x80000000+a+i*256, 'used' if s[1] else 'zero', (i-s[0])/4)); s = (i, u)
