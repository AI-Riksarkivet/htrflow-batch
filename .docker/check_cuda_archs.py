"""Fail the image build when torchvision cannot run on a GPU torch can.

torch and torchvision ship separate CUDA kernels. A pair whose torchvision
lacks an architecture its torch carries loads, imports and passes every
CPU-side check, then fails the first torchvision op on that card with "no
kernel image is available for execution on the device" -- the cu129 builds
did exactly that on Blackwell (torch to sm_120, torchvision to sm_90), and
only a page on a Blackwell card showed it.

So this reads the architectures compiled into torchvision's extension (the
fat binaries in its ``.nv_fatbin`` section, whose entry headers stay readable
when the payload is compressed) and requires every ``sm_XY`` that
``torch.cuda.get_arch_list()`` reports. No GPU is needed.

Usage: python check_cuda_archs.py            (checks the installed pair)
       python check_cuda_archs.py <lib.so>   (prints that library's archs)
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

FATBIN_MAGIC = 0xBA55ED50
_ELF_KIND = 2  # a cubin; 1 is PTX


def fatbin_section(data: bytes) -> bytes:
    """The ``.nv_fatbin`` section of a 64-bit little-endian ELF shared object."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not a 64-bit little-endian ELF file")
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)

    def section(i: int) -> tuple[int, int, int]:
        base = shoff + i * shentsize
        name = struct.unpack_from("<I", data, base)[0]
        offset, size = struct.unpack_from("<QQ", data, base + 0x18)
        return name, offset, size

    _, str_off, _ = section(shstrndx)
    for i in range(shnum):
        name, offset, size = section(i)
        end = data.index(b"\0", str_off + name)
        if data[str_off + name : end] == b".nv_fatbin":
            return data[offset : offset + size]
    raise ValueError("no .nv_fatbin section: the library carries no CUDA kernels")


def cubin_archs(fatbin: bytes) -> set[str]:
    """Every ``sm_XY`` with a compiled kernel image in the fat binaries."""
    archs: set[str] = set()
    pos = 0
    while pos + 16 <= len(fatbin):
        magic, _version, header_size, fat_size = struct.unpack_from(
            "<IHHQ", fatbin, pos
        )
        if magic != FATBIN_MAGIC:
            # Fat binaries are 8-byte aligned and may be padded between.
            pos += 8
            continue
        entry = pos + header_size
        end = entry + fat_size
        while entry + 32 <= end:
            kind, _flags, entry_header, payload = struct.unpack_from(
                "<HHIQ", fatbin, entry
            )
            arch = struct.unpack_from("<I", fatbin, entry + 28)[0]
            if kind == _ELF_KIND:
                archs.add(f"sm_{arch}")
            entry += entry_header + payload
        pos = end
    return archs


def torchvision_extension() -> Path:
    import torchvision

    return Path(torchvision.__file__).parent / "_C.so"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print(" ".join(sorted(cubin_archs(fatbin_section(Path(argv[1]).read_bytes())))))
        return 0
    import torch

    wanted = {a for a in torch.cuda.get_arch_list() if a.startswith("sm_")}
    have = cubin_archs(fatbin_section(torchvision_extension().read_bytes()))
    missing = sorted(wanted - have)
    if missing:
        print(
            f"torchvision has no kernels for {', '.join(missing)}, which torch "
            f"supports ({' '.join(sorted(wanted))}); torchvision carries "
            f"{' '.join(sorted(have)) or 'none'}. Install a torch/torchvision "
            "pair built for the same architectures.",
            file=sys.stderr,
        )
        return 1
    print(f"torchvision covers torch's architectures: {' '.join(sorted(wanted))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
