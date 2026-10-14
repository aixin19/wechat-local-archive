"""Exhaustive hunt: does the known key exist in ANY common encoding in WeChat's memory?

Searches both WeChat processes for the key bytes, UTF-16, base64 and hex forms.
"""
import base64
import ctypes
import json
import os
from ctypes import wintypes

import pymem

PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x0010, 0x0400
MEM_COMMIT, MEM_PRIVATE = 0x1000, 0x20000

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


def regions(h):
    out, mbi, a = [], MBI(), 0
    while k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        if mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE:
            out.append((mbi.BaseAddress, mbi.RegionSize))
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
hexs = rec["db_key"].lower().encode()
hexu = rec["db_key"].upper().encode()
needles = {
    "raw32": key,
    "raw32-reversed": key[::-1],
    "utf16le": rec["db_key"].lower().encode("utf-16-le"),
    "utf16be": rec["db_key"].lower().encode("utf-16-be"),
    "hexascii-lower": hexs,
    "hexascii-upper": hexu,
    "base64": base64.b64encode(key),
    "hex-with-spaces": " ".join(rec["db_key"][i:i + 2] for i in range(0, 64, 2)).upper().encode(),
}

pm = pymem.Pymem("Weixin.exe")
targets = [("Weixin.exe", pm.process_id)]
try:
    targets.append(("wechatdb_broker.exe", pymem.Pymem("wechatdb_broker.exe").process_id))
except Exception:
    pass

for name, pid in targets:
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print(f"{name}: cannot open")
        continue
    hits = {k: 0 for k in needles}
    scanned = 0
    for base, size in regions(h):
        off = 0
        while off < size:
            n = min(32 * 1024 * 1024, size - off)
            mem = read_mem(h, base + off, n)
            off += n
            if not mem:
                continue
            scanned += len(mem)
            for k, v in needles.items():
                if v in mem:
                    hits[k] += 1
    print(f"\n{name} pid={pid}  scanned {scanned/1048576:,.0f} MB")
    for k, v in hits.items():
        print(f"   {k:18} {'FOUND x%d' % v if v else 'absent'}")
