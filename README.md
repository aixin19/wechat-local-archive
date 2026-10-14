# wechat-local-archive

**把微信聊天记录完整保存到自己的电脑上 —— 独立、只读、不依赖任何商业软件。**

本仓库提供一组小工具，用一条已知的数据库口令（passphrase）完成三件事：

1. **解密**微信 4.x 的 SQLCipher 数据库
2. **读取**会话、联系人、消息（含 zstd 压缩内容）
3. **导出**成带样式的 HTML 和结构化 JSON

不依赖 WeChatDataAnalysis、不依赖任何原生 core、不需要 GUI。

---

## 为什么会有这个仓库

微信 4.1.10 起采用 **「零驻留」** 策略：数据库密钥派生后即用即弃，稳态内存中不再有
任何可寻形态。同时多个相关工具因 DMCA / 律师函下架（调研时 `0xlane/wechat-dump-rs`
返回 HTTP 451）。

所以正确策略不是"以后想办法再取一次密钥"，而是：**现在就把口令和明文数据保存好，
以后任何微信版本都能自己解密。**

关于密钥到底怎么来的、为什么现在扫内存拿不到，见
[docs/密钥提取技术报告.md](docs/密钥提取技术报告.md)。

---

## 快速开始

```bash
pip install cryptography zstandard
```

### 1. 解密一个数据库

```bash
python wxarchive.py decrypt \
  --db  "D:/WeChat/xwechat_files/<account>/db_storage/session/session.db" \
  --key <64位hex口令> \
  --out out/session.db
```

- 会先做 **page 1 的 HMAC 校验**，口令错误会直接报错，不会产出垃圾文件
- `--key` 也可以传一个文件路径（文件里放那串 hex）

### 2. 列出会话

```bash
python wxarchive.py sessions --dir out/
```

### 3. 导出

```bash
python wxarchive.py export --dir out/ --all --out archive/          # 全部会话
python wxarchive.py export --dir out/ --chat 12345678@chatroom      # 指定会话
python wxarchive.py export --dir out/ --all --limit 500             # 每会话最多 500 条
```

每个会话产出 `.html`（气泡样式）+ `.json`（结构化）。

> ⚠️ 只解密 `session.db` 时只能看会话列表。要导出某人的完整消息，需要把该账号
> `db_storage` 下的 `.db` 一并解密到同一个目录（工具会自动在 `message_N.db` 里找表）。

---

## 独立解密器

`standalone_decrypt.py` 是一个零依赖（除 `cryptography`）的参考实现：

```bash
python standalone_decrypt.py <加密.db> <输出.db> <64位hex口令>
```

它和 SQLCipher 4 的标准一致：

| 参数 | 值 |
| --- | --- |
| 页大小 | 4096 |
| 加密 | AES-256-CBC |
| KDF | PBKDF2-HMAC-SHA512，**256,000** 轮 |
| MAC key | PBKDF2(enc_key, salt XOR 0x3A, 2 轮) |
| 保留区 | 80 字节 = IV 16 + HMAC 64 |
| 校验 | HMAC-SHA512(page[16:4016] + 小端页号) |

---

## 仓库结构

```
wxarchive.py                     独立工具：decrypt / sessions / export
standalone_decrypt.py            零依赖解密参考实现
docs/
  wxarchive-使用说明.md            工具用法 + 数据结构说明
  备份方案.md                     四层备份策略
  密钥提取技术报告.md              为什么扫内存拿不到密钥（含 8 种方法评级）
  vendor/                        第三方技术文档存档（见下方致谢）
tools/
  extract_asar.py                无需 Node 的 Electron asar 解包器
  verify_provenance.py           验证安装版与公开源码是否一致
  dll_scan_repro.py              复现 DLL 特征扫描（含密码学校验）
  memory_key_scan.py             V4 内存扫描的忠实移植
  raw_mem_search.py              原始内存明文搜索（决定性判定）
  hunt_all_memory.py             全内存类型扫描（IMAGE/MAPPED/PRIVATE）
  hunt_key_encodings.py          多编码搜索（UTF-16/hex/base64）
  test_universal_key.py          测试口令是否对所有库通用
analysis/
  findings-backend.md            某桌面工具的 Python 后端剖析
  findings-frontend.md           其前端 + IPC + HTTP API 契约
  findings-build.md              其构建流水线、复刻步骤与许可状况
  README-复刻指南.md              源码溯源与复刻指南
```

---

## 实测结论（微信 4.1.15.13 / Windows）

| 项目 | 结果 |
| --- | --- |
| 口令跨多个库通用 | ✅ 10 个数据库各有独立 salt，同一口令全部通过 HMAC |
| 独立解密 vs 商业工具解密 | ✅ 表结构逐张一致（550 vs 550 会话） |
| 内存中搜索口令明文 | ❌ 869 MB / 8 种编码 / 3 种内存类型，**全部不存在** |
| 上游 YARA 特征扫描 | ❌ 4492 次假匹配，0 个通过验证 |

**结论：解密完全可行；但"扫描内存取密钥"在 4.1.10+ 上原理性失效。**

---

## 风险与合规

- **仅用于处理你自己合法持有的数据。** 读取他人聊天记录可能违法。
- 商业工具的高版本适配依赖私有原生组件，注入类方案
  （Frida / DLL 注入）**有真实封号案例**，主号请避免。
- 口令等同账号密码，**不要提交到任何仓库、不要发给任何人**。
- 本仓库的工具全部**只读**打开数据库（`mode=ro`），不会修改原始数据。

---

## 致谢

`docs/vendor/` 下的技术文档来自第三方，版权归原作者：

- [NinjaSln-labs/wxlocal](https://github.com/NinjaSln-labs/wxlocal) — 微信 4.1.13 Windows 数据库解密研究
- [ZedeX/weixin-decrypte-script](https://github.com/ZedeX/weixin-decrypte-script) — Windows 微信 4.x 本地数据解密指南

其他参考资料：[TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool)、
[ycccccccy/wx_key](https://github.com/ycccccccy/wx_key)、
[chclt/oh-my-wechat](https://github.com/chclt/oh-my-wechat)、
[hicccc77/WeFlow](https://github.com/hicccc77/WeFlow)

---

## 许可

本仓库自有代码以 MIT 许可发布（见 `LICENSE`）。
`docs/vendor/` 下的文档版权归原作者，转载仅为存档，请遵循其原始许可。
已知问题：本仓库**不含**密钥提取工具（微信 4.1.10+ 已无法被动提取），
也不分发任何微信安装包或破解组件。
