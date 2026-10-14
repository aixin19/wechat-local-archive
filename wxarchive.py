#!/usr/bin/env python3
"""WeChat local archive toolkit - fully standalone.

Nothing here depends on WeChatDataAnalysis, the native core, or any GUI. You need
only (a) an encrypted DB + its 64-hex passphrase, or (b) an already-decrypted DB.

Commands
--------
  decrypt   decrypt an encrypted .db from the source tree into a plaintext copy
  sessions  list chat sessions (from a decrypted session.db)
  export    export one chat (or all) to HTML + JSON

Examples
--------
  python wxarchive.py decrypt  --db "F:\\...\\db_storage\\session\\session.db" --key <hex> --out out/
  python wxarchive.py sessions --dir out/wxid_xxx
  python wxarchive.py export   --dir out/wxid_xxx --all --out archive/

DB layout it expects (created by `decrypt`): the account folder holding the
decrypted .db files, e.g. out/wxid_example123456/{session.db,message_0.db,...}
"""
import argparse
import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import struct
import sys

PAGE_SIZE, SALT_SIZE, KEY_SIZE, ROUNDS = 4096, 16, 32, 256000
RESERVE = 80  # IV 16 + HMAC 64


# ----------------------------------------------------------------- decryption
def _aes_cbc_decrypt(key, iv, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return d.update(data) + d.finalize()


def derive_enc_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, ROUNDS, dklen=KEY_SIZE)


def decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    iv = page[PAGE_SIZE - RESERVE: PAGE_SIZE - RESERVE + 16]
    if pgno == 1:
        plain = _aes_cbc_decrypt(enc_key, iv, page[16:PAGE_SIZE - RESERVE])
    else:
        plain = _aes_cbc_decrypt(enc_key, iv, page[:PAGE_SIZE - RESERVE])
    out = bytearray(PAGE_SIZE)
    if pgno == 1:
        out[0:16] = b"SQLite format 3\x00"
        out[16:16 + len(plain)] = plain
    else:
        out[0:len(plain)] = plain
    return bytes(out)


def verify_key(passphrase: bytes, page1: bytes) -> bool:
    salt = page1[:SALT_SIZE]
    nk = derive_enc_key(passphrase, salt)
    mk = hashlib.pbkdf2_hmac("sha512", nk, bytes(b ^ 0x3A for b in salt), 2, dklen=KEY_SIZE)
    mac = hmac.new(mk, page1[SALT_SIZE:PAGE_SIZE - RESERVE + 16], hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return mac.digest() == page1[PAGE_SIZE - RESERVE + 16:PAGE_SIZE - RESERVE + 16 + 64]


def decrypt_file(src, dst, key_hex):
    key = bytes.fromhex(key_hex.strip())
    if len(key) != KEY_SIZE:
        raise SystemExit(f"key must be {KEY_SIZE * 2} hex chars, got {len(key_hex.strip())}")
    size = os.path.getsize(src)
    if size % PAGE_SIZE:
        raise SystemExit(f"{src}: size {size} is not a multiple of {PAGE_SIZE}")
    with open(src, "rb") as f:
        first = f.read(PAGE_SIZE)
    if not verify_key(key, first):
        raise SystemExit(f"{src}: passphrase does NOT authenticate page 1 (wrong key?)")
    salt = first[:16]
    enc_key = derive_enc_key(key, salt)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fin.read(PAGE_SIZE)
        fout.write(b"\x00" * PAGE_SIZE)
        pgno = 2
        while True:
            page = fin.read(PAGE_SIZE)
            if not page:
                break
            fout.write(decrypt_page(enc_key, page, pgno))
            pgno += 1
        fout.seek(0)
        fout.write(decrypt_page(enc_key, first, 1))
    return pgno - 1


# ------------------------------------------------------------------- reading
def decompress_content(row) -> str:
    raw = row["message_content"]
    ct = row.get("WCDB_CT_message_content") or 0
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if ct == 4:
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(raw).decode("utf-8", "replace")
        except Exception:
            return ""
    return raw.decode("utf-8", "replace")


MSG_TYPES = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "表情",
    48: "位置", 49: "链接/文件/红包", 50: "通话", 10000: "系统", 10002: "拍一拍",
}


def describe(local_type: int, content: str) -> str:
    base = local_type & 0xFFFF
    label = MSG_TYPES.get(base, f"类型{base}")
    m = re.search(r"<title>(.*?)</title>", content, re.S)
    if m:
        return f"[{label}] {html.unescape(m.group(1))[:80]}"
    if base == 3:
        return "[图片]"
    if base == 34:
        return "[语音]"
    if base == 43:
        return "[视频]"
    if base == 47:
        return "[表情]"
    if base == 10000:
        return f"[系统] {content[:120]}"
    return f"[{label}]"


class Account:
    """One decrypted account directory."""

    def __init__(self, d):
        if not os.path.isdir(d):
            raise SystemExit(f"not a directory: {d}")
        self.dir = d
        self.sessions = self._load_sessions()
        self.contacts = self._load_contacts()
        self.msg_dbs = self._msg_dbs()

    def _ro(self, name):
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            return None
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True)

    def _msg_dbs(self):
        out = []
        for f in sorted(os.listdir(self.dir)):
            if re.fullmatch(r"message_\d+\.db", f):
                out.append((int(re.search(r"\d+", f).group()), f))
        out.sort()
        return out

    def _load_sessions(self):
        c = self._ro("session.db")
        if not c:
            return {}
        try:
            cols = {r[1] for r in c.execute("pragma table_info(SessionTable)")}
            want = [x for x in ("username", "summary", "last_timestamp", "unread_count", "last_msg_type") if x in cols]
            rows = c.execute(f'select {",".join(want)} from SessionTable').fetchall()
            return {r[0]: dict(zip(want, r)) for r in rows if r[0]}
        finally:
            c.close()

    def _load_contacts(self):
        c = self._ro("contact.db")
        if not c:
            return {}
        try:
            cols = [r[1] for r in c.execute("pragma table_info(contact)")]
            out = {}
            for r in c.execute("select username, remark, nick_name, alias from contact"):
                u, remark, nick, alias = r
                if u:
                    out[u] = remark or nick or alias or u
            return out
        finally:
            c.close()

    def display_name(self, username):
        if not username:
            return ""
        return self.contacts.get(username) or username

    def find_table(self, username):
        """Locate Msg_<md5(username)> across message shards, newest shard first."""
        h = hashlib.md5(username.encode()).hexdigest()
        target = f"Msg_{h}"
        for _idx, f in reversed(self.msg_dbs):
            c = self._ro(f)
            if not c:
                continue
            try:
                row = c.execute("select 1 from sqlite_master where type='table' and name=?", (target,)).fetchone()
                if row:
                    return f, target
            finally:
                c.close()
        return None, None

    def name2id(self, dbfile):
        c = self._ro(dbfile)
        try:
            return {r[0]: r[1] for r in c.execute("select rowid, user_name from Name2Id")}
        finally:
            c.close()

    def messages(self, username, limit=None):
        dbfile, table = self.find_table(username)
        if not dbfile:
            return []
        ids = self.name2id(dbfile)
        c = self._ro(dbfile)
        try:
            q = f'select local_id, local_type, create_time, real_sender_id, message_content, WCDB_CT_message_content from "{table}" order by create_time'
            if limit:
                q += f" limit {int(limit)}"
            out = []
            for r in c.execute(q):
                row = {
                    "local_id": r[0], "local_type": r[1], "create_time": r[2],
                    "sender": ids.get(r[3], ""), "message_content": r[4],
                    "WCDB_CT_message_content": r[5],
                }
                content = decompress_content(row)
                out.append({
                    "local_id": row["local_id"],
                    "type": row["local_type"] & 0xFFFF,
                    "time": row["create_time"],
                    "sender": row["sender"],
                    "sender_name": self.display_name(row["sender"]),
                    "content": content,
                })
            return out
        finally:
            c.close()


def ts(t):
    import datetime
    try:
        return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(t)


# ------------------------------------------------------------------ export
HTML_HEAD = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{title}</title><style>
body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#ededed;margin:0;padding:24px}}
.wrap{{max-width:900px;margin:0 auto}}
h1{{font-size:18px;color:#333}}
.day{{text-align:center;margin:22px 0 10px;color:#999;font-size:12px}}
.msg{{display:flex;margin:10px 0}}
.msg.me{{flex-direction:row-reverse}}
.bubble{{max-width:70%;background:#fff;border-radius:8px;padding:10px 13px;font-size:14px;line-height:1.5;
word-break:break-word;white-space:pre-wrap;box-shadow:0 1px 2px rgba(0,0,0,.06)}}
.msg.me .bubble{{background:#95ec69}}
.meta{{font-size:11px;color:#999;margin-bottom:4px}}
.msg.me .meta{{text-align:right}}
</style></head><body><div class="wrap"><h1>{title}</h1>{body}</div></body></html>"""


def export_html(acct, username, msgs, outpath):
    title = html.escape(acct.display_name(username))
    parts = []
    day = None
    for m in msgs:
        d = ts(m["time"])[:10]
        if d != day:
            day = d
            parts.append(f'<div class="day">{d}</div>')
        me = (m["sender"] == username) or (m["sender"] == "")
        cls = "msg me" if me else "msg"
        who = "我" if me else html.escape(m["sender_name"] or "?")
        body = html.escape(m["content"]).strip() or f"({describe(m['type'], '')})"
        parts.append(
            f'<div class="{cls}"><div class="bubble">'
            f'<div class="meta">{who} · {ts(m["time"])[11:16]}</div>{body}'
            f'</div></div>'
        )
    os.makedirs(os.path.dirname(outpath) or ".", exist_ok=True)
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(HTML_HEAD.format(title=title, body="".join(parts)))
    return len(msgs)


def cmd_decrypt(a):
    src, key = a.db, a.key
    if key and os.path.exists(key):
        key = open(key).read().strip()
    if not key:
        raise SystemExit("--key is required (64 hex chars, or a file containing it)")
    out = a.out or (os.path.basename(os.path.dirname(src)) + "_decrypted")
    pages = decrypt_file(src, out, key)
    print(f"OK {src} -> {out}  ({pages} pages)")


def cmd_sessions(a):
    acct = Account(a.dir)
    print(f"{'username':38} {'msgs':>6}  {'last time':20}  summary")
    for username, s in sorted(acct.sessions.items(), key=lambda kv: -(kv[1].get("last_timestamp") or 0)):
        n = ""
        print(f"{username:38} {n:>6}  {ts(s.get('last_timestamp') or 0):20}  {(s.get('summary') or '')[:60]}")


def cmd_export(a):
    acct = Account(a.dir)
    out = a.out or "archive"
    targets = []
    if a.chat:
        targets = [a.chat]
    else:
        targets = [u for u in sorted(acct.sessions, key=lambda u: -(acct.sessions[u].get("last_timestamp") or 0))]
    total = 0
    for username in targets:
        msgs = acct.messages(username, limit=a.limit)
        if not msgs:
            print(f"  skip {username}: no message table")
            continue
        safe = re.sub(r"[^A-Za-z0-9_.@-]", "_", username)
        p = os.path.join(out, f"{safe}.html")
        export_html(acct, username, msgs, p)
        with open(os.path.join(out, f"{safe}.json"), "w", encoding="utf-8") as f:
            json.dump({"username": username, "display_name": acct.display_name(username),
                       "messages": msgs}, f, ensure_ascii=False, indent=1)
        total += len(msgs)
        print(f"  {acct.display_name(username):22} {len(msgs):>7} messages -> {p}")
    print(f"\ndone: {total} messages exported to {out}")


def main():
    ap = argparse.ArgumentParser(description="Standalone WeChat local archive toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("decrypt", help="decrypt an encrypted .db with a known passphrase")
    p.add_argument("--db", required=True)
    p.add_argument("--key", required=True, help="64 hex chars, or path to a file containing it")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_decrypt)

    p = sub.add_parser("sessions", help="list sessions in a decrypted account dir")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("export", help="export chat(s) to HTML + JSON")
    p.add_argument("--dir", required=True)
    p.add_argument("--chat", default=None, help="username (default: all sessions)")
    p.add_argument("--all", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", default="archive")
    p.set_defaults(func=cmd_export)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
