"""Rebuild track 1 of the disc with the translation and produce a patch.

Output: build/genghis_khan_4_en_track1.bin, a copy of the audio track 2 and a .cue next to
them (boots in an emulator), and build/genghis_khan_4_en.xdelta (clean track 1 -> English
track 1; the audio track is not changed).

usage: .venv/bin/python tools/build.py [--split LIMIT] [clean_track1.bin] [out_track1.bin]
  --split LIMIT  dev test: split the Japanese script at a lower block limit (overflow blocks + J stubs)
"""
import os, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(__file__))
import gfx, iso, msgfile, namegen, nameentry, newname, numentry, progstr, scedat, setup, tl, vwf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'build', 'genghis_khan_4_en_track1.bin')
FIXED = (iso.EXE_NAME,)                  # must keep their place


def build_files(disc, recs, limit=msgfile.BLOCK_MAX):
    """{iso path: bytes} of every rebuilt file (script with the English of tl/, program/overlay strings,
    scenario data names, EXE block tables, VWF font, redrawn images)."""
    clean = progstr.disc_files(disc, recs)
    prog = {t: bytearray(b) for t, b in clean.items()}
    n, moved, pool = progstr.apply(prog, clean, progstr.RESIDENT)
    print('progstr: %d strings (%d moved; overlays grown %s)' % (
        n, moved, ', '.join('%s +%d' % (t, len(prog[t]) - len(clean[t])) for t in pool.grown) or '-'))
    exe = prog['PROG']
    namegen.apply(exe, prog['TURN'])
    nameentry.apply(exe)
    numentry.apply(exe)
    blocks = [msgfile.records(b) for b in msgfile.blocks(disc.file(msgfile.NAME, recs), exe)]
    blocks, done, errs = tl.english_blocks(blocks)
    if errs:
        sys.exit('\n'.join(errs) + '\n%d errors in tl/ (tools/tl.py check)' % len(errs))
    print('MESSAGE.CK: %d English segments' % done)
    blocks = msgfile.split_blocks(blocks, limit)
    msg, offs, sizes = msgfile.build(blocks)
    assert msgfile.TABLE_AT == vwf.FONT_END
    vwf.apply(exe)
    msgfile.patch_exe(exe, offs, sizes)
    print('MESSAGE.CK: %d blocks, %d bytes' % (len(blocks), len(msg)))
    out = {iso.EXE_NAME: bytes(exe), msgfile.NAME: msg}
    out.update((progstr.ovl_name(t), bytes(b)) for t, b in prog.items() if t != 'PROG' and b != clean[t])
    for name, path in scedat.FILES.items():
        out[path] = scedat.apply(name, disc.file(path, recs))
    print('SCEDAT: %d names' % sum(len(v) for v in scedat.load().values()))
    out[newname.NAME] = newname.apply(disc.file(newname.NAME, recs))
    g = gfx.apply(lambda path: disc.file(path, recs))
    out.update(g)
    print('gfx: %d files redrawn' % len(g))
    return out


def write(clean, out, files):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    moved = iso.write_image(clean, out, files)
    print('rebuilt %s%s' % (out, ' (moved: %s)' % ', '.join(moved) if moved else ''))
    if set(moved) & set(FIXED):
        sys.exit('%s moved on the disc' % ', '.join(set(moved) & set(FIXED)))
    iso.verify(out, files)
    audio = []
    for t, src in enumerate(setup.AUDIO, 2):
        dst = out.replace('_track1.bin', '_track%d.bin' % t)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst)
        audio.append(dst)
    iso.write_cue(out.replace('_track1.bin', '.cue'), out, audio)
    patch = os.path.join(os.path.dirname(out), 'genghis_khan_4_en.xdelta')
    try:
        subprocess.run(['xdelta3', '-e', '-f', '-9', '-S', 'djw', '-B', str(1 << 28), '-s', clean, out, patch], check=True)
        print('patch %s (%d bytes)' % (patch, os.path.getsize(patch)))
    except FileNotFoundError:
        print('xdelta3 not installed; skipped patch')
    return moved


def main():
    args, limit = sys.argv[1:], msgfile.BLOCK_MAX
    if args[:1] == ['--split']:
        limit, args = int(args[1], 0), args[2:]
    clean = args[0] if args else setup.CLEAN
    out = args[1] if len(args) > 1 else OUT
    disc = iso.Disc(clean)
    files = build_files(disc, disc.records(), limit)
    write(clean, out, files)


if __name__ == '__main__':
    main()
