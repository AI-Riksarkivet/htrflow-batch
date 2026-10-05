"""The image build's torch/torchvision architecture check (.docker/check_cuda_archs.py).

It reads the kernel architectures out of torchvision's extension without a
GPU. These tests build a minimal ELF with a ``.nv_fatbin`` section in the
layout nvcc emits, so the reader is pinned without committing a binary; it was
also checked against ``cuobjdump`` on the real cu129, cu130 and PyPI wheels.
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / ".docker" / "check_cuda_archs.py"
_spec = importlib.util.spec_from_file_location("check_cuda_archs", SCRIPT)
assert _spec and _spec.loader
cca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cca)


def _entry(kind: int, arch: int, payload: bytes = b"\0" * 16) -> bytes:
    header = struct.pack("<HHIQ", kind, 0, 64, len(payload))
    header += b"\0" * 12 + struct.pack("<I", arch)
    header += b"\0" * (64 - len(header))
    return header + payload


def _fatbin(*entries: bytes) -> bytes:
    body = b"".join(entries)
    return struct.pack("<IHHQ", cca.FATBIN_MAGIC, 1, 16, len(body)) + body


def _elf(fatbin: bytes) -> bytes:
    """A 64-bit little-endian ELF: null section, .nv_fatbin, .shstrtab."""
    names = b"\0.nv_fatbin\0.shstrtab\0"
    data_off = 64
    names_off = data_off + len(fatbin)
    shoff = (names_off + len(names) + 7) // 8 * 8
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x28, shoff)
    struct.pack_into("<HHH", header, 0x3A, 64, 3, 2)

    def shdr(name: int, offset: int, size: int) -> bytes:
        s = bytearray(64)
        struct.pack_into("<I", s, 0, name)
        struct.pack_into("<QQ", s, 0x18, offset, size)
        return bytes(s)

    body = bytes(header) + fatbin + names
    body += b"\0" * (shoff - len(body))
    return (
        body
        + shdr(0, 0, 0)
        + shdr(1, data_off, len(fatbin))
        + shdr(11, names_off, len(names))
    )


def test_reads_every_cubin_arch_and_skips_ptx() -> None:
    fat = _fatbin(_entry(2, 86), _entry(1, 90), _entry(2, 120))
    assert cca.cubin_archs(cca.fatbin_section(_elf(fat))) == {"sm_86", "sm_120"}


def test_reads_several_fatbins_with_padding_between() -> None:
    fat = _fatbin(_entry(2, 75)) + b"\0" * 8 + _fatbin(_entry(2, 90))
    assert cca.cubin_archs(fat) == {"sm_75", "sm_90"}


def test_a_library_without_kernels_is_refused() -> None:
    elf = bytearray(_elf(_fatbin(_entry(2, 80))))
    elf[elf.index(b".nv_fatbin\0")] = ord("x")  # rename the section
    with pytest.raises(ValueError, match="no .nv_fatbin section"):
        cca.fatbin_section(bytes(elf))


def test_not_an_elf_is_refused() -> None:
    with pytest.raises(ValueError, match="not a 64-bit"):
        cca.fatbin_section(b"MZ" + b"\0" * 100)


def test_a_torchvision_missing_one_of_torchs_archs_fails_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    lib = tmp_path / "_C.so"
    lib.write_bytes(_elf(_fatbin(_entry(2, 86), _entry(2, 90))))
    monkeypatch.setattr(cca, "torchvision_extension", lambda: lib)

    class _Cuda:
        @staticmethod
        def get_arch_list() -> list[str]:
            return ["sm_86", "sm_90", "sm_120", "compute_120"]

    class _Torch:
        cuda = _Cuda

    monkeypatch.setitem(__import__("sys").modules, "torch", _Torch)
    assert cca.main(["check"]) == 1
    assert "sm_120" in capsys.readouterr().err

    lib.write_bytes(_elf(_fatbin(_entry(2, 86), _entry(2, 90), _entry(2, 120))))
    assert cca.main(["check"]) == 0
