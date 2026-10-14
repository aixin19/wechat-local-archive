# WeChatDataAnalysis 原代码还原与复刻指南

> 结论先行：**本机安装的 `<install-dir>\WeChatDataAnalysis`（v2.9.0）就是开源项目
> [LifeArchiveProject/WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis)
> 的公开源码构建版本。** 它的 Electron 主进程源码、Python 后端源码、Nuxt 前端源码在 GitHub 上
> 全部可获取，且已逐字节核对通过。**但"写入/发送/自动化"（高级版）的实现不在公开仓库里。**

本文件是索引与结论汇总，三份深度分析见：

| 文件 | 内容 |
| --- | --- |
| [findings-backend.md](findings-backend.md) | Python 后端：入口、模块清单、解密原理、原生组件、依赖 |
| [findings-frontend.md](findings-frontend.md) | Nuxt 前端 + Electron IPC + HTTP API 契约 |
| [findings-build.md](findings-build.md) | 构建流水线、复刻步骤、许可与法务、哪些是不公开的 |

---

## 1. 它是"什么东西"：三层结构

桌面快捷方式指向的不是一个单独的 exe，而是三个进程协同：

```
WeChatDataAnalysis.exe        ← Electron 外壳（Chromium + Node），负责窗口、托盘、更新、启动后端
   └─ resources/app.asar/src/*.cjs   ← 主进程源码（未压缩、可读）
   └─ resources/backend/wechat-backend.exe  ← PyInstaller 打包的 Python 后端（FastAPI + uvicorn）
        └─ resources/backend/native/{wechatdb_client.dll, wechatdb_broker.exe, wechatdb_native_build.json}
                                          ← WCDB 原生读取组件（关键，见 §4）
   └─ resources/ui/                        ← Nuxt 静态生成的前端页面，由 Python 后端在 / 上提供服务
```

- 界面是在浏览器里打开 `http://127.0.0.1:10392/` 显示的；该 URL 由 **Python 后端自己**托管静态页面
  （`_SPAStaticFiles` + `_maybe_mount_frontend`，`src/wechat_decrypt_tool/api.py`），Electron 只是套壳。
  源码中**没有任何 `file://` 或 `loadFile()` 调用**。
- 你的实际运行端口是 `10393`（见 `%APPDATA%\wechat-data-analysis-desktop\desktop-settings.json`
  的 `backendPort`），默认值才是 `10392`。

---

## 2. 原代码在哪里：仓库与版本对应关系

| 项目 | 值 |
| --- | --- |
| 仓库 | https://github.com/LifeArchiveProject/WeChatDataAnalysis |
| 分支 / 标签 | `main` / `v2.9.0` |
| 你的安装版本 | 2.9.0（注册表 `WeChatDataAnalysis 2.9.0`；`resources/app.asar/package.json` → `wechat-data-analysis-desktop@2.9.0`） |
| v2.9.0 对应 commit | `d8cdf253d2a681d4f14b360e952e3a628f2ba0b2`（2026-10-11T05:51:36Z） |
| 仓库 HEAD | `cc919673ab8a213045ba62e17bf4319516ae47c5`（2026-10-11T06:06:53Z，"ci: refresh macOS source runtime pin"） |
| 公开文件总数 | 1284 个 blob |
| 许可 | **无 LICENSE 文件**（GitHub API 返回 `license: null`）→ 默认"保留所有权利" |

### 2.1 怎么确认"装的就是它"（可复现）

1. `resources/app.asar` 被解出后，`src/` 下 16 个 `.cjs` 源码文件与仓库 `desktop/src/` 同名文件
   **逐字节一致**（仅换行符 LF→CRLF 不同，打包器所为）。
2. 16/16 文件对 **`main` 和 `v2.9.0` 两个引用都一致**；两个图标 `.ico/.png` 连换行归一化都不需要，二进制完全一致。
3. Python 侧 `src/**/*.py` 共 **184 个文件，`main` 与 `v2.9.0` 之间哈希完全相同**。
4. `wechat-backend.exe` 内嵌 `PyInstaller`、`pyi-`、`_MEIPASS` 标记，版本信息为
   `WeChatDataAnalysis Backend 2.9.0.0 / LifeArchiveProject` → 确认是同一套 Python 后端的 PyInstaller 打包产物。

复现命令（需联网）：

```powershell
& 'C:\Users\<user>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe' `
  <repo>\verify_provenance.py <repo>\app-asar\src main
# 输出：compared 16 files / identical 16 / RESULT: installed build matches public source exactly
```

配套脚本：`extract_asar.py`（免 Node 的 asar 解包器）、`verify_provenance.py`、`check_py_tag.py`。

---

## 3. 怎么复刻：最短路径（源码运行）

来源：`README.md` 的"从源码运行"章节，逐条核对过仓库脚本。

```bash
git clone https://github.com/LifeArchiveProject/WeChatDataAnalysis.git
cd WeChatDataAnalysis
uv sync --no-editable          # 后端依赖（需要 Python >= 3.11 与 uv）
cd desktop && npm ci && cd ..  # Electron 外壳
cd frontend && npm ci && cd .. # Nuxt 前端
cd desktop && npm run dev      # 一条命令拉起 Nuxt + Electron + 后端
```

`npm run dev`（即 `desktop/scripts/dev.cjs`）会自动：先解析/准备原生组件 → 启动 `nuxi dev`（3000 端口）
→ 启动 Electron → Electron 再以 `uv run --no-dev --extra voice-transcription main.py` 拉起后端。

只想跑后端 + 网页版：

```bash
uv run --no-sync main.py            # API: http://127.0.0.1:10392  （文档 /docs）
cd frontend && npm run dev          # 页面: http://localhost:3000
```

打安装包（`npm run dist`）完整链路：

```
dist:win → build:ui (nuxt generate + copy-ui.cjs → desktop/resources/ui)
         → build:backend (PyInstaller --onefile → resources/backend/wechat-backend.exe)
         → build:icon → electron-builder --win --x64 → dist\WeChatDataAnalysis-2.9.0-Setup.exe
```

细节（PyInstaller 参数、electron-builder 配置、NSIS、签名钩子）见 [findings-build.md](findings-build.md) §2–§6。

---

## 4. 复刻时真正的三个拦路虎

这一节是最重要的部分——**公开源码"能跑"，但"不能独立地重新打包出官方那种安装包"**。

### 4.1 原生 WCDB 组件不在公开仓库（最大障碍）

`wechatdb_client.dll`、`wechatdb_broker.exe`、`wechatdb_native_build.json` 这三个文件：
在公开仓库里**根本不存在**（被 `.gitignore` 排除，`desktop/resources/backend/` 只有 `.gitkeep`），
但后端启动时**硬性要求**它们存在，否则在 uvicorn 绑定端口之前就抛
`NativeCoreComponentMissingError` 直接退出（`native_core_client.py:1651-1688`）。

- 构建脚本 `desktop/scripts/build-backend.cjs` 强制要求环境变量 `WCE_NATIVE_CORE_ARTIFACT_DIR`；
- `docs/release-native-build.md` 指向**私有**生产者仓库 `2977094657/WCDB` 与密钥
  `WCE_NATIVE_CORE_PRODUCER_TOKEN`；原生组件有 **45 天有效期**，过期即拒绝启动。

**唯一的公开获取途径**：官方 Release 里有一套标着 `windows-source-runtime-*` 的公开运行时资产
（例如 `wechatdataanalysis-windows-source-runtime-x64-v1.tar.gz`，约 0.8 MB），
`desktop/src/windows-source-native-core-bootstrap.cjs` 正是通过
`https://github.com/LifeArchiveProject/WeChatDataAnalysis/releases/download/<tag>/<asset>`
自动下载它，并按 SHA-256 校验后解出上述三件套。也就是说：**源码模式能自动"取"到原生组件，
但那不是"从源码编译"出来的，而是从公开发布物中解包复用。**

> 注意时效：仓库里 `desktop/resources/native-core-source-windows.json` 钉的是 `v2.4.0`，
> `expiresAtUnix` = **2026-10-22T14:44:48Z**。到期后 `npm run dev` 会 fail-closed，
> 需要上游刷新 pin（注意：`main` 的 HEAD commit 恰好就是在做"刷新 source runtime pin"这件事）。
> 已安装的应用不受影响：它的原生组件已于 2026-10-11 随包提供，有效期到 **2026-11-25**。

### 4.2 代码签名是私有的

`desktop/resources/signing/` 同样只有 `.gitkeep`；electron-builder 配置为 `forceCodeSigning: true`
并使用自定义的 `windows-private-pki-sign.cjs`，发布者名为 `WDA Private PKI`。
打包前还要校验一张与 manifest 中指纹一致的私有根证书。**没有维护者的私钥，就做不出官方那种已签名
的 Setup.exe**；最多只能尝试（未经验证的）本地关闭强制签名的未签名构建。

### 4.3 "高级版"（写入/发送/自动化）实现不开源

README 原文：公开版本聚焦**解密、读取、导出、年度总结**等只读能力；高级版另加
"**77 项写入、在线动作与自动化能力**"，并明确写道
"当前公开版本仅展示功能说明与演示动画，**不包含高级功能的执行实现**"。

代码层面的三重佐证：

1. `.github/release-editions.md`：公开的是开源版源码，高级版为独立版本，其实现不属于本仓库开源范围；
   发布工作流从单独的 `advanced-latest` 发布中拉取 `advanced-*` 资产再一起发布。
2. 前端 `frontend/composables/chat/useChatEditing.js` 的 `onEditMessageClick()` 直接调用
   `openFeatureUnavailableDialog()`，提示语来自 `frontend/lib/developer-support.js`：
   "当前版本仅展示该功能入口，暂时无法执行……"。
3. 已安装的 `wechatdb_native_build.json` 自述为只读构建并被构建脚本强制校验：
   `distributionMode: "public"`、`readOnlyBuild: true`、`databaseWriteBuild: false`、`wechatActions: []`
   （原生日志：原生核宣称的微信动作数为 0）。`build-backend.cjs` 会在
   `readOnlyBuild !== true` 或 `wechatActions` 非空时**直接让构建失败**。

因此：**要复刻"读数据、看聊天、导出"完全可行；要复刻"改消息、发消息、自动化"在公开源码里没有实现可抄。**

---

## 5. 它是怎么读到微信数据的（核心原理）

公开源码里写得非常清楚，这也是最有复刻价值的部分。

**两条读取路径：**

| 路径 | 做法 | 用在哪 |
| --- | --- | --- |
| 离线解密 | 纯 Python 实现 SQLCipher 分页解密，产出明文 SQLite | `wechat_decrypt.py`，输出到 `output/databases/` |
| 实时只读 | 通过 `wechatdb_client.dll` + broker 直接读 WCDB | `native_core_*.py` / `wcdb_realtime.py` |

**离线解密的关键参数**（`wechat_decrypt.py`）：

- 页大小 4096；AES-256-CBC；PBKDF2-HMAC-SHA512、**256000** 轮、dkLen 32；
- MAC key = PBKDF2(enc_key, salt XOR 0x3A, 2 轮)；页尾保留 80 字节 = IV 16 + HMAC 64；
- 第 1 页用 HMAC-SHA512 校验做真实性验证（不会误判错误密钥）；
- 项目**没有** `pysqlcipher` 依赖，加密全部用 `cryptography`/`pycryptodome` 自己实现。

**数据库密钥怎么来的**（Windows）：

1. 默认走 `key_v4.py` 内存扫描：对微信进程做 YARA 特征扫描（`GetKeyAddrStub`）找指针槽，
   读取 32 字节候选密钥，用"第 1 页 HMAC 校验"逐个验证（多进程并行）；
2. 候选密钥还需与 `dll_key_scan.py` 从 `Weixin.dll` 代码段里 PE 扫描出的"内部密钥"做 XOR 还原；
3. 兜底方案是 `wx_key` 钩子（会重启微信）；也始终支持直接手填 64 位十六进制密钥；
4. macOS 用单独签名的 helper（`macos-key-extractor/`）。

**图片密钥是另一套**：`image_key_resolver.py` / `image_key_memory_scan.py` + kvcomm 扫描，
（可选）还会调用远程接口换取 `xorKey`/`aesKey`。

**它读取的目录**（`%USERPROFILE%\Documents\xwechat_files\...`）：各账号 `db_storage\**\*.db`、
`all_users\config\global_config`（AES-128-CFB，固定密钥 `xwechat_crypt_ke`）、
`%APPDATA%\Tencent\xwechat\net\kvcomm`、`key_info.db`、`Weixin.dll` 等。详见
[findings-backend.md](findings-backend.md) §3.6。

---

## 6. 许可与合规（事实陈述，非法律意见）

- 仓库**没有任何 LICENSE 文件**，GitHub API `license: null`。按默认著作权规则，项目自身代码属
  **保留所有权利**：本地自己用没问题，但**复制、修改、再分发（包括发布衍生安装包）需要版权方许可**。
- README 的"安全说明/免责声明"要求：**仅限个人使用、只解密自己的数据**、密钥与数据自行保管、
  与腾讯/微信无任何隶属或授权关系、内存扫描与 Hook 存在账号提示/失效风险、按"现状"提供不担保。
- `THIRD_PARTY_NOTICES.md` 只约束内嵌的第三方组件（Lobe Icons MIT、faster-whisper/CTranslate2/PyAV/
  OpenCC/ONNX Runtime、wcdb-key-tool MIT、WeFlow、NVIDIA CUDA 轮子等），**不构成对项目自身代码的授权**。
- 官方签名使用的是维护者私有 PKI，你无法复现同一签名链。

---

## 7. 参考链接

- 仓库：https://github.com/LifeArchiveProject/WeChatDataAnalysis
- 发布页（含 `windows-source-runtime-*` 原生运行时资产）：https://github.com/LifeArchiveProject/WeChatDataAnalysis/releases
- v2.9.0：https://github.com/LifeArchiveProject/WeChatDataAnalysis/releases/tag/v2.9.0
- 官网（仓库内也有一份 `website/`）：https://lifearchiveproject.github.io/WeChatDataAnalysis/
- 同类参考项目（取自本仓库 README「致谢」章节，链接为按作者/仓库名拼写）：
  [H3CoF6/WeQ](https://github.com/H3CoF6/WeQ)、
  [0xlane/wechat-dump-rs](https://github.com/0xlane/wechat-dump-rs)、
  [ycccccccy/wx_key](https://github.com/ycccccccy/wx_key)、
  [ycccccccy/echotrace](https://github.com/ycccccccy/echotrace)、
  [hicccc77/WeFlow](https://github.com/hicccc77/WeFlow)、
  [chclt/oh-my-wechat](https://github.com/chclt/oh-my-wechat)
