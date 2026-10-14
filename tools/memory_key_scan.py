#!/usr/bin/env python3
"""Faithful single-process port of WeChatDataAnalysis' V4 memory key recovery.

Algorithm (from upstream src/wechat_decrypt_tool/key_v4.py, unchanged):
  1. Get every committed, private, read-write region of the Weixin.exe process.
  2. YARA-match the `GetKeyAddrStub` structure; each hit holds an 8-byte pointer.
  3. Read the 32 bytes at that pointer -> candidate workspace key.
  4. Drop candidates that fail the entropy/ASCII filter.
  5. Verify each candidate against page 1 of a real encrypted DB using
     PBKDF2-HMAC-SHA512(256000) + HMAC-SHA512, optionally after XOR-unmasking.

Differences from upstream: one process (no multiprocessing) for observability,
and it also tests candidates against the already-known key so we can see whether
the memory candidate is the raw value or the masked/unmasked derivative.
"""
import ctypes
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from ctypes import wintypes

import pymem
import yara

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PAGE_READWRITE = 0x04
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
ROUNDS = 256000

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


YARA_SRC = r'''
rule GetKeyAddrStub
{
    strings:
        $a = { ?? ?? ?? ?? ?? ?? 00 00 00 00 00 00 00 00 00 00 20 00 00 00 00 00 00 00 2f 00 00 00 00 00 00 00 }
    condition:
        all of them
}
'''


def regions(handle, want_writable_only=False):
    out = []
    mbi = MBI()
    addr = 0
    while k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        if mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE:
            if not want_writable_only or (mbi.Protect & (PAGE_READWRITE | 0x40)):
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


def is_potential_key(key: bytes) -> bool:
    if len(key) != 32:
        return False
    if len(set(key)) < 15:
        return False
    if sum(32 <= b <= 126 for b in key) > 24:
        return False
    return True


def page1_ok(passphrase: bytes, buf: bytes, unmask=None) -> bool:
    if unmask is not None:
        passphrase = bytes(a ^ b for a, b in zip(passphrase, unmask))
    salt = buf[:SALT_SIZE]
    mac_salt = bytes(x ^ 0x3A for x in salt)
    new_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, ROUNDS, dklen=KEY_SIZE)
    mac_key = hashlib.pbkdf2_hmac("sha512", new_key, mac_salt, 2, dklen=KEY_SIZE)
    reserve = 16 + 64  # already 16-aligned
    start, end = SALT_SIZE, PAGE_SIZE
    mac = hmac.new(mac_key, buf[start:end - reserve + 16], hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return mac.digest() == buf[end - reserve + 16: end - reserve + 16 + 64]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    db_path = sys.argv[1]
    buf = open(db_path, "rb").read(PAGE_SIZE)

    ks = os.path.join(os.environ["APPDATA"], "wechat-data-analysis-desktop", "output", "account_keys.json")
    stored = bytes.fromhex(json.load(open(ks, encoding="utf-8"))["wxid_example123456"]["db_key"])

    pm = pymem.Pymem("Weixin.exe")
    pid = pm.process_id
    print(f"[*] attached Weixin.exe pid={pid}")
    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        print("[-] OpenProcess failed:", ctypes.get_last_error())
        return 1

    rs = regions(handle)
    total_mb = sum(s for _, s in rs) / 1048576
    print(f"[*] private committed regions: {len(rs)}  ({total_mb:,.0f} MB)")

    rules = yara.compile(source=YARA_SRC)
    t0 = time.time()
    stub_hits = 0
    candidates = set()
    scanned_mb = 0.0
    for i, (base, size) in enumerate(rs, 1):
        step = 32 * 1024 * 1024
        off = 0
        while off < size:
            n = min(step, size - off)
            mem = read_mem(handle, base + off, n)
            off += n
            if not mem:
                continue
            scanned_mb += len(mem) / 1048576
            for m in rules.match(data=mem):
                for s in m.strings:
                    for inst in s.instances:
                        stub_hits += 1
                        ptr = struct.unpack_from("<Q", mem, inst.offset)[0]
                        key = read_mem(handle, ptr, 32)
                        if len(key) == 32 and is_potential_key(key):
                            candidates.add(key)
        if i % 20 == 0:
            print(f"    ...region {i}/{len(rs)}  scanned={scanned_mb:,.0f} MB  stubs={stub_hits}  cands={len(candidates)}")

    print(f"[*] done in {time.time()-t0:.1f}s  scanned={scanned_mb:,.0f} MB")
    print(f"[*] GetKeyAddrStub hits: {stub_hits}")
    print(f"[*] candidates after entropy/ASCII filter: {len(candidates)}")

    # verify - try several derivations
    print()
    found = None
    csum = lambda b: hashlib.sha512(b).digest()[:32]
    for c in candidates:
        tests = [
            ("direct", c),
            ("masked(stored^c)", bytes(a ^ b for a, b in zip(stored, c))),
            ("masked(c^stored)", bytes(a ^ b for a, b in zip(c, stored))),
            ("reversed", c[::-1]),
            ("sha512[:32](c)", csum(c)),
            ("sha256(c)", hashlib.sha256(c).digest()),
            ("masked(sha512[:32](c))", bytes(a ^ b for a, b in zip(stored, csum(c)))),
        ]
        for kind, k in tests:
            try:
                if page1_ok(k, buf):
                    found = (kind, k)
                    break
            except Exception:
                pass
        if found:
            break
    if found:
        kind, key = found
        print(f"[+] VALID KEY RECOVERED FROM MEMORY via '{kind}'")
        print(f"    candidate {key.hex()[:16]}...{key.hex()[-8:]}")
    else:
        print("[-] no memory candidate verified against page 1 (tried 7 derivations x %d candidates)" % len(candidates))

    # what do the candidates look like? (prefixes only)
    print("\n[*] sample of raw candidates (prefixes):")
    for c in list(candidates)[:8]:
        print(f"      {c.hex()[:24]}...  distinct={len(set(c))}")

    # informational: is the stored (known-good) key itself sitting in memory?
    print(f"\n[*] known-good key present verbatim in candidates: "
          f"{'yes' if any(c == stored for c in candidates) else 'no'}")
    k32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
