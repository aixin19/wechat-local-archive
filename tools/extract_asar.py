#!/usr/bin/env python3
"""Minimal portable ASAR (Electron archive) extractor.

Usage: python extract_asar.py <archive.asar> <outdir> [--list]
"""
import json
import os
import struct
import sys


def read_header(f):
    # ASAR: pickle-ish header.  First 4 bytes uint32 = size of the
    # "size payload" pickle, next 4 bytes uint32 = size of the JSON string,
    # then that many bytes of JSON, then 4 bytes uint32 = JSON string length again.
    # Layout observed in electron asar archives:
    #   0x00 uint32  size of the first pickle scalar (== 4)
    #   0x04 uint32  json_len + 8
    #   0x08 uint32  json_len + 4
    #   0x0C uint32  json_len
    #   0x10 ...     json_len bytes of UTF-8 JSON header
    #   data_offset = 16 + json_len
    f.seek(12)
    (json_len,) = struct.unpack("<I", f.read(4))
    f.seek(16)
    header_json = f.read(json_len)
    header = json.loads(header_json.decode("utf-8"))
    data_offset = 16 + json_len
    return header, data_offset


def walk(node, prefix, out):
    for name, meta in node.get("files", {}).items():
        path = prefix + "/" + name if prefix else name
        if "files" in meta:
            out.append((path + "/", meta))
            walk(meta, path, out)
        else:
            out.append((path, meta))


def main():
    archive = sys.argv[1]
    outdir = sys.argv[2]
    list_only = "--list" in sys.argv
    with open(archive, "rb") as f:
        header, data_offset = read_header(f)
        entries = []
        walk(header, "", entries)
        if list_only:
            for path, meta in entries:
                if not path.endswith("/"):
                    print(f"{meta.get('size', 0):>10}  {path}")
            return
        for path, meta in entries:
            target = os.path.join(outdir, path.replace("/", os.sep))
            if path.endswith("/"):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if meta.get("unpacked"):
                continue
            offset = int(meta["offset"])
            size = int(meta["size"])
            f.seek(data_offset + offset)
            data = f.read(size)
            with open(target, "wb") as o:
                o.write(data)
        print(f"extracted {sum(1 for p,_ in entries if not p.endswith('/'))} files -> {outdir}")


if __name__ == "__main__":
    main()
