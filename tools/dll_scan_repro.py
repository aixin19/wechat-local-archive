"""Reproduce step 1 of the public key-recovery algorithm: scan Weixin.dll for the
32-byte internal DB key, then prove it is the right one.

The pattern + chunking + code-section filter come from
src/wechat_decrypt_tool/dll_key_scan.py in the upstream repo.
"""
import multiprocessing
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pefile

PATTERN = re.compile(
    b"^\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\xBA(.{8})"
    b".{3,8}?"
    b"\x48\x85\xC0",
    re.DOTALL,
)
CODE_SECTION_CHARACTERISTIC = 0x20000000
CHUNK_SIZE = 2 * 1024 * 1024
OVERLAP_SIZE = 100


def worker_search(task):
    file_path, file_offset, chunk_size, overlap, base_va = task
    results = []
    with open(file_path, "rb") as f:
        f.seek(file_offset)
        chunk_data = f.read(chunk_size + overlap)
    offset = 0
    while True:
        idx = chunk_data.find(b"\x48\xBA", offset)
        if idx == -1 or idx >= chunk_size:
            break
        m = PATTERN.match(chunk_data[idx:idx + 85])
        if m:
            key = m.group(1) + m.group(2) + m.group(3) + m.group(4)
            results.append({"va": f"0x{base_va + idx:X}", "key_hex": key.hex()})
            offset = idx + len(m.group(0))
        else:
            offset = idx + 1
    return results


def scan(dll: str):
    path = Path(dll)
    pe = pefile.PE(str(path), fast_load=True)
    tasks = []
    try:
        image_base = int(pe.OPTIONAL_HEADER.ImageBase)
        for section in pe.sections:
            if not (int(section.Characteristics) & CODE_SECTION_CHARACTERISTIC):
                continue
            sec_size = int(section.SizeOfRawData)
            sec_off = int(section.PointerToRawData)
            sec_va = image_base + int(section.VirtualAddress)
            for i in range(0, sec_size, CHUNK_SIZE):
                tasks.append((str(path), sec_off + i, min(CHUNK_SIZE, sec_size - i),
                              OVERLAP_SIZE, sec_va + i))
    finally:
        pe.close()
    out = []
    workers = min(max(1, multiprocessing.cpu_count()), len(tasks) or 1)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(worker_search, t) for t in tasks]):
            out.extend(fut.result())
    out.sort(key=lambda x: int(x["va"], 16))
    return out


if __name__ == "__main__":
    import hashlib
    import hmac
    import json
    import os
    import sys

    dll = sys.argv[1]
    hits = scan(dll)
    print(f"scanned: {dll}")
    print(f"internal-key candidates found: {len(hits)}")
    for h in hits:
        print(f"  va={h['va']:>14}  key={h['key_hex'][:8]}...{h['key_hex'][-4:]}")
    with open(r"<repo>\dll_candidates.json", "w") as f:
        json.dump(hits, f)

    # ---- verify: XOR the stored db_key with each candidate and test page-1 HMAC ----
    ks = os.path.join(os.environ["APPDATA"], "wechat-data-analysis-desktop", "output", "account_keys.json")
    stored = bytes.fromhex(json.load(open(ks, encoding="utf-8"))["wxid_example123456"]["db_key"])
    enc = r"D:\WeChat\xwechat_files\wxid_example123456_a1b2\db_storage\session\session.db"
    with open(enc, "rb") as f:
        page1 = f.read(4096)
    salt = page1[:16]

    def page1_ok(key: bytes) -> bool:
        enc_key = hashlib.pbkdf2_hmac("sha512", key, salt, 256000, dklen=32)
        mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
        data = page1[16:4096 - 80 + 16] + (1).to_bytes(4, "little")
        return hmac.new(mac_key, data, hashlib.sha512).digest() == page1[-64:]

    print("\n=== verification (stored_key XOR candidate -> page-1 HMAC) ===")
    winner = None
    for h in hits:
        cand = bytes.fromhex(h["key_hex"])
        derived = bytes(a ^ b for a, b in zip(stored, cand))
        ok = page1_ok(derived)
        print(f"  va={h['va']:>14}  {h['key_hex'][:8]}...{h['key_hex'][-4:]}  ->  "
              f"{'*** MATCH ***' if ok else 'no'}")
        if ok:
            winner = h
    print("\nRESULT:", "internal key recovered and cryptographically confirmed" if winner
          else "no candidate verified (WeChat version may differ from the pattern)")

