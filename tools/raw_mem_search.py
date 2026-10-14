"""Decisive check: does the KNOWN-GOOD passphrase appear anywhere in WeChat's memory?

This bypasses the upstream YARA stub entirely. If the key bytes are not present at
all, then no stub-based scan can ever find it - which tells us the recovery path is
different from the public source's memory scan.
"""
import ctypes
import json
import os
import sys
from ctypes import wintypes

import pymem

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000

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


def regions(handle):
    out, mbi, addr = [], MBI(), 0
    while k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        if mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE:
            out.append((mbi.BaseAddress, mbi.RegionSize))
        addr += mbi.RegionSize
        if addr > 0x7FFFFFFFFFFF:
            break
    return out


def read_mem(handle, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return b""
    return buf.raw[:got.value]


def search(pid, needles):
    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return None
    hits = {name: [] for name in needles}
    rs = regions(handle)
    step = 32 * 1024 * 1024
    scanned = 0
    for base, size in rs:
        off = 0
        while off < size:
            n = min(step, size - off)
            mem = read_mem(handle, base + off, n)
            off += n
            if not mem:
                continue
            scanned += len(mem)
            for name, needle in needles.items():
                i = mem.find(needle)
                if i != -1 and len(hits[name]) < 3:
                    hits[name].append(base + off - n + i)
    return hits, scanned


def main():
    ks = os.path.join(os.environ["APPDATA"], "wechat-data-analysis-desktop", "output", "account_keys.json")
    j = json.load(open(ks, encoding="utf-8"))["wxid_example123456"]
    key = bytes.fromhex(j["db_key"])
    needles = {
        "db_key (as stored)": key,
        "db_key reversed": key[::-1],
        "image_aes_key": bytes.fromhex(j["image_aes_key"]),
    }
    pm = pymem.Pymem("Weixin.exe")
    targets = [("Weixin.exe", pm.process_id)]
    try:
        b = pymem.Pymem("wechatdb_broker.exe")
        targets.append(("wechatdb_broker.exe", b.process_id))
    except Exception:
        pass
    try:
        w = pymem.Pymem("wechat-backend.exe")
        targets.append(("wechat-backend.exe", w.process_id))
    except Exception:
        pass

    for name, pid in targets:
        r = search(pid, needles)
        if r is None:
            print(f"{name} pid={pid}: cannot open")
            continue
        hits, scanned = r
        print(f"{name} pid={pid}: scanned {scanned/1048576:,.0f} MB")
        for k, v in hits.items():
            print(f"    {k:24} {'FOUND at ' + ', '.join(hex(x) for x in v) if v else 'not present'}")


if __name__ == "__main__":
    main()
