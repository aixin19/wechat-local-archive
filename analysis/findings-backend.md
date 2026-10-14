# WeChatDataAnalysis v2.9.0 — Python Backend Findings

**Scope.** How the Python/FastAPI backend of [LifeArchiveProject/WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis) works, for someone who wants to replicate it. Backend source root: `src/wechat_decrypt_tool/` (package version `2.9.0`, entry scripts `main.py` and `backend_entry.py`).

**Primary sources (all fetched from upstream `main`, tag `v2.9.0`; repo is public):**

- Repo tree: `GET https://api.github.com/repos/LifeArchiveProject/WeChatDataAnalysis/git/trees/main?recursive=1` (1 380 blobs; cached at `<repo>\upstream_tree.json`).
- Raw files: `https://raw.githubusercontent.com/LifeArchiveProject/WeChatDataAnalysis/main/<path>`.
- Local installed artifact for unpackaged native files: `<install-dir>\WeChatDataAnalysis\resources\backend\` (not in git; see §4 and §6).

**Citation convention:** every file claim cites the exact upstream path. URLs are written as `raw/…` meaning `https://raw.githubusercontent.com/LifeArchiveProject/WeChatDataAnalysis/main/<path>`, and `blob/…` meaning the GitHub web view. Line numbers are from the fetched `main` revision (which the Lead verified is byte-identical to tag `v2.9.0` for `desktop/src/*.cjs`; the Python tree was not tag-diffed here — see UNVERIFIED notes at the end).

---

## 0. TL;DR architecture

```
Electron desktop shell
   └─ spawns  resources/backend/wechat-backend.exe   (PyInstaller onefile of src/wechat_decrypt_tool)
                 │
                 ├─ backend_entry.py / main.py
                 │     • configure_native_core_entrypoint()  ← REQUIRES wechatdb_client.dll + wechatdb_broker.exe + wechatdb_native_build.json
                 │     • uvicorn.run(wechat_decrypt_tool.api:app, port from WECHAT_TOOL_PORT | output/runtime_settings.json | 10392)
                 │
                 ├─ FastAPI app (api.py) + 26 routers, CORS *, SPA static mount of Nuxt output
                 │
                 ├─ Key layer: key_service.py + key_v4.py (pymem/yara memory scan) + dll_key_scan.py (PE scan)
                 │              fallback: wx_key hook   |   macOS: wda_xkey_helper / LLDB
                 │
                 ├─ Read paths
                 │     • offline: wechat_decrypt.py → AES-256-CBC page decrypt → plaintext SQLite in output/databases
                 │     • realtime: wcdb_realtime.py / native_core_*.py → wechatdb_client.dll broker, read-only WCDB
                 │
                 └─ Media: VoipEngine.dll (WxAM), img_helper.dll (hook toggle), weflow wasm (SNS) via Electron/Node
```

The public release ships a **read-only, source-public native core**: `readOnlyBuild: true`, `databaseWriteBuild: false`, `wechatActions: []` (`<install-dir>\WeChatDataAnalysis\resources\backend\native\wechatdb_native_build.json:10-12`). There is no WeChat-write capability in the backend.

---

## 1. Entry points and app assembly

### 1.1 Entry scripts

| File | Role | Key evidence |
|---|---|---|
| `main.py` | Developer/source launcher. `uv run main.py`. Prepends repo `src/` to `sys.path`, calls `multiprocessing.freeze_support()`, starts desktop watchdog + native-core handshake, then `uvicorn.run("wechat_decrypt_tool.api:app", ...)`. Default port **10392**. | raw/main.py:23-27, 43-48, 82-100 |
| `src/wechat_decrypt_tool/backend_entry.py` | PyInstaller entry. Same handshake, imports the app object directly (`from wechat_decrypt_tool.api import app`) to avoid the dynamic `"pkg:app"` import string. Adds smoke modes `--smoke-ai`, `--smoke-opencc`, `--smoke-watchfiles`, `--smoke-sns-wasm`. | raw/src/wechat_decrypt_tool/backend_entry.py:1-5, 89-111 |
| `src/wechat_decrypt_tool/api.py` | Builds the `FastAPI` app, installs middleware, includes all routers, mounts the Nuxt SPA. Has its own `__main__` block. | raw/src/wechat_decrypt_tool/api.py:57-160, 352-361 |
| `src/wechat_decrypt_tool/__init__.py` | `__version__ = "2.9.0"`, `__author__ = "WeChat Decrypt Tool"`. | raw/src/wechat_decrypt_tool/__init__.py:4-5 |

### 1.2 FastAPI app assembly (`api.py`)

Order of operations:

1. `setup_logging()` + sensitive-query log filter (`api.py:17-19`).
2. `app = FastAPI(title="微信数据库解密工具", version=APP_VERSION)`; `app.router.route_class = PathFixRoute` (custom Starlette route that repairs backslash/relative paths in JSON bodies — `path_fix.py:1`). (`api.py:57-64`)
3. `CORSMiddleware`: `allow_origins=["*"]`, `allow_credentials=True`, all methods/headers, exposes `X-WCDA-AI-Trace`, `X-WCDA-AI-Diagnostic`. (`api.py:67-74`)
4. Two `@app.middleware("http")`: `_log_server_errors` and `_record_content_free_product_events` (content-free telemetry for exports/page views). (`api.py:77-126`)
5. `ChatRequestPerfMiddleware` (opt-in ASGI timing). (`api.py:129`)
6. Imports and `include_router(...)` for **26 routers** (see §2.2) — note the tree has 27 files under `routers/` but one is `__init__.py` (empty), so there are 26 actual routers, not 27. (`api.py:132-160`, raw/src/wechat_decrypt_tool/routers/)
7. Deterministic MIME overrides for `.js/.mjs/.css/.json/.svg`. (`api.py:163-172`)
8. `_maybe_mount_frontend()`: mounts `_SPAStaticFiles` at `/` from `$WECHAT_TOOL_UI_DIR` or `frontend/.output/public`, with `200.html`/`index.html` fallback and long-cache for `_nuxt/*`. (`api.py:175-272`)
9. Startup hooks: `configure_native_core_entrypoint()`, `ai.lifecycle.start_services()`, `WCDB_REALTIME.start_background_prime()`, `CHAT_REALTIME_AUTOSYNC.start()`, `SNS_REALTIME_AUTOSYNC.start()`. (`api.py:275-302`)
10. Shutdown hooks: stop AI services, autosyncs, `WCDB_REALTIME.close_all()`, `IMG_HELPER.disable()`, `shutdown_product_telemetry()`. (`api.py:305-349`)

> The **native-core handshake happens before the app is imported** in both entry points (`main.py:46`, `backend_entry.py:105`), i.e. a missing `wechatdb_*` component prevents the backend from starting at all.

### 1.3 Port / host resolution and data directories

Precedence is implemented in `runtime_settings.py` and used by both entry points.

| Setting | Env var | Settings file key | Default |
|---|---|---|---|
| Port | `WECHAT_TOOL_PORT` | `output/runtime_settings.json` → `backend_port` | **10392** |
| Host | `WECHAT_TOOL_HOST` | `runtime_settings.json` → `backend_host` | `127.0.0.1` (or `0.0.0.0` when `WECHAT_TOOL_ALLOW_REMOTE_CALLS` is truthy) |
| MCP token | `WECHAT_TOOL_MCP_TOKEN` | `runtime_settings.json` → `mcp_token` | generated `secrets.token_urlsafe(32)` |
| Reload | `WECHAT_TOOL_RELOAD=1` | — | off (source launcher only) |
| Data dir | `WECHAT_TOOL_DATA_DIR` | — | `Path.cwd()` |
| Output dir | `WECHAT_TOOL_OUTPUT_DIR` | — | `<data dir>/output` |

Evidence: `read_effective_backend_port` (`raw/src/wechat_decrypt_tool/runtime_settings.py:182-194`), `read_effective_backend_host` (same file `:218-229`), `default_backend_host` (`:49-52`), `ensure_mcp_token` (`:354-361`), `app_paths.py:6-36` (`get_data_dir`, `get_output_dir`, `get_output_databases_dir`, `get_account_keys_path`), `main.py:47-50, 82` (`WECHAT_TOOL_RELOAD`, LAN access host).

Derived layout: `<output>/databases/<account>/…` for decrypted SQLite, `<output>/account_keys.json` for the key store, `<output>/runtime_settings.json` for settings.

---

## 2. Module inventory

### 2.1 Top-level modules (`src/wechat_decrypt_tool/*.py`)

Purpose taken from each module's own docstring where present; otherwise inferred from its defined symbols and imports (marked *inferred*).

| Module | Purpose |
|---|---|
| `__init__.py` | Package version/author. |
| `api.py` | FastAPI app, CORS, middleware, router registration, SPA mount (see §1). |
| `backend_entry.py` | PyInstaller entry point + smoke-test subcommands. |
| `app_paths.py` | Writable data/output directory resolution via `WECHAT_TOOL_DATA_DIR`/`OUTPUT_DIR`; `account_keys.json` path. |
| `logging_config.py` | Unified loguru/std logging setup and sensitive-query filter. |
| `path_fix.py` | `PathFixRoute` — repairs Windows backslash/relative paths in JSON payloads. |
| `request_logging.py` | Server-error middleware + log-safe copies of request/error data. |
| `perf_trace.py` | Opt-in ASGI boundary timing middleware (`ChatRequestPerfMiddleware`). |
| `runtime_settings.py` | Port/host/MCP-token/voice-device settings (env > `runtime_settings.json` > default). |
| `network_access.py` | Picks a LAN-reachable local IPv4 for MCP advertising. |
| `platform_support.py` | Platform detection + macOS native-resource readiness and `runtime_capabilities()` matrix. |
| `source_fallback.py` | *Inferred:* fallback helper when an optional data source is unavailable. |
| `desktop_parent_watchdog.py` | *Inferred:* exits the backend when the Electron parent process dies (`start_desktop_parent_watchdog_from_env`). |
| `database_filters.py` | `should_skip_source_database()` — excludes non-chat/sidecar DB files. |
| `wechat_detection.py` | WeChat install/data-dir discovery, `global_config` parsing, account detection. |
| `wechat_decrypt.py` | SQLCipher 4.0 offline page decryptor → plaintext SQLite; key validation. |
| `key_service.py` | Key workflows: V4 memory scan, `wx_key` hook, image keys, remote image-key API. |
| `key_v4.py` | WeChat 4.x key recovery from process memory (pymem + YARA + PBKDF2/HMAC verify). |
| `key_store.py` | `output/account_keys.json` read/upsert/remove, aliases, raw-key-cache purge. |
| `dll_key_scan.py` | PE-scan `Weixin.dll` for the 32-byte internal DB key (`mov rdx`×4 pattern). |
| `image_key_memory_scan.py` | Image-key recovery from WeChat process memory (Windows + macOS, lazy platform loading). |
| `image_key_resolver.py` | Deterministic kvcomm/template-based image-key resolution (WeFlow-compatible). |
| `img_helper.py` | Loads/hooks `native/img_helper.dll` (`InitImgHelper`/`UninstallImgHelper`), singleton `IMG_HELPER`. |
| `isaac64.py` | Pure-Python ISAAC-64 keystream fallback for SNS video decryption. |
| `media_helpers.py` | Media path/account resolution, WxAM (`VoipEngine.dll`) and wxgf/HEVC decoding, image helpers. |
| `avatar_cache.py`, `cdn_image_service.py` | Avatar SQLite cache; WxCDN client (`wxcdn.c3o.re`) for token/quota/redeem/media download. |
| `chat_accounts.py`, `chat_helpers.py`, `chat_realtime_reader.py`, `chat_search_index.py`, `session_last_message.py` | Account resolution, time formatting/formatting helpers, realtime reader, FTS search index, last-message index. |
| `chat_export_service.py`, `chat_incremental_export.py` | Full and incremental chat export (ZIP/HTML/Excel etc.). |
| `chat_realtime_autosync.py` | Optional WCDB-realtime → decrypted-SQLite background autosync. |
| `sns_export_service.py`, `sns_full_sync.py`, `sns_media.py`, `sns_realtime_autosync.py` | Moments export, full-cache sync, remote media download+decrypt, file-event autosync. |
| `asr_backends.py`, `asr_models.py`, `asr_worker.py` | Voice-ASR backends, pinned model manifests, cancellable worker process. |
| `voice_transcription.py`, `native_voice_transcription.py`, `native_core_voice_asr.py`, `qwen_gpu_runtime.py` | Whisper/Sherpa/Qwen ASR dispatch, native-core ASR, CUDA runtime bootstrap. |
| `native_core_client.py` | ctypes ABI to `wechatdb_client.dll`: build-manifest validation, lease, DB read, export, ASR. |
| `native_core_broker.py` | Broker process lifecycle + `managed_native_core_operation` lease. |
| `native_core_database.py` | sqlite3-like `NativeCoreDatabaseSource` over the native client. |
| `native_core_lease.py` | Online lease/device-proof/license policy. |
| `native_core_dev_lease.py` | Offline dev-local lease issuance (source builds only). |
| `native_core_device_credential.py` | Encrypted per-device credential store (DPAPI). |
| `native_core_export.py` | Native sealed/encrypted export (WES1/WES2 envelopes). |
| `native_core_raw_key_cache.py` | Per-DB derived raw-key cache (DPAPI-backed). |
| `native_core_realtime.py` | Realtime account/message/cursor access through the broker. |
| `native_core_telemetry.py` | Content-free product events. |
| `export_integrity.py` | Loads `wce_integrity.pyd` and seals exports (CSS assets, manifest, signature sidecars). |
| `wcdb_realtime.py` | Manager/connection pool over native-core realtime (status, prime, close). |
| `sqlite_diagnostics.py`, `sqlite_wal.py` | Integrity/repair diagnostics on decrypted copies; WAL snapshot merge. |
| `xlsx_export.py` | Excel export helpers (column labels, etc.). |
| `account_identity.py`, `account_source_policy.py` | *Inferred:* account identity/aliasing and which source dir an account may read. |
| `logging_config.py` etc. — see above | |
| macOS key capture: `macos_db_key_capture.py`, `macos_db_key_helper.py`, `macos_db_key_discovery.py`, `macos_inplace_capture.py`, `macos_clone_capture.py`, `macos_native_capture.py`, `macos_capture_validation.py` | LLDB/entitlement-based macOS passphrase capture paths and fail-closed validation. |

### 2.2 Routers (`src/wechat_decrypt_tool/routers/`, 26 + `__init__.py`)

`prefix` column is exact from each module's `APIRouter(...)` call (blank = full paths written per-endpoint). Endpoint counts are the `@router.*` decorators found in the file.

| Router | Prefix / route class | Purpose | Main endpoints |
|---|---|---|---|
| `health.py` | `PathFixRoute` | Liveness/root. | `GET /api`, `GET /api/health` |
| `decrypt.py` | `PathFixRoute` | Run offline decryption of one account's `db_storage` with a 64-hex key; persist key only after per-DB verification. | `POST /api/decrypt`, `GET /api/decrypt_stream` |
| `keys.py` | `PathFixRoute` | Get/save DB keys, macOS capture, image keys. | `GET /api/keys`, `GET /api/get_keys`, `GET/POST /api/macos-key-capture/*`, `GET /api/get_image_key`, `POST /api/get_image_key_memory` |
| `wechat_detection.py` | `PathFixRoute` | Detect installed WeChat / current account / status. | `GET /api/wechat-detection`, `/api/current-account`, `/api/wechat/status` |
| `import_decrypted.py` | `PathFixRoute` | Preview/import an external decrypted DB snapshot. | `POST /api/import_decrypted/preview`, `/cancel`, `GET /api/import_decrypted` |
| `chat.py` | `PathFixRoute` | Realtime status/sync, search-index, accounts, sessions, messages, search, appmsg resolve. | 20 endpoints incl. `GET /api/chat/sessions`, `/api/chat/messages`, `/api/chat/search`, `POST /api/chat/realtime/sync_all` |
| `chat_realtime_sse.py` | `PathFixRoute` | Message-level SSE stream. | `GET /api/chat/realtime/messages` |
| `chat_contacts.py` | `PathFixRoute` | Contacts/groups/profile + contact export. | `GET /api/chat/contacts`, `/group_members`, `/profile`; `POST /api/chat/contacts/export(/seal)` |
| `chat_export.py` | `PathFixRoute` | Async chat export jobs + download/events. | `POST/GET /api/chat/exports`, `…/{id}/download`, `/files`, `/events`, `/commit` |
| `chat_media.py` | `PathFixRoute` | Avatars, images, video, voice, emoji, and the whole voice-transcription API (models, GPU, batch, native). | 27 endpoints incl. `GET /api/chat/media/voice`, `POST /api/chat/media/voice/transcription` |
| `sns.py` | `PathFixRoute` | Moments self-info, realtime sync, timeline, users, media. | `GET /api/sns/timeline`, `/users`, `/media`, `POST /api/sns/realtime/full_sync` |
| `sns_export.py` | `PathFixRoute` | Async Moments export jobs. | `POST/GET /api/sns/exports`, `…/{id}/download`, `/events` |
| `wrapped.py` | `PathFixRoute` | Annual-summary ("Wrapped") deck. | `GET /api/wrapped/annual`, `/annual/meta`, `/annual/cards/{card_id}` |
| `favorites.py` | plain `APIRouter()` | WeChat favorites + favorite voice media. | `GET /api/favorites`, `/api/favorites/media/voice` |
| `record_export.py` | plain `APIRouter()` | Export of chat "records" (merged chat logs). | `POST /api/records/export` |
| `biz.py` | `PathFixRoute` | Official-account (biz) image proxy, list, messages, pay records. | `GET /api/biz/list`, `/messages`, `/pay_records`, `/proxy_image` |
| `cdn.py` | plain `APIRouter()` | WxCDN plan/connect/redeem/token-duration; errors surfaced in body, not 500s. | `GET /api/cdn/plan`, `POST /api/cdn/connect|redeem|token_duration` |
| `general.py` | plain `APIRouter()` | Account overview and "general" features: friend verifications, mini-programs, finder, payments, revokes, search records. | `GET /api/general/overview` + 6 |
| `admin.py` | `PathFixRoute` | Log file view/open, frontend error reporting, port + MCP access/token admin. | `GET /api/admin/log-file`, `/port`, `/mcp-access`, `/mcp-token`; matching POSTs |
| `account_archive_export.py` | `PathFixRoute` | Account-level archive export (async) + import. | `POST /api/account/archive_export`, `GET …/{id}`, `/download`, `DELETE …/{id}` |
| `media.py` | `PathFixRoute` | Media key management + bulk media decrypt (stream). | `POST /api/media/keys`, `/api/media/decrypt_all`, `GET /api/media/resource/{md5}` |
| `mcp.py` | `PathFixRoute` | MCP endpoint: Bearer/`x-mcp-token` auth, JSON-RPC POST, skill bundle. See §2.3.6. | `GET /mcp`, `POST /mcp`, `GET /mcp/skill`, `/mcp/skill/bundle` |
| `ai.py` | `/api/ai` + `Depends(local_only)` | AI provider profiles, models, usage, conversations, tasks, rules, alerts, events. | 30 endpoints |
| `ai_agent.py` | `/api/ai/agent` + `Depends(local_only)` | DeepAgents threads/runs/materials/subtasks/events. | 17 endpoints |
| `local_search.py` | `/api/ai/local-search` + `Depends(local_only)` | Local semantic-search status/settings/index/device/GPU. | 11 endpoints |
| `system.py` | plain `APIRouter()` | Platform info, directory picker, img_helper toggle, CDN-image toggle. | `GET /api/system/platform`, `POST /api/system/img_helper/toggle`, `/api/system/cdn_image/toggle` |

`local_only` is defined in `routers/ai.py:20-31`: it rejects non-loopback clients/origins/hosts. (`raw/…/routers/ai.py:20-34`)

### 2.3 Sub-packages

#### 2.3.1 `ai/` — 57 files

Purpose from each module docstring. Two engines coexist: a "simple" AI service (`service.py`, `providers.py`, `storage.py`, `model_*`) and the official **DeepAgents** agent runtime (`deep_*`, `agent_*`).

| Group | Files |
|---|---|
| Core service | `__init__.py` (public AI services/chat workflows), `service.py`, `storage.py` (SQLite `AIStore`), `schemas.py`, `providers.py` (PRESETS/validation/usage audit), `reasoning_client.py`, `model_catalog.py` (models.dev catalog), `model_selection.py`, `model_reasoning.py`, `model_execution.py`, `model_scheduler.py` (global request scheduler), `recent_bounds.py`, `context_meter.py`, `compaction_policy.py`, `lifecycle.py` |
| Support | `diagnostics.py`, `diagnostics_http.py`, `runtime_check.py`, `messages.py`, `media.py` (PDF/Office extraction), `schemas.py` |
| `agent_*` (19) | `agent_service.py` (production engine = DeepAgents), `agent_tools.py`, `agent_workspace.py`, `agent_history.py`, `agent_context.py`, `agent_continuous.py`, `agent_events.py`, `agent_global.py`, `agent_live_search.py`, `agent_model.py`, `agent_notes.py`, `agent_reading.py`, `agent_references.py`, `agent_report.py`, `agent_budget.py`, `agent_citation_check.py`, `agent_subtasks.py`, `agent_timeline.py`, `agent_schemas.py` |
| `deep_*` (20) | `deep_runtime.py` (entry), `deep_backend.py` (virtual FS), `deep_tools.py`, `deep_model.py`, `deep_conversation.py`, `deep_context.py`, `deep_checkpoints.py`, `deep_dispatch.py`, `deep_partition.py`, `deep_planning.py`, `deep_projection.py`, `deep_subtasks.py`, `deep_sawtooth.py` (sawtooth compaction), `deep_guide.py`, `deep_calculation.py`, `deep_omissions.py`, `deep_validation.py`, `deep_synchronization.py`, `deep_agent*` companions |

Notable statements in docstrings: `deep_backend.py` "task-isolated virtual filesystem; never touches host paths"; `agent_service.py` "only production execution engine is official DeepAgents"; `diagnostics.py` whitelists metadata only.

#### 2.3.2 `local_search/` — 11 files

| File | Purpose |
|---|---|
| `__init__.py` | Optional local semantic search; no remote inference API. |
| `catalog.py` | Remote model endpoint/name/key overridden per account; protocol fixed to OpenAI-compatible `/v1/embeddings`. |
| `downloads.py` | Anonymous HuggingFace download jobs (resumable, one shared retry budget). |
| `frozen.py` | Frozen message snapshot: paged to disk, exact counts, no re-read of a changing source. |
| `gpu.py` | Versioned NVIDIA component install; does not modify system CUDA/Python. |
| `index.py` | Per-account index; snippets used for recall only, original message re-filtered on return. |
| `inference.py` | Isolated inference process; on CUDA failure process is destroyed and batch replayed on CPU. |
| `progressive.py` | All-account progressive indexing with committed batches queryable. |
| `reading.py` | Batch size bounded by free RAM, not GPU model. |
| `service.py` | Unified local-search task/scope/device service. |
| `totals.py` | Fixed totals published only after full snapshot. |

#### 2.3.3 `wrapped/` — 12 files

- `service.py` (annual-report orchestration), `storage.py` (per-account `<account>/_wrapped` working dir), `cards/` with 9 card modules: `card_00_global_overview`, `card_01_cyber_schedule`, `card_02_message_chars`, `card_03_reply_speed`, `card_04_emoji_universe`, `card_04_monthly_best_friends_wall`, `card_05_keywords_wordcloud`, `card_07_bento_summary`, plus `cards/__init__.py`. (There is no `card_06` in the tree.)

#### 2.3.4 `mcp/` — 5 files

| File | Purpose |
|---|---|
| `protocol.py` | JSON-RPC 2.0 + MCP `protocolVersion = "2025-06-18"`, `initialize`, `tools/list`, `tools/call`, parse errors. |
| `registry.py` | `McpToolRegistry`, `McpTool`, JSON-safe integer stringification, schema builders. |
| `tools.py` | 1 616 lines of tool implementations (status, accounts, contacts, sessions, messages, SNS, biz, wrapped, media URLs, mobile bundle, analytics) registered with `MCP_REGISTRY`. |
| `errors.py` | JSON-RPC error codes + `error_from_exception`. |
| `__init__.py` | "MCP integration for WeChatDataAnalysis." |

HTTP surface (`routers/mcp.py`): token via `Authorization: Bearer`, `x-mcp-token`, or `?token=`; `GET /mcp` → 405 "Use POST"; `POST /mcp` → JSON-RPC; `GET /mcp/skill[/bundle]` serves `skills/wechat-mcp-copilot/SKILL.md` + references.

#### 2.3.5 `native/` — 19+ entries

| Entry | Purpose |
|---|---|
| `wce_integrity.pyd` | Rust extension (export-integrity); loaded by `export_integrity.py`. Built from private crate `native/wce_integrity` by `tools/build_wce_integrity.ps1`. |
| `img_helper.dll` | Win32 hook DLL; loaded by `img_helper.py` (`InitImgHelper(pid)`). |
| `VoipEngine.dll` (18.5 MB) | Provides `wxam_dec_wxam2pic_5` used by `media_helpers.py:1259-1275` to decode WxAM images. |
| `SDL2.dll`, `msvcp140.dll`, `msvcp140_1.dll`, `vcruntime140.dll`, `vcruntime140_1.dll` | Runtime dependencies of the above DLLs. |
| `weflow_wasm/` | `weflow_wasm_keystream.js`, `wasm_video_decode.js`, `wasm_video_decode.wasm`, `sns_image_fixture.json` — WxIsaac64/video-decode keystream for SNS, run through Electron-as-Node or `node`. |
| `macos/universal/image_scan_helper`, `macos/universal/libwx_key.dylib` | macOS image-key scan helper + library. |
| `macos/source/image_scan_helper.c`, `macos/source/wcdb_native_capture.c`, `macos/source/image_scan_entitlements.plist` | Source of the macOS helpers (GPL/notice in `macos/WEFLOW_LICENSE.txt`). |
| `__init__.py` | Empty marker. |

> `wechatdb_client.dll`, `wechatdb_broker.exe`, `wechatdb_native_build.json`, `macos/db-key/` are **deliberately absent from git** — see `.gitignore:87-92` and §4.

#### 2.3.6 `resources/` — 6 JSON manifests

| File | Contents |
|---|---|
| `contact_region_lookup.json` (314 KB) | Phone-prefix → region lookup table. |
| `local_search_models.json` | Embedding models: `bge-small-zh` (512-dim ONNX, ~95 MB, recommended) and `bge-base-zh` (768-dim, ~407 MB), each pinned by repo + revision + per-file SHA-256. |
| `local_search_gpu.json` | Pinned CUDA 12.8 / ORT 1.26.0 / cuDNN 9.8.0.87 wheel set with URLs + SHA-256 and validated device notes. |
| `qwen_gpu_runtime.json` (41 KB) | Qwen GPU runtime component manifest. |
| `voice_models.json` | Pinned ASR models: `zipformer-small-ctc-int8` (CPU), `qwen3-asr-06b-onnx-int4`, `qwen3-asr-06b-hf` (CUDA) with file sizes/hashes. |
| `macos_db_key_contract.json` | macOS key-helper bundle contract (see §4). |

---

## 3. Decryption and data-access approach

Two independent read paths, plus a separate image/media key path.

### 3.1 How the WeChat 4.x database key is obtained

`get_db_key_workflow()` in `key_service.py:671-767` is the dispatcher. Platform/mode matrix:

| Mode / platform | Implementation | Evidence |
|---|---|---|
| Windows `auto` (default) | Try **V4 memory scan**, then fall back to **wx_key hook**. | `key_service.py:740-767` |
| Windows `v4` / `key_v4` / `memory` | `_get_db_key_with_v4` only. | `key_service.py:724-729` |
| Windows `hook` | `WeChatKeyFetcher.fetch_db_key` only. | `key_service.py:731-735` |
| macOS (any of `auto` / `macos_private_helper` / `mac_private_helper`) | `capture_macos_database_key()` helper; rejected if it fails `validate_realtime_database_key` for both realtime roles. | `key_service.py:680-719` |
| Non-Windows/macOS | Error: "当前平台不支持自动获取数据库密钥". | `key_service.py:720-721` |

**Path A — V4 memory scan (`key_v4.py`, the default on Windows).**

1. `_resolve_v4_probe_db_file` picks an encrypted `.db` from the account's `db_storage` (priority `msg0.db`, `msg.db`, `micromsg.db`, `favorite.db`, `mediamsg0.db`, `media_msg0.db`, `sns.db`, …, else first `.db` ≥ 4096 bytes). (`key_service.py:53-61, 371-420`)
2. `_load_internal_db_key_candidates` runs `dll_key_scan.extract_xor_keys_from_dll()` over `Weixin.dll` to get 32-byte "internal DB keys", unless the caller supplied one. (`key_service.py:329-348, 438-453`)
3. `dll_key_scan.py` parses the PE, iterates **code sections only** (`IMAGE_SCN_CNT_CODE = 0x20000000`), 2 MiB chunks + 100-byte overlap, and matches the regex `48 BA <8B> .{3,8}? 48 BA <8B> … 48 85 C0` (four `mov rdx, imm64` loading a 32-byte key, i.e. 4×8 bytes). Parallelised with `ProcessPoolExecutor`. (`dll_key_scan.py:17-33, 73-138`)
4. `key_v4.recover_key(pid, db_file)` — `open_process` the WeChat PID, enumerate `MEM_COMMIT|MEM_PRIVATE|PAGE_READWRITE` regions, YARA rule `GetKeyAddrStub` (a `??×6 00×10 20 00…2f 00…` stub) to find pointer slots, read a 32-byte candidate at each pointer, entropy-filter candidates (`is_potential_key`: ≥15 distinct bytes, ≤24 printable), then verify each candidate in a multiprocessing pool. (`key_v4.py:218-340`)
5. Verification is real SQLCipher page-1 authentication: `salt = buf[:16]`, `mac_salt = salt XOR 0x3a`, `new_key = PBKDF2-HMAC-SHA512(passphrase XOR internal_db_key, salt, 256000, dkLen=32)`, `mac_key = PBKDF2-HMAC-SHA512(new_key, mac_salt, 2, dkLen=32)`, then compare HMAC-SHA512 over page 1 (`salt..PAGE_SIZE-reserve+IV`) + little-endian page number. (`key_v4.py:46-54, 175-205`)
6. The internal key is unmasked as `db_key = recovered_raw_key XOR internal_db_key` (`_xor_hex_key_with_internal_db_key`, `key_service.py:351-358`).

**Path B — `wx_key` hook (`WeChatKeyFetcher`, `key_service.py:547-668`).** Kills/relaunches WeChat, calls `wx_key.initialize_hook(pid)`, polls `wx_key.poll_key_data()` for up to 60 s, then `wx_key.cleanup_hook()`. `wx_key>=2.0.1` is a Windows-only dependency shipped as local wheels in `tools/key_wheels/wx_key-2.0.1-cp3xx-cp3xx-win_amd64.whl` with `find-links = ["./tools/key_wheels/"]` (`pyproject.toml:23, 97`).

**Path C — manual key.** All decrypt endpoints accept a 64-hex passphrase directly (`DecryptRequest.key`, `routers/decrypt.py:375-379`). `wechat_decrypt.validate_realtime_database_key` authenticates page 1 of the relevant DB without mutating it (`wechat_decrypt.py:282-291`).

**Key persistence gate.** The backend only writes a key to the store if per-DB diagnostics show `key_mode ∈ {raw_enc_key, sqlcipher_passphrase}` **and** page-1 authentication passed for the required realtime roles: `session.db`/`sessiondata.db` **and** `message*.db`/`msg*.db` (or a combined `MicroMsg.db`, or ≥2 verified encrypted DBs for unknown layouts). (`routers/decrypt.py:199-270`; rationale documented in the comment at `:250-254`)

**Image keys are completely separate**: `image_key_resolver.py` + `image_key_memory_scan.py` + kvcomm scanning (`key_service.py:818-939`), plus an optional remote API `POST https://view.free.c3o.re/api/key` that uploads `all_users/config/global_config` and `.crc` and returns `xorKey`/`aesKey` (`key_service.py:1403-1492`).

### 3.2 SQLCipher/WCDB parameters used

| Parameter | Value | Evidence |
|---|---|---|
| Page size | 4096 | `wechat_decrypt.py:34` |
| Cipher | AES-256-CBC | `_decrypt_page`, `wechat_decrypt.py:393-410` |
| Key derivation | PBKDF2-HMAC-SHA512, 256 000 iterations, dkLen 32 | `_derive_sqlcipher_enc_key`, `wechat_decrypt.py:50-52`; `key_v4.py:35` (`ROUND_COUNT = 256000`) |
| MAC key | PBKDF2-HMAC-SHA512(enc_key, salt XOR 0x3A, 2 rounds, dkLen 32) | `_derive_mac_key`, `wechat_decrypt.py:44-47` |
| Page HMAC | HMAC-SHA512 over `page[16 if page1 else 0 : 4096-80+16]` + `<I` page number | `_compute_page_hmac`, `wechat_decrypt.py:55-62` |
| Reserve | IV 16 + HMAC 64 = 80 bytes at page tail | `wechat_decrypt.py:36-41` |
| Key length | 32 bytes / 64 hex chars | `wechat_decrypt.py:35, 685-691` |

`_decrypt_page` reads IV from `page[4096-80 : 4096-80+16]`, decrypts `page[16:4096-80]` (page 1) or `page[0:4096-80]`, restores the `SQLite format 3\0` header on page 1, and zero-fills the reserve tail so the output is a normal SQLite page. (`wechat_decrypt.py:393-410`)

### 3.3 Offline decryption output

`decrypt_wechat_databases(db_storage_path, key)` (`wechat_decrypt.py:1168`):

- Requires an explicit `db_storage_path` (no multi-account auto-detect, `:1243-1254`); `_resolve_db_storage_roots` requires exactly one `db_storage` dir else errors (`:501-540, 582-595`).
- Walks `db_storage` for `.db` files, skipping names rejected by `database_filters.should_skip_source_database` (`:600-613`).
- Writes plaintext SQLite into `get_output_databases_dir()` = `<output>/databases/<account>/…` (`:1215`), via a temp file then atomic replace (`:741-745`).
- Merges an existing WAL snapshot before decrypting (`sqlite_wal.merge_wal_snapshot`, `:977, 1014`) and runs `sqlite_diagnostics` on the output copy, never the source.
- Per-DB result includes `key_mode`, `key_authenticated`, `failed_pages`, `hmac_warning_pages`, `wal`, `index_repair`, diagnostics (`:706-737`).

There is **no `pysqlcipher`/`sqlcipher3` dependency** in the project (absent from `pyproject.toml` and `uv.lock`); encryption is handled in pure Python (`cryptography`/`pycryptodome`), and normal `sqlite3` reads the decrypted output.

### 3.4 Realtime (no-decrypt) read path — the native core

Realtime reads go through `wechatdb_client.dll` + `wechatdb_broker.exe`, **not** through file decryption.

- `native_core_client.py` loads the client via `ctypes.CDLL` (`__cdecl` on Windows, `:1691-1696`), validates ABI version 1 / protocol 2 (`:26-27, 1798-1801`), and exposes `open_database(path, key=…, key_mode=NativeCoreDatabaseKeyMode.AUTO)`.
- `NativeCoreDatabaseAccess` has exactly one member: `READ_ONLY = 0` (`native_core_client.py:164-166`).
- `native_core_database.open_native_core_database_source()` resolves `db_storage/<group>/<name>`, enforces the path stays inside `db_storage` (`:137-146`), loads the 64-hex key from `key_store` (`:149-156`), and zeroes it after open (`:210-211`).
- `native_core_realtime.py` derives per-database **raw** keys by `PBKDF2(passphrase, salt, 2 rounds)` from the stored passphrase + page-1 salt and caches them (DPAPI-backed), passing `key_mode=RAW` when cached and `AUTO` otherwise (`:458-470, 658-679`).
- `wcdb_realtime.py` is the pool/manager: `open_account`, `get_sessions`, `get_messages`, message cursors, contacts, SNS timeline, SNS image decrypt (`wcdb_realtime.py:97-291`). DB name resolution covers `session/session.db`, `session.db`, `Session.db`, `MicroMsg.db` (`:291-306`).

### 3.5 Key store on disk

- Path: `<output>/account_keys.json` (`app_paths.get_account_keys_path`, `app_paths.py:35-36`; `key_store.py:11`).
- Stores per-account `db_key`, image XOR/AES keys with `image_key_source`/`image_key_verified`, aliases, and source `db_storage` paths; atomic writes (`key_store.py:120-176`).
- Derived raw-key cache and purge hooks: `native_core_raw_key_cache.py`, `key_store.py:39`.

### 3.6 Files and directories read

| Path | What is read | Evidence |
|---|---|---|
| `%USERPROFILE%\Documents\xwechat_files\<account>\db_storage\**\*.db` | Encrypted chat/session DBs | `wechat_detection.py:257-303`; `wechat_decrypt.py:543-613` |
| `…\xwechat_files\<account>\db_storage\session\session.db` / `session.db` / `MicroMsg.db` | Realtime session DB | `wcdb_realtime.py:291-306` |
| `…\xwechat_files\<account>\db_storage\message\` | Realtime message DBs | `native_core_realtime.py:909` |
| `…\xwechat_files\all_users\config\global_config` + `global_config.crc` | Account wxid/nickname/avatar; AES-128-CFB with fixed key `b"xwechat_crypt_ke"`, IV = `.crc[12:28]`, payload length from first LE uint32 (fallback `crc[28:32]`), then MMKV varint parse | `wechat_detection.py:154, 202-254` |
| `%APPDATA%\Tencent\xwechat\config\<md5(KEY_LAST_XWEACHAT_FILE_DIR)>.ini` | Data-root override (parent + `xwechat_files`) | `wechat_detection.py:477-563` |
| `…\xwechat_files\<account>\db_storage\…` under all-users / login dirs | Account discovery | `wechat_detection.py:863-991` |
| `%APPDATA%\Tencent\xwechat\net\kvcomm` (Windows) / container kvcomm dirs (macOS) | Image XOR/AES keys | `key_service.py:857-912` |
| `key_info.db` | Current-account inference by latest activity | `wechat_detection.py:1288-1298` |
| `Weixin.dll` / `WeChat.exe` / `Weixin.exe` | PE scan for internal key; version read from exe | `key_service.py:51, 169-186, 209-257` |
| WeChat process memory (`Weixin.exe`/`WeChat.exe`) | V4 key recovery | `key_service.py:471-481`; `key_v4.py:236-341` |
| Output: `<output>/databases`, `<output>/account_keys.json`, `<output>/runtime_settings.json` | Written runtime state | `app_paths.py:24-36` |

---

## 4. Native components: REQUIRED vs optional

### 4.1 Required to start (hard failure)

`configure_native_core_entrypoint()` (called from `main.py:46` and `backend_entry.py:105`) does all of:

- forces `WECHAT_TOOL_NATIVE_CORE_MODE=required` (`native_core_client.py:1655-1656`);
- resolves the entrypoint dir (frozen → `<exe dir>/native`) and **hard-requires three files**: `wechatdb_client.dll` (or `libwechatdb_client.dylib`), `wechatdb_broker.exe` (or `wechatdb_broker`), `wechatdb_native_build.json` (`:1664-1673`, `_require_native_core_entrypoint_file` `:1614-1626`);
- validates the manifest profile and starts/stops the broker once as a handshake (`:1675-1688`).

Missing any of them raises `NativeCoreComponentMissingError` before uvicorn ever binds. `.gitignore:87-91` excludes these three from the source tree; they are produced by the private `2977094657/WCDB` producer and staged into `desktop/build/native-runtime` by `desktop/scripts/build-backend.cjs` (see `NATIVE_CORE_ARTIFACTS`, `build-backend.cjs:39-44`).

**This means: cloning the public repo and running `uv run main.py` cannot start the backend unless you obtain or build the native core** (source mode additionally requires the "source-public" profile — see §6).

### 4.2 Required for specific features

| Component | Required for | Optional behaviour | Evidence |
|---|---|---|---|
| `wechatdb_client.dll` + `wechatdb_broker.exe` + `wechatdb_native_build.json` | Startup, realtime reads, native export/sealing, native ASR | none — fail-fast | `native_core_client.py:1651-1688`; `native_core_database.py:188-217` |
| `wce_integrity.pyd` | **Export sealing / integrity sidecars** (`export_integrity.py`), and packaging preflight | `load_wce_integrity_native()` raises `RuntimeError("导出完整性组件初始化失败。")` when absent; basic decryption/chat read does not import it at startup | `export_integrity.py:17-83`; `desktop/scripts/build-backend.cjs:425-455` |
| `img_helper.dll` | Windows "big-image download" process hook, toggled by the user | `img_helper._load_lib` raises `FileNotFoundError` only when enabled; `/api/system/img_helper/status` reports `supported/enabled` | `img_helper.py:40-93`; `routers/system.py:50-75`; `platform_support.py:235` |
| `VoipEngine.dll` | WxAM → image decoding (`wxam_dec_wxam2pic_5`) | Missing DLL logs a warning and returns `None` (falls back to other decoders) | `media_helpers.py:1255-1278` |
| `libwx_key.dylib` + `macos/universal/image_scan_helper` | macOS image-key memory scan | `runtime_capabilities.image_key_memory_scan` is true only on Windows or when both files exist in the same dir | `platform_support.py:104-115, 174-226`; `platform_support.mac_image_scan_*` |
| `macos/db-key/wda_xkey_helper` (+ manifest/trust/checksums/provenance) | macOS DB-key capture | Required only for macOS `get_db_key_workflow`; `runtime_capabilities.database_key_extraction` reflects availability; manual 64-hex key still works | `platform_support.py:12-15, 83-101, 197-213`; `resources/macos_db_key_contract.json`; `routers/keys.py:633-848` |
| `weflow_wasm/*` (4 files) | SNS image/video decryption keystream | **Packaging hard-requires all four** (`validateRuntimeNativeHelpers`, `build-backend.cjs:396-407`); at runtime a Node/Electron-as-Node executable must also exist or SNS decryption fails | `build-backend.cjs:396-407`; `sns_media.py:260-262, 426-449` |
| Node/Electron runtime | SNS WASM keystream | Resolution order: `WECHAT_TOOL_NODE_EXECUTABLE` (+ `WECHAT_TOOL_NODE_MODE` = `node` or `electron-run-as-node`), else `shutil.which("node")`; Electron path is supplied by the desktop launcher | `sns_media.py:426-449, 291-299` |
| `SDL2.dll`, `msvcp140*.dll`, `vcruntime140*.dll` | Support DLLs for VoipEngine/img_helper | Present next to the natives; loaded via DLL directory search | `src/wechat_decrypt_tool/native/` listing; `img_helper.py:48-56` |
| `isaac64.py` | Pure-Python fallback if the WASM/Node path is unavailable | Built-in, always present | `isaac64.py:1-10` |

### 4.3 What ships in the installed app vs upstream git

| File | Upstream `src/wechat_decrypt_tool/native/` | Installed `resources/backend/native/` (2.9.0) |
|---|---|---|
| `wce_integrity.pyd` | yes (6 978 448 B) | yes |
| `img_helper.dll` | yes | yes |
| `VoipEngine.dll` | yes | yes |
| `SDL2.dll`, `msvcp140*.dll`, `vcruntime140*.dll` | yes | yes |
| `libwx_key.dylib`, `image_scan_helper` (macOS) | yes | yes (unused on Windows) |
| `weflow_wasm/` 4 files | yes | yes |
| `wechatdb_client.dll` | **no (gitignored)** | yes (719 424 B) |
| `wechatdb_broker.exe` | **no (gitignored)** | yes (1 538 112 B) |
| `wechatdb_native_build.json` | **no (gitignored)** | yes (1 601 B) |

Installed-app evidence: local directory listing of `<install-dir>\WeChatDataAnalysis\resources\backend\native\`; upstream evidence: cached tree + `.gitignore:87-92`.

---

## 5. Dependencies (`pyproject.toml` / `uv.lock`)

`pyproject.toml` declares `requires-python = ">=3.11"`, `version = "2.9.0"`, build backend hatchling, wheel packages `src/wechat_decrypt_tool`. Resolved versions below are from `uv.lock` (main revision, 3 559 lines).

### 5.1 Core runtime

| Package | Constraint | Resolved | Why it matters |
|---|---|---|---|
| `fastapi` | `>=0.104.0` | 0.115.13 | Web API framework. |
| `uvicorn[standard]` | `>=0.24.0` | 0.34.3 (`starlette` 0.46.2) | ASGI server; `watchfiles` enables reload. |
| `python-multipart` | `>=0.0.6` | 0.0.20 | Multipart uploads (keys, media, imports). |
| `aiofiles` | `>=23.2.1` | 24.1.0 | Async file serving. |
| `cryptography` | `>=41.0.0` | 45.0.4 | AES-CBC page decryption, AES-CFB `global_config`. |
| `pycryptodome` | `>=3.23.0` | 3.23.0 | PBKDF2/HMAC in `key_v4.py`, CFB fallback. |
| `psutil` | `>=7.0.0` | 7.0.0 | Process discovery/kill/relaunch, LAN interface enumeration. |
| `pywin32` | `>=310` (win32) | 310 | Windows API access (DPAPI etc.). |
| `requests`, `httpx` | `>=2.32.4` / any | 2.32.4 / 0.28.1 | HTTP clients (CDN, remote key API, license service). |
| `loguru` | `>=0.7.0` | 0.7.3 | Logging backend. |
| `zstandard` | `>=0.23.0` | 0.25.0 | zstd payloads (export/media). |
| `pilk`, `pypinyin`, `jieba` | `>=0.2.4`, `>=0.53.0`, `>=0.42.1` | 0.2.4, 0.55.0, 0.42.1 | Voice (silk) decode, pinyin, Chinese tokenization. |
| `packaging` | any | 25.0 | Version comparison in `key_service`. |
| `watchfiles` | `>=1.1.0` | 1.1.0 | File-event autosync + uvicorn reload. Also smoke-tested in packaging. |
| `typing-extensions` | `>=4.8.0` | 4.14.0 | Typing backports. |

### 5.2 Windows-only key/scan stack (conditional deps)

| Package | Constraint | Resolved | Role |
|---|---|---|---|
| `wx_key` | `>=2.0.1; win32` | 2.0.1 | Hook-based DB key capture; local wheels in `tools/key_wheels/`, `find-links` configured in `[tool.uv]`. |
| `pymem` | `>=1.14.0; win32` | 1.14.0 | Open WeChat process / read memory in `key_v4.py`. |
| `yara-python` | `>=4.5.2; win32` | 4.5.4 | `GetKeyAddrStub` memory scan. |
| `pefile` | `>=2024.8.26; win32` | 2024.8.26 | Parse `Weixin.dll` sections for the internal key. |

### 5.3 AI stack

| Package | Constraint | Resolved | Role |
|---|---|---|---|
| `langchain-core` | `>=1.0,<2` | 1.6.2 | Message/tool abstractions. |
| `langchain-openai` | `>=1.0,<2` | 1.6.0 | OpenAI-compatible transport (`reasoning_client.py`). |
| `langchain-anthropic` | `>=1.0,<2` | 1.7.1 | Anthropic provider. |
| `langgraph` | `>=1.0,<2` | 1.2.11 | Graph runtime. |
| `langgraph-checkpoint-sqlite` | `>=3.0,<4` | 3.1.1 | Conversation checkpoints. |
| `deepagents` | `==0.7.13` | 0.7.13 | Pinned agent engine (`agent_service.py` says it is the only production engine). |
| `pypdf`, `pypdfium2`, `python-docx`, `openpyxl`, `python-pptx`, `Pillow` | see pyproject | 6.17.0, 5.13.0, 1.2.0, 3.1.5, 1.0.2, 12.3.0 | Document/image ingestion for AI (`ai/media.py`, `xlsx_export.py`). |
| `httpx` | any | 0.28.1 | Provider HTTP. |

### 5.4 Local semantic search

| Package | Constraint | Resolved | Role |
|---|---|---|---|
| `onnxruntime` | `==1.28.0` (non-darwin) / `==1.23.2` (darwin) | 1.28.0 / 1.23.2 | Embedding inference; macOS pinned for universal wheels. |
| `tokenizers` | `==0.23.1` | 0.23.1 | BGE tokenizer. |
| `sqlite-vec` | `==0.1.9` | 0.1.9 | Vector storage in SQLite. |
| `huggingface-hub` | `==1.27.0` | 1.27.0 | Model download. |
| `numpy` | (transitive) | 2.4.6 / 2.5.2 (two marker variants) | Array math. |

### 5.5 Optional extras

| Extra | Packages (constraints) | Resolved | Role |
|---|---|---|---|
| `build` | `pyinstaller>=6.0.0` | 6.18.0 | Onefile backend packaging. |
| `voice-transcription` | `faster-whisper>=1.1.0`, `opencc-python-reimplemented>=0.1.7`, `sherpa-onnx==1.13.8`, `sherpa-onnx-core==1.13.8; win32` | 1.2.1, 0.1.7, 1.13.8 | CPU ASR; OpenCC t2s conversion (smoke-tested in packaging). |
| `voice-transcription-gpu` | `torch==2.9.1`, `transformers==5.17.0` | 2.9.1 (+cu128) / 5.17.0 | Qwen GPU ASR; CPU installs do **not** pull PyTorch (comment in pyproject:60). |
| dev group | `pytest>=8.0.0` | — | Tests under `tests/`. |

Index/source config: `tool.uv.index` `pytorch-cu128` = `https://download.pytorch.org/whl/cu128` (explicit), `tool.uv.sources` maps `torch` to it on win32 (`pyproject.toml:99-105`).

---

## 6. Platform constraints and the read-only vs write-build distinction

### 6.1 Platform matrix

| Capability | Windows | macOS (Apple Silicon) | macOS (Intel) | Linux |
|---|---|---|---|---|
| Backend runs | yes | yes | yes | importable; `main.py` works |
| Auto DB-key extraction | yes (V4 memory scan / hook) | yes, via signed `wda_xkey_helper` + online authorization | UNVERIFIED (helper is universal; `runtime_capabilities` gates on helper presence) | no ("当前平台不支持自动获取数据库密钥") |
| Realtime WCDB | yes | native core requires arm64 (`apple_silicon`) | no (`realtime_wcdb` false) | no (`wechatdb native core supports Windows and macOS only`, `native_core_client.py:1537`) |
| Image-key memory scan | yes | yes with `libwx_key.dylib` + `image_scan_helper` | helper present but `image_key_memory_scan` true only if files exist | no |
| Process media hook (`img_helper.dll`) | yes | no (`wechat_process_media_hook` is `system == "windows"`) | no | no |
| Offline decryption of user-supplied DB copies | yes | yes | yes | `database_decryption: true` (platform-agnostic) |
| Manual 64-hex key input | yes | yes | yes | yes (`database_key_manual_input: true`) |

Source: `platform_support.runtime_capabilities()` (`platform_support.py:170-252`); macOS rejection for other key modes in `key_service.py:680-683`; Windows/macOS-only native core (`native_core_client.py:1532-1537, 1589-1596`).

### 6.2 What `readOnlyBuild: true` / `distributionMode: public` mean

The installed manifest (`<install-dir>\WeChatDataAnalysis\resources\backend\native\wechatdb_native_build.json`) is a **schemaVersion 2 source-public Windows native core**:

| Field | Value | Meaning |
|---|---|---|
| `schemaVersion` | 2 | Windows profile; schema 3 is macOS (`native_core_client.py:954, 968-987`) |
| `distributionMode` | `public` | Public distribution (`_NATIVE_CORE_DISTRIBUTION_MODES = {"public","controlled"}`, `native_core_client.py:47`) |
| `readOnlyBuild` | `true` | No write access to WeChat databases |
| `databaseWriteBuild` | `false` | Same, explicit |
| `wechatActions` | `[]` | No WeChat-side actions/automation allowed |
| `sourceRuntime` | `true` | "source-public" artifact (restricted build), not the full production profile |
| `windowsHostVerification` | `same-user-direct-parent` | Broker only accepts a same-user direct parent |
| `offlineBootstrapFeatureBits` | 3 | `DATABASE_READ | EXPORT` (`native_core_client.py:106-110, 145-147`) |
| `offlineExportSealFormat` | `WES2` | Export envelope format |
| `nativeAsrFeatureBit` / `Authorization` | 16 / `database-read` | Native ASR granted under DB-read authorization |
| `codeSignatureEnforced` / `rootPublicKeyCompiled` | `true`/`true` | Signature and root-pin checks active |
| `testHooksEnabled` / `stagingPinnedSignerTrust` | `false`/`false` | Production-grade flags |
| `buildId` / `buildIssuedAtUnix` / `buildExpiresAtUnix` | `wcda-38116174842-1-windows-native` / 1791697943 / 1795585943 | **Fixed 45-day lifetime**, issued 2026-10-11T05:52:23Z, expires 2026-11-25T05:52:23Z (`_NATIVE_CORE_BUILD_LIFETIME_SECONDS`, `native_core_client.py:60`; check at `:1436-1437`) |
| `windowsClientSignerSha256`, `windowsBrokerSignerSha256`, `windowsPrivateRootSha256` | three SHA-256 pins | Authenticode private-PKI trust domain (`windowsSignerTrustMode: private-pki`) |
| `securityNoticeId` / checkpoint set | `WCE-AUTOMATED-ANALYSIS-NOTICE-V2` / `WCE-AI-CHECKPOINT-SET-V3`, 7 checkpoints | Automated-analysis notice contract |

Enforcement in Python:

- Schema-2 manifests **must** have `readOnlyBuild=true` and an empty `wechatActions` list, else `NativeCoreProtocolError` (`native_core_client.py:980-987`).
- Runtime only exposes `NativeCoreDatabaseAccess.READ_ONLY` (`native_core_client.py:164-166`); `open_database` defaults to it (`:2674`).
- The frozen app accepts either a full production `sourceRuntime=false` manifest or a **source-public** manifest; a source-public one is accepted only if `_is_source_public_native_core_build_manifest()` holds: `source_runtime == true` + full production base checks + `windowsHostVerification == "same-user-direct-parent"` (`native_core_client.py:1363-1387, 1484-1496`).
- Source (non-frozen) runs are **stricter**: a full production manifest is rejected ("Source WeChatDataAnalysis on Windows requires the exact restricted source-public native core", `:1388-1392`), and a source-public one is allowed only when not frozen or remote calls are enabled (`:1377-1383`). Development artifacts require both `developmentBuild: true`, `buildId == "dev-local"` and `WECHAT_TOOL_NATIVE_CORE_ALLOW_DEVELOPMENT_BUILD=1` (`:1452-1463, 1407-1419`).
- Legacy WCDB env overrides (`WECHAT_TOOL_WCDB_API_DLL_PATH`, sidecar URLs, …) are deleted at startup (`:68-77, 1646-1648`); external `WECHAT_TOOL_NATIVE_CORE_LIBRARY`/`_BROKER` overrides are refused after migration (`:1637-1643`).
- Builds expire: `native_core_lease.validate_native_core_authorization_policy` rejects production/source-public manifests past `buildExpiresAtUnix` (`native_core_lease.py:409-418`), and leases cannot exceed it (`:1038-1040, 1126-1128`).

### 6.3 Packaging constraints (how a replicator must build)

From `desktop/scripts/build-backend.cjs` and `.github/workflows/release.yml`:

- Packaging on win32/darwin **requires** an explicit `WCE_NATIVE_CORE_ARTIFACT_DIR` containing exactly the "reviewed runtime trio" (`wechatdb_client.dll`, `wechatdb_broker.exe`, `wechatdb_native_build.json`); release CI additionally checks `SHA256SUMS.txt`, `provenance.json`, signer pins, `readOnlyBuild`, and no WeChat actions (`build-backend.cjs:39-44, 238-317`; `release.yml` native validation block).
- `wce_integrity` must be resolvable or packaging fails (`runIntegrityPreflight`, `build-backend.cjs:425-455`): it runs `from wechat_decrypt_tool.export_integrity import load_wce_integrity_native` and asserts non-empty CSS for `chat`, `sns`, `records-project`, `records-generic`, `contacts`.
- `validateRuntimeNativeHelpers` hard-requires the four `weflow_wasm/*` files; on macOS additionally `macos/universal/image_scan_helper` and `macos/db-key/wda_xkey_helper` (`build-backend.cjs:396-423`).
- macOS distribution builds require a pinned `libwce_integrity.dylib` artifact with `_PyInit_wce_integrity` symbol (`integrity-native-packaging.cjs:91, 104-121, 176-187`).
- The macOS key helper contract is fixed in `resources/macos_db_key_contract.json`: bundle path `macos/db-key`, helper `wda_xkey_helper`, manifest `wda_xkey_build.json`, trust `wda_xkey_trust.json`, checksums `SHA256SUMS.txt`, provenance `provenance.json`, bundle id `com.lifearchive.wechatdataanalysis.xkey-helper`, host signer `…backend`, required archs arm64 + x86_64, timeouts 1 000–180 000 ms.
- Backend build-time lifetime is 45 days and per-run (`docs/release-native-build.md:3-16`); release workflows are documented in `.github/workflows/release.yml`, `windows-source-runtime-promotion.yml`, `macos-source-runtime-consumer.yml`.

**Practical consequence for replication:** the decryption logic is fully reproducible from public source, but a working *runtime* requires native artifacts that are not in the public repo. For source mode you must obtain a signed "source-public"/"dev-local" native core; for a release build you must produce it through the private `2977094657/WCDB` producer with the documented secrets.

---

## 7. Verification notes and UNVERIFIED items

Verified directly from upstream `main` raw files (all paths above). Explicitly flagged:

- **UNVERIFIED — tag equivalence for Python sources.** The Lead verified `desktop/src/*.cjs` byte-equality between the installed 2.9.0 app and upstream. This report fetched upstream `main`; it assumes `main` == tag `v2.9.0` for `src/wechat_decrypt_tool/**`, which was not independently re-diffed here. `pyproject.toml` and `src/wechat_decrypt_tool/__init__.py` both say `2.9.0`, consistent with the tag.
- **UNVERIFIED — upstream tree membership of the installed native trio.** `wechatdb_client.dll`, `wechatdb_broker.exe`, `wechatdb_native_build.json` are gitignored; their presence/absence in `main` was confirmed only via `.gitignore:87-92` and the cached tree (which lists only `wce_integrity.pyd`, `img_helper.dll`, `VoipEngine.dll`, the runtime DLLs, `macos/*`, `weflow_wasm/*`). Actual contents/hashes of the installed trio were not decompiled or signature-verified.
- **UNVERIFIED — macOS end-to-end behaviour.** macOS capture/validation was read only; no macOS host was used to run `macos_db_key_capture.py`/`macos_native_capture.py` or to confirm `wda_xkey_helper` availability.
- **UNVERIFIED — Intel-Mac realtime/image capabilities.** `runtime_capabilities` gates realtime on `apple_silicon`; whether Intel macOS can use `image_scan_helper` in practice was not executed.
- **UNVERIFIED — exact endpoint semantics for every router.** Router purposes/endpoints were extracted statically (`APIRouter` prefixes, `@router.*` decorators, module docstrings); request/response bodies were only inspected for `decrypt.py`, `keys.py`, `mcp.py`, `system.py`, `health.py`.
- **INFERRED — one-line purposes** for modules whose module docstring was empty (e.g. `account_identity.py`, `account_source_policy.py`, `chat_accounts.py`, `database_filters.py`, `desktop_parent_watchdog.py`, `source_fallback.py`, `native_core_lease.py`, `native_core_raw_key_cache.py`, `native_core_realtime.py`, `macos_db_key_helper.py`, and some `ai/*` grouping entries). These are marked *inferred* in §2.
- **Not executed:** no Python imports of the backend, no PyInstaller build, no `pip install`, no runtime API calls against the installed `wechat-backend.exe`. All conclusions are source-read + installed-artifact inspection.

### Minimal replication checklist

1. Python ≥ 3.11, `uv sync` with the extra you need (`--extra voice-transcription`, `--extra build`).
2. Obtain/produce the native trio + `wce_integrity.pyd` and place under `src/wechat_decrypt_tool/native/` (plus `weflow_wasm/`, `img_helper.dll`, `VoipEngine.dll`, runtime DLLs which are in git).
3. `uv run main.py` → FastAPI on `127.0.0.1:10392` (`WECHAT_TOOL_PORT` to change).
4. `POST /api/decrypt {key, db_storage_path}` or `GET /api/get_keys` → `output/databases/<account>/`.
5. Optional: build the Nuxt UI into `frontend/.output/public` for the SPA mount; package with `desktop/scripts/build-backend.cjs` + PyInstaller.
