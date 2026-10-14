"""Broader hunt: scan ALL committed memory types (not just MEM_PRIVATE).

Previous hunts only covered MEM_PRIVATE, which was a real blind spot: a key could
live in MEM_MAPPED (e.g. a shared/backed page) or MEM_IMAGE.
"""
import base64
import ctypes
import json
import os
from ctypes import wintypes

import pymem

PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x0010, 0x0400
MEM_COMMIT = 0x1000

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wintypes.BOOL


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", ctypes.c_ulong), ("RegionSize", ctypes.c_size_t),
                ("State", ctypes.c_ulong), ("Protect", ctypes.c_ulong), ("Type", ctypes.c_ulong)]


TYPE_NAMES = {0x1000000: "IMAGE", 0x40000: "MAPPED", 0x20000: "PRIVATE"}


def all_regions(h):
    out, mbi, a = [], MBI(), 0
    while k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        if mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) not in (0x01, 0x100):
            out.append((mbi.BaseAddress, mbi.RegionSize, mbi.Type))
        a += mbi.RegionSize
        if a > 0x7FFFFFFFFFFF:
            break
    return out


def read_mem(h, a, n):
    b = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(a), b, n, ctypes.byref(got)):
        return b""
    return b.raw[:got.value]


ks = os.path.join(os.environ["APPDATA"], "wechat-data-analysis-desktop", "output", "account_keys.json")
rec = json.load(open(ks, encoding="utf-8"))["wxid_example123456"]
key = bytes.fromhex(rec["db_key"])
needles = {
    "raw32": key,
    "rev": key[::-1],
    "utf16le": rec["db_key"].lower().encode("utf-16-le"),
    "hexascii": rec["db_key"].lower().encode(),
    "base64": base64.b64encode(key),
}

pm = pymem.Pymem("Weixin.exe")
pid = pm.process_id
h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
rs = all_regions(h)
by_type = {}
for _, s, t in rs:
    by_type[TYPE_NAMES.get(t, hex(t))] = by_type.get(TYPE_NAMES.get(t, hex(t)), 0) + s
print(f"pid={pid}  regions={len(rs)}")
for t, s in sorted(by_type.items()):
    print(f"   {t:8} {s/1048576:,.0f} MB")

hits = {k: 0 for k in needles}
scanned = 0
for base, size, t in rs:
    off = 0
    while off < size:
        n = min(64 * 1024 * 1024, size - off)
        mem = read_mem(h, base + off, n)
        off += n
        if not mem:
            continue
        scanned += len(mem)
        for k, v in needles.items():
            if v in mem:
                hits[k] += 1
        if scanned > 3 * 1024 * 1024 * 1024:
            break
    if scanned > 3 * 1024 * 1024 * 1024:
        break

print(f"\nscanned {scanned/1048576:,.0f} MB across all memory types")
for k, v in hits.items():
    print(f"   {k:10} {'FOUND x%d' % v if v else 'absent'}")
