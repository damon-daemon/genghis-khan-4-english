"""Linear scan for NUL-terminated Japanese strings (SJIS + half-width kana). usage: sjscan.py file [min]"""
import sys


def scan(d, minjp=2):
    out, i, n = [], 0, len(d)
    while i < n:
        j, jp = i, 0
        while j < n:
            c = d[j]
            if (0x81 <= c <= 0x9f or 0xe0 <= c <= 0xef) and j + 1 < n and 0x40 <= d[j + 1] <= 0xfc:
                jp += 1; j += 2
            elif 0xa1 <= c <= 0xdf:
                jp += c >= 0xa6; j += 1
            elif 0x20 <= c < 0x7f or c in (0x0a, 0x1b):
                j += 1
            else:
                break
        if j < n and d[j] == 0 and jp >= minjp:
            out.append((i, d[i:j]))
        i = j + 1 if j > i else i + 1
    return out


if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    for o, s in scan(d, int(sys.argv[2]) if len(sys.argv) > 2 else 2):
        print('%06x %s' % (o, s.decode('cp932', 'replace')))
