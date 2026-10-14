# wxarchive — 独立微信本地记录工具

**不依赖 WeChatDataAnalysis、不依赖原生 core、不需要 GUI。** 只需要两点之一：

- 加密的 `.db` + 64 位 hex 口令
- 或已经解密好的 `.db` 目录

依赖：`cryptography`（解密用）、`zstandard`（约 30% 的消息是 zstd 压缩的）

```powershell
& 'C:\Users\<user>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe' -m pip install cryptography zstandard
```

---

## 三条命令

### 1. `decrypt` — 用口令解密单个数据库

```powershell
python wxarchive.py decrypt --db "D:\WeChat\xwechat_files\<账号>\db_storage\session\session.db" `
                            --key <64位hex> --out out\session.db
```

- 先用 **page 1 的 HMAC** 验证口令，错了会直接报错（不会产出垃圾文件）
- 口令也可以是一个文件路径（文件里放那串 hex）

### 2. `sessions` — 列出会话

```powershell
python wxarchive.py sessions --dir out\
```

### 3. `export` — 导出成 HTML + JSON

```powershell
python wxarchive.py export --dir out\ --all --out archive\        # 全部会话
python wxarchive.py export --dir out\ --chat 12345678@chatroom    # 指定会话
python wxarchive.py export --dir out\ --all --limit 500           # 每个会话最多 500 条
```

产出：每个会话一个 `.html`（气泡样式，像微信）和一个 `.json`（结构化，便于二次处理）。

---

## 它靠什么工作（数据结构）

| 你知道的 | 它从哪来 |
| --- | --- |
| 会话列表 | `session.db` 的 `SessionTable`（`username`, `summary`, `last_timestamp`） |
| 联系人显示名 | `contact.db` 的 `contact`（`remark` → `nick_name` → `alias`，优先备注） |
| 某人的消息表 | `Msg_<md5(username)>`，在 `message_N.db` 里 |
| 消息内容 | `Msg_*` 表的 `message_content`，`WCDB_CT_message_content == 4` 时是 **zstd** 压缩 |
| 发送者 | `real_sender_id` → 同库 `Name2Id(rowid, user_name)` |

已验证的映射：**`Msg_<表名后缀> == md5(user_name)`**，200/200 精确命中。

> ⚠️ 已知坑（来自社区经验）：`message_0.db`（最新）的 `real_sender_id` 是全局 `Name2Id` 行号；
> 更早的分片（`message_1/2.db`）可能用的是会话内角色编号，含义甚至相反。
> 本工具的 `find_table()` 会优先在**最新分片**里找表，但**多分片场景下发送者归属需要人工抽查**。

---

## 消息类型对照（`local_type & 0xFFFF`）

| 值 | 类型 | 值 | 类型 |
| --- | --- | --- | --- |
| 1 | 文本 | 47 | 表情 |
| 3 | 图片 | 48 | 位置 |
| 34 | 语音 | 49 | 链接/文件/红包/转账/小程序 |
| 42 | 名片 | 50 | 通话 |
| 43 | 视频 | 10000 | 系统消息（撤回、入群通知等） |

消息正文若为 XML（如 type 49），工具会尽量抽出 `<title>` 作为摘要；完整 XML 保留在 JSON 里。

---

## 实测结果（你本机，2026-10-15）

| 项目 | 结果 |
| --- | --- |
| `decrypt` 加密库 → 明文 | ✅ 142 页，`bad_hmac_pages=0`，通过 page-1 校验 |
| `sessions` | ✅ 550 个会话 / 13515 个联系人 |
| `export` 3 个群聊 | ✅ 120 条消息，中文/发送者/zstd 全部正确 |
| 与软件自身解密结果对比 | ✅ 表结构逐张一致（550 vs 550 会话） |

---

## 数据安全提醒

- `wxarchive.py` **只读**数据库（用 `mode=ro` 打开），不会改动你的原始数据
- 口令 = 你聊天记录的钥匙，等同账号密码
- 导出目录请放在你自己控制的磁盘，别进云同步
