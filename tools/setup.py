"""Create the working folder script/ from your own disc image.

script/ holds the Japanese text extracted from the disc next to the English, so it is
generated on each machine and never distributed. Run this once after cloning (and
again after editing tl/), then tools/build.py.

usage: .venv/bin/python tools/setup.py [track1.bin]
"""
import hashlib, json, os, sys, unicodedata

sys.path.insert(0, os.path.dirname(__file__))
import iso, msgfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'script')
TL = os.path.join(ROOT, 'tl')
CLEAN = os.path.join(ROOT, 'genghis_khan_4_track1.bin')
CLEAN_SHA1 = '8f473168dccffd5f50c1cf1f890457e0ca04ed7a'
# CD audio track 2: copied next to the rebuilt track 1, never patched
AUDIO = [os.path.join(ROOT, 'genghis_khan_4_track2.bin')]
AUDIO_SHA1 = ['7c8508996d34d2a39fd75a8fab58e2c9b766b09b']


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def check_clean(path, want=CLEAN_SHA1):
    digest = sha1(path)
    if digest != want:
        sys.exit('%s: SHA-1 %s does not match the clean dump (%s)' % (path, digest, want))


def kana(s, kata):
    """Half-width kana -> full-width hiragana (game default) or katakana (after ESC K)."""
    out = ''
    for ch in s:
        if 'ｦ' <= ch <= 'ﾟ':
            ch = unicodedata.normalize('NFKC', ch)
            if not kata and 'ァ' <= ch <= 'ヶ':
                ch = chr(ord(ch) - 0x60)
        out += ch
    # NFKC leaves dakuten as separate marks after kana: compose them
    return unicodedata.normalize('NFC', out.replace('゛', '゙').replace('゜', '゚'))


def readable(tokens):
    """Record tokens -> one readable string: text as Japanese, codes as {hex}, newline as \\n."""
    s, kata = '', False
    for k, v in tokens:
        if k == 't':
            s += kana(v.decode('cp932', 'replace'), kata)
        elif v == b'\n':
            s += '\\n'
        else:
            if v[:1] == b'\x1b' and len(v) > 1:
                kata = v[1:2] == b'K' or (kata and v[1:2] not in b'H')
            s += '{' + v.hex() + '}'
    return s


def export_messages(msg, exe):
    """script/msg/blockNN.json = [{'id', 'jp'}] (records with text only)."""
    os.makedirs(os.path.join(SCRIPT, 'msg'), exist_ok=True)
    total = 0
    for b, blk in enumerate(msgfile.blocks(msg, exe)):
        rows = []
        for i, r in enumerate(msgfile.records(blk)):
            try:
                t = msgfile.Parser(r).tokens()
            except msgfile.ParseError:
                continue                  # kept raw (23.24)
            if any(k == 't' for k, _ in t):
                rows.append({'id': b * 1000 + i, 'jp': readable(t)})
        total += len(rows)
        with open(os.path.join(SCRIPT, 'msg', 'block%02d.json' % b), 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=0)
    return total


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else CLEAN
    check_clean(path)
    if path == CLEAN:
        for p, want in zip(AUDIO, AUDIO_SHA1):
            check_clean(p, want)
    disc = iso.Disc(path)
    recs = disc.records()
    os.makedirs(SCRIPT, exist_ok=True)
    print('messages: %d records' % export_messages(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs)))
    print('script/ ready; now run tools/build.py')


if __name__ == '__main__':
    main()
