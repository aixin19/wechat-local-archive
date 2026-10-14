#!/usr/bin/env python3
"""Standalone WeChat 4.x / SQLCipher database decryptor.

No dependency on WeChatDataAnalysis or any other third-party tool - only `cryptography`
(or pycryptodome) and the standard library.

Usage:
    python standalone_decrypt.py <encrypted.db> <output.db> <64-hex-key>

Format implemented (matches WeChat 4.x / SQLCipher 4 defaults):
    page size                    4096
    KDF                          PBKDF2-HMAC-SHA512(passphrase, salt, 256000, dkLen=32)
    MAC key                      PBKDF2-HMAC-SHA512(enc_key, salt XOR 0x3a, 2, dkLen=32)
    page encryption              AES-256-CBC, IV = page[-80:-64]
    page reserve                 80 bytes = 16-byte IV + 64-byte HMAC
    page 1                       has a 16-byte randomsalt prefix that is skipped
    HMAC                         HMAC-SHA512 over page[16:] (page 1) or page[:] (others),
                                 with the reserve replaced by the little-endian page number
"""
import hashlib
import hmac
import os
import sys

PAGE_SIZE = 4096
RESERVE = 80
IV_LEN = 16
ROUNDS = 256000
KEY_LEN = 32


def derive_enc_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, ROUNDS, dklen=KEY_LEN)


def derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_LEN)


def decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    """Decrypt one 4096-byte page into a normal plaintext SQLite page.

    Page 1 keeps its 16-byte encrypted prefix (the salt) untouched and the decrypted
    body goes at offset 16; the reserve tail is zero-filled.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = page[PAGE_SIZE - RESERVE: PAGE_SIZE - RESERVE + IV_LEN]
    if pgno == 1:
        ciphertext = page[IV_LEN: PAGE_SIZE - RESERVE]   # 16 .. 4016
    else:
        ciphertext = page[0: PAGE_SIZE - RESERVE]        # 0 .. 4016
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(ciphertext) + decryptor.finalize()

    out = bytearray(PAGE_SIZE)          # reserve tail already zero
    if pgno == 1:
        # SQLCipher keeps the 16-byte salt as the page prefix; a normal SQLite header
        # needs the magic at offset 0, so write the magic back and put the decrypted
        # body at offset 16 (the native SQLite page header).
        out[0:IV_LEN] = b"SQLite format 3\x00"
        out[IV_LEN: IV_LEN + len(plain)] = plain
    else:
        out[0:len(plain)] = plain
    return bytes(out)


def page_hmac(mac_key: bytes, page: bytes, pgno: int) -> bytes:
    data = bytearray(page)
    start = IV_LEN if pgno == 1 else 0
    # the last 64 bytes hold the HMAC; the 16 byte IV before it is excluded too
    mac_input = bytes(data[start:PAGE_SIZE - RESERVE + IV_LEN]) + pgno.to_bytes(4, "little")
    return hmac.new(mac_key, mac_input, hashlib.sha512).digest()


def decrypt_file(src: str, dst: str, key_hex: str, verify: bool = True) -> dict:
    key = bytes.fromhex(key_hex)
    if len(key) != KEY_LEN:
        raise ValueError(f"key must be {KEY_LEN} bytes ({KEY_LEN*2} hex chars)")
    size = os.path.getsize(src)
    if size % PAGE_SIZE:
        # WeChat always uses whole pages; refuse rather than guess
        raise ValueError(f"file size {size} is not a multiple of {PAGE_SIZE}")

    with open(src, "rb") as f:
        page1 = f.read(PAGE_SIZE)
    salt = page1[:IV_LEN]
    enc_key = derive_enc_key(key, salt)
    mac_key = derive_mac_key(enc_key, salt)

    bad_hmac = 0
    total = size // PAGE_SIZE
    # step 1: decrypt everything EXCEPT page 1 (standard SQLCipher trick: page 1's
    # plaintext header is restored at the end, after the page is read).
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        first = fin.read(PAGE_SIZE)
        if verify and page_hmac(mac_key, first, 1) != first[-64:]:
            bad_hmac += 1
        fout.write(b"\x00" * PAGE_SIZE)
        pgno = 2
        while True:
            page = fin.read(PAGE_SIZE)
            if not page:
                break
            if verify and page_hmac(mac_key, page, pgno) != page[-64:]:
                bad_hmac += 1
            fout.write(decrypt_page(enc_key, page, pgno))
            pgno += 1
        # step 2: now write page 1
        fout.seek(0)
        fout.write(decrypt_page(enc_key, first, 1))

    return {"pages": total, "bad_hmac_pages": bad_hmac, "salt": salt.hex()}


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    src, dst, key_hex = sys.argv[1], sys.argv[2], sys.argv[3]
    info = decrypt_file(src, dst, key_hex)
    with open(dst, "rb") as f:
        magic = f.read(16)
    print(f"decrypted {info['pages']} pages -> {dst}")
    print(f"  salt={info['salt'][:16]}...  bad_hmac_pages={info['bad_hmac_pages']}")
    print(f"  output magic={magic!r}  valid_sqlite={magic.startswith(b'SQLite format 3')}")
    return 0 if magic.startswith(b"SQLite format 3") else 2


if __name__ == "__main__":
    sys.exit(main())
