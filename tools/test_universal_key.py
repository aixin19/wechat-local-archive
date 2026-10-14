"""Is the stored key a universal passphrase, or per-database?

If it opens many DBs, it is the account passphrase. If it opens only some
(and others need an XOR-unmasked variant), the app stores a derived value.
"""
import hashlib
import hmac
import json
import os
import struct

KS = os.path.join(os.environ["APPDATA"], "wechat-data-analysis-desktop", "output", "account_keys.json")
key = bytes.fromhex(json.load(open(KS, encoding="utf-8"))["wxid_example123456"]["db_key"])
SRC = r"D:\WeChat\xwechat_files\wxid_example123456_a1b2\db_storage"

DBS = [
    "session/session.db", "contact/contact.db", "message/message_0.db",
    "message/message_1.db", "favorite/favorite.db", "sns/sns.db",
    "general/general.db", "head_image/head_image.db", "media/media_0.db",
    "emoticon/emoticon.db", "bizchat/bizchat.db", "hardlink/hardlink.db",
]


def page1_ok(passphrase, page1):
    salt = page1[:16]
    nk = hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=32)
    mk = hashlib.pbkdf2_hmac("sha512", nk, bytes(b ^ 0x3A for b in salt), 2, dklen=32)
    mac = hmac.new(mk, page1[16:4096 - 80 + 16], hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return mac.digest() == page1[4096 - 80 + 16:4096 - 80 + 16 + 64]


print(f"{'database':28} {'salt':34} {'stored key opens it?'}")
ks_seen = {}
for rel in DBS:
    p = os.path.join(SRC, rel.replace("/", os.sep))
    if not os.path.exists(p):
        print(f"{rel:28} MISSING")
        continue
    page1 = open(p, "rb").read(4096)
    salt = page1[:16].hex()
    ks_seen.setdefault(salt, []).append(rel)
    ok = page1_ok(key, page1)
    print(f"{rel:28} {salt:34} {'YES' if ok else 'no'}")

print(f"\ndistinct salts across {sum(len(v) for v in ks_seen.values())} databases: {len(ks_seen)}")
for salt, dbs in ks_seen.items():
    print(f"  {salt}  <- {dbs}")
