#!/usr/bin/env python3
"""Measure chunk-type SEQUENCE diversity in two AFL corpora (the IJON class-2
metric, cf. paper Table IV: distinct message sequences explored).

Parses each corpus PNG's chunk-type sequence and reports, per corpus:
  - distinct individual chunk types seen (the alphabet)
  - distinct full chunk-type sequences (ordered tuple per file)
  - distinct 3-chunk windows (sub-sequences)

Usage: chunk_seq_diversity.py <corpusA> <corpusB> [labelA labelB]
A PNG is an 8-byte signature then chunks: [len(4)][type(4)][data][crc(4)].
Corpus files may be malformed (CRC ignored at runtime), so parse defensively.
"""
import sys
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def chunk_types(data: bytes, max_chunks: int = 64):
    """Best-effort extract the ordered list of chunk type tags from a PNG blob."""
    if not data.startswith(PNG_SIG):
        return None
    off, types = 8, []
    n = len(data)
    while off + 8 <= n and len(types) < max_chunks:
        length = int.from_bytes(data[off:off + 4], "big")
        tag = data[off + 4:off + 8]
        if not all(65 <= b <= 122 for b in tag):     # not ASCII letters -> stop
            break
        types.append(tag.decode("latin1"))
        if tag == b"IEND":
            break
        off += 8 + length + 4                          # len + type + data + crc
        if length > n:                                 # bogus length -> stop
            break
    return types


def analyse(corpus: Path):
    files = list(corpus.glob("id:*")) or [p for p in corpus.iterdir() if p.is_file()]
    seqs, alphabet, grams = set(), set(), set()
    parsed = 0
    for f in files:
        t = chunk_types(f.read_bytes())
        if not t:
            continue
        parsed += 1
        seqs.add(tuple(t))
        alphabet.update(t)
        for i in range(len(t) - 2):
            grams.add(tuple(t[i:i + 3]))
    return dict(files=len(files), parsed=parsed, types=len(alphabet),
                sequences=len(seqs), trigrams=len(grams), alphabet=sorted(alphabet))


if __name__ == "__main__":
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    la = sys.argv[3] if len(sys.argv) > 3 else a.name
    lb = sys.argv[4] if len(sys.argv) > 4 else b.name
    ra, rb = analyse(a), analyse(b)
    print(f"{'metric':<26}{la:>16}{lb:>16}")
    for k in ("files", "parsed", "types", "sequences", "trigrams"):
        print(f"{k:<26}{ra[k]:>16}{rb[k]:>16}")
    print(f"\n{la} chunk types: {ra['alphabet']}")
    print(f"{lb} chunk types: {rb['alphabet']}")
