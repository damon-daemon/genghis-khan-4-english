"""Put a new build's program changes into a running (state-loaded) headless DuckStation.

A save state holds the RAM of the build it was taken on, program included. This halts the CPU
(GDB connect; reconnected until pc and ra are outside the text renderer and our hook areas),
writes every program word the build changes (vs the clean disc) through the GDB server and
continues. It also writes what differs from the build injected last (copies in build/inject_last/):
a word the new build restored to its clean value (a string pointer that no longer moves) is stale in
a RAM that ran an earlier build. After a fresh boot of an older build, delete build/inject_last/.
The loaded overlay (SETUP/USER/...) gets its changed bytes too, but that can disturb
live overlay state (session 14: city panel label boxes vanished) -> confirm overlay changes on a
fresh boot. MESSAGE.CK changes are not injected (a build with > 44 blocks moves the block tables:
boot that build instead).

usage: .venv/bin/python tools/emu/inject.py [built_track1.bin]
"""
import os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
import iso, setup
import gdbmem

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILT = os.path.join(ROOT, 'build', 'genghis_khan_4_en_track1.bin')
BASE, FOFF = 0x80010000, 0x800
BUSY = [(0x8004d600, 0x8004fc40), (0x800d9c0c, 0x800da50c)]   # renderer; font area / wrapper


OVL_BASE, OVLS = 0x80190000, ('SETUP', 'USER', 'TURN', 'WAR', 'DINNER')


def exe(path):
    d = iso.Disc(path)
    return d.file(iso.EXE_NAME, d.records())[FOFF:]


def overlay(path, name):
    d = iso.Disc(path)
    return d.file('BIN/%s.BIN' % name, d.records())


def diff(old, new):
    """[(a, b)] word-aligned byte ranges where new differs from old (new may be longer)."""
    old = old + bytes(max(0, len(new) - len(old)))
    runs, i = [], 0
    while i < len(new):
        if old[i:i + 4] != new[i:i + 4]:
            j = i
            while j < len(new) and new[j:j + 4] != old[j:j + 4]:
                j += 4
            # carry on to the word holding the string's terminator: a tail equal to the clean
            # bytes (e.g. '%s' at the Japanese '%s' offset) is still stale in a RAM that ran an
            # earlier build
            while j < len(new) and 0 not in new[j - 4:j]:
                j += 4
            runs.append((i, min(j, len(new)))); i = j
        else:
            i += 4
    return runs


def write(g, base, data, runs):
    for a, b in runs:
        for k in range(a, b, 0x200):
            chunk = data[k:min(b, k + 0x200)]
            assert g.cmd('M%x,%x:%s' % (base + k, len(chunk), chunk.hex())) == 'OK'


LAST = os.path.join(ROOT, 'build', 'inject_last')


def since_last(name, new, runs):
    """runs + where new differs from the copy injected last; stores new as the last copy."""
    path = os.path.join(LAST, name)
    if os.path.exists(path):
        runs = sorted(set(runs) | set(diff(open(path, 'rb').read(), new)))
    os.makedirs(LAST, exist_ok=True)
    open(path, 'wb').write(new)
    return runs


def halt():
    for _ in range(50):
        g = gdbmem.G()
        r = gdbmem.regs(g)
        if not any(lo <= r[k] < hi for k in ('pc', 'r31') for lo, hi in BUSY):
            return g
        g.resume(); time.sleep(0.05)
    sys.exit('CPU stays in the renderer')


def main():
    built = sys.argv[1] if len(sys.argv) > 1 else BUILT
    old, new = exe(setup.CLEAN), exe(built)
    assert len(old) == len(new)
    runs = diff(old, new)
    g = halt()
    runs = since_last('PROG', new, runs)
    write(g, BASE, new, runs)
    print('injected %d runs, %d bytes' % (len(runs), sum(b - a for a, b in runs)))
    head = g.read(OVL_BASE, 8)             # the loaded overlay: "MWo1" + id
    for name in OVLS:
        o = overlay(setup.CLEAN, name)
        if o[4:8] == head[4:8]:
            n = overlay(built, name)
            r = since_last(name, n, diff(o, n))
            write(g, OVL_BASE, n, r)
            print('%s: injected %d runs, %d bytes' % (name, len(r), sum(b - a for a, b in r)))
            break
    else:
        print('no known overlay loaded')
    g.resume()


if __name__ == '__main__':
    main()
