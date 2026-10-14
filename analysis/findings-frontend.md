# WeChatDataAnalysis — Frontend + IPC + HTTP API contract

Scope: how the installed Electron/Nuxt app is wired end-to-end, for someone who wants to
replicate it. Upstream: **LifeArchiveProject/WeChatDataAnalysis**, branch `main`, tag **v2.9.0**.
All upstream URLs below are `https://raw.githubusercontent.com/LifeArchiveProject/WeChatDataAnalysis/v2.9.0/<path>`.

Two independent sources are used and they cross-check each other:

| Source | Path | What it proves |
|---|---|---|
| Extracted real Electron source of the **installed** app | `<repo>\app-asar\src\` (`main.cjs` 3577 lines, `preload.cjs`, `renderer-startup.cjs`, …) | IPC channels, UI URL resolution, backend spawn |
| Packaged **Nuxt generate** output | `<install-dir>\WeChatDataAnalysis\resources\ui\` (`index.html`, `_nuxt/`, 15 route dirs) | Which routes actually shipped |
| Upstream frontend/backend source at tag v2.9.0 (fetched via raw.githubusercontent.com) | `frontend/**`, `src/wechat_decrypt_tool/**` | Page features, API base + auth, endpoint paths |

Legend: **[verified]** = read directly from a cited file/line; **[UNVERIFIED]** = inferred, not directly confirmed.

> **Lead addendum (provenance closed):** the local extracted `app-asar/src` sources were
> subsequently byte-diffed against upstream by the Lead. All **16/16** code files are identical
> to the public repo **after CRLF/BOM normalisation** (and identical to both `main` and `v2.9.0`,
> which share these files); the two binary icons match byte-for-byte with no normalisation.
> Reproduce with `<repo>\verify_provenance.py`. This closes UNVERIFIED item #5.

---

## 1. How Electron loads the UI

### 1.1 The URL is always `http://`, never `file://`

**[verified]** There is **no `file://` and no `BrowserWindow.loadFile()` anywhere** in
`app-asar/src/main.cjs` — a content grep for `loadFile|file://` over the whole file returns zero
matches. The renderer is always loaded from a URL string.

Chain of resolution:

1. `app-asar/src/main.cjs:3434-3470` `ensureMainWindowReady()`:
   waits for the backend (`ensureBackendReadyWithFallback()`), creates the window
   (`createMainWindow()`), then `const startUrl = getDesktopUiUrl()` (main.cjs:3447) and
   `await loadWithRetry(win, startUrl)` (main.cjs:3449).
2. `app-asar/src/main.cjs:292-299` `getDesktopUiUrl()` calls `resolveDesktopUiUrl({...})`.
3. `app-asar/src/renderer-startup.cjs:61-66` `resolveDesktopUiUrl()`:

```js
if (staticUi) return backendUrl;
const explicit = String(startUrl || '').trim();
return explicit || (isPackaged ? backendUrl : 'http://localhost:3000');
```

with (main.cjs:293-297):

- `startUrl` = `process.env.ELECTRON_START_URL`
- `backendUrl` = `getBackendUiUrl()` = `` `http://${host}:${port}/` `` (main.cjs:286-290)
- `isPackaged` = `app.isPackaged`
- `staticUi` = `process.env.WECHAT_TOOL_STATIC_UI === "1"`

Resulting URLs:

| Mode | URL loaded into the BrowserWindow |
|---|---|
| Packaged desktop (`app.isPackaged === true`) | **`http://127.0.0.1:10392/`** (or `0.0.0.0` host + user port) |
| Dev (`ELECTRON_START_URL` unset, not packaged) | `http://localhost:3000` (Nuxt dev server) |
| Dev with `WECHAT_TOOL_STATIC_UI=1`, or explicit `ELECTRON_START_URL` | that URL / the backend URL |

Host/port defaults: `DEFAULT_BACKEND_HOST = "127.0.0.1"` (main.cjs:63),
`DEFAULT_BACKEND_PORT = parsePort(process.env.WECHAT_TOOL_PORT) ?? 10392` (main.cjs:65);
LAN mode switches the bind host to `0.0.0.0` (main.cjs:64, 261-271).

### 1.2 Who serves that URL

**[verified]** The **Python backend itself serves the packaged Nuxt UI at `/`**; Electron only
points the window at it.

- Electron passes the generated UI directory to the backend process:
  `app-asar/src/main.cjs:2145-2147`:
  `if (app.isPackaged && !env.WECHAT_TOOL_UI_DIR) env.WECHAT_TOOL_UI_DIR = path.join(process.resourcesPath, "ui");`
  → `<install-dir>\WeChatDataAnalysis\resources\ui`.
- The backend mounts it: `src/wechat_decrypt_tool/api.py`
  - `_SPAStaticFiles(StaticFiles)` at line 175 — SPA fallback: a 404 for a path whose basename has
    no `.` returns `200.html`, else `index.html` (api.py:214-233); `_nuxt/*` gets
    `Cache-Control: public, max-age=31536000, immutable`, shell files get `no-store` (api.py:200-212).
  - `_maybe_mount_frontend()` at line 236 → `app.mount("/", _SPAStaticFiles(directory=str(ui_dir), html=True), name="ui")`; UI dir candidates are `WECHAT_TOOL_UI_DIR` then `repo_root/frontend/.output/public` (api.py:243-251).
  - Docstring at api.py:237-241: *"This keeps web + desktop UI identical when the desktop shell (Electron) loads http://127.0.0.1:<port>/ from the same backend that serves `/api/*`."*
- Static-UI mode in dev: `getPackagedUiDir()` (main.cjs:851-861) returns
  `WECHAT_TOOL_UI_DIR` or `frontend/.output/public` when
  `!app.isPackaged && WECHAT_TOOL_STATIC_UI === "1"`; used by
  `refreshRendererCacheForPackagedUi()` (main.cjs:1294-1320), which clears the Chromium cache and
  service workers whenever the shell's `buildId` changes (`readPackagedUiBuildId()`, main.cjs:863-880).

### 1.3 `loadWithRedirect` — why it exists

**[verified]** `app-asar/src/renderer-startup.cjs:16-59`. The Nuxt app has a **first-use agreement
page that aborts the initial navigation** (`window.location.replace('/agreement?...')` — visible in
the packaged `resources/ui/index.html` inline `data-hid="first-use-bootstrap"` script). That makes
`win.loadURL()` reject with `ERR_ABORTED` / `errno -3`. `loadWithRedirect` therefore:

- ignores `ERR_ABORTED` while waiting for `did-finish-load`;
- accepts the load only if the finished URL is a **same-origin / same-directory internal redirect**
  (`isInternalRedirect`, renderer-startup.cjs:4-13);
- otherwise rethrows, and has a 60 s navigation deadline (error code `ERR_NAVIGATION_TIMEOUT`).

`loadWithRetry()` (main.cjs:2557-2577) retries the whole thing every 500 ms for up to 60 s, which
covers the backend still booting.

### 1.4 Window wiring

**[verified]** `createMainWindow()` main.cjs:2507-2555: 1200×800, min 980×700, hidden title bar with
overlay (`titleBarOverlay`), `backgroundColor: "#EDEDED"`, and
`webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, devTools: true }`
(main.cjs:2517-2524). Renderer lifecycle/console logging is attached at main.cjs:2440-2505
(`logRendererLifecycle`) and main.cjs:2551-2552.

Packaged UI actually shipped on disk (`<install-dir>\WeChatDataAnalysis\resources\ui\`): `index.html`,
`200.html`, `404.html`, `_payload.json`, `_nuxt/`, plus route directories **agreement, biz, chat,
contacts, decrypt, decrypt-result, detection-result, favorites, finder, import, mini-programs,
payments, revokes, sns, wrapped** and public dirs `assets, fonts, wxemoji`.
Note: there is **no `/settings` route directory** — settings is a modal component
(`frontend/components/SettingsDialog.vue`), not a page. **[verified by directory listing]**

---

## 2. IPC surface

All channels are registered in **one function**, `registerWindowIpc()` in
`app-asar/src/main.cjs:2806-3377` (called from `main()` at main.cjs:3488). The renderer side is the
`contextBridge.exposeInMainWorld("wechatDesktop", {...})` object in `app-asar/src/preload.cjs:63-180`.
Every `ipcRenderer.invoke` channel has exactly one `ipcMain.handle` counterpart; there are
**36 `ipcMain.handle` + 1 `ipcMain.on` = 37 channels**, plus 6 main→renderer push events.

`preload.cjs` also exposes `__brand: "WeChatDataAnalysisDesktop"` (preload.cjs:65) — the frontend
uses this marker to detect the desktop shell (e.g. `useApiBase()` and `useDesktopUpdate`).

### 2.1 Renderer → main (invoke / handle)

| Channel | main.cjs line | preload exposure (preload.cjs) | Purpose |
|---|---|---|---|
| `window:minimize` | 2817 | `minimize()` :75 | Minimize the window |
| `window:toggleMaximize` | 2822 | `toggleMaximize()` :76 | Maximize / restore |
| `window:close` | 2829 | `close()` :77 | Close (may minimize to tray per close behavior) |
| `window:isMaximized` | 2834 | `isMaximized()` :78 | Query maximize state |
| `window:setTitleBarTheme` | 2839 | `setTitleBarTheme(theme)` :79 | Recolor the native title-bar overlay (light/dark) |
| `app:getAutoLaunch` | 2844 | `getAutoLaunch()` :83 | Read OS login-item setting |
| `app:setAutoLaunch` | 2854 | `setAutoLaunch(enabled)` :84 | Set OS login-item setting |
| `app:getCloseBehavior` | 2871 | `getCloseBehavior()` :86 | `"tray"` vs `"exit"` |
| `app:setCloseBehavior` | 2898 | `setCloseBehavior(b)` :87 | Persist close behavior + create/remove tray |
| `app:isDebugEnabled` | 2880 | `isDebugEnabled()` :80 | Whether debug tooling is on |
| `app:getVersion` | 3045 | `getVersion()` :155 | `app.getVersion()` |
| `backend:getPort` | 2909 | `getBackendPort()` :89 | Effective FastAPI port |
| `backend:setPort` | 2918 | `setBackendPort(port)` :90 | Validate port, persist, restart backend, reload UI; **rejects in dev** (main.cjs:2923-2925) |
| `backend:getMcpLanAccess` | 2969 | `getMcpLanAccess()` :91 | LAN/MCP binding state + access URLs |
| `backend:setMcpLanAccess` | 2993 | `setMcpLanAccess(enabled)` :92 | Toggle `0.0.0.0` bind (`WECHAT_TOOL_HOST`), restart backend |
| `app:getOutputDirInfo` | 3054 | `getOutputDirInfo()` :98 | Output dir path/default/pending/error info |
| `app:getOutputDir` | 3072 | `getOutputDir()` :99 | Current output dir path |
| `app:getOutputDirChangeProgress` | 3076 | `getOutputDirChangeProgress()` :100 | Progress snapshot of an in-flight dir migration |
| `app:setOutputDir` | 3260 | `setOutputDir(dir)` :101 | Transactional output-dir migration (may move data) |
| `app:openOutputDir` | 3080 | `openOutputDir()` :102 | `shell.openPath(outputDir)` |
| `app:captureRegion` | 3106 | `captureRegion(opts)` :105 | Screenshot a screen rect to PNG via CDP, save to `output/年度总结/`, reveal in Explorer |
| `app:wrappedBatchBegin` | 3143 | `wrappedBatchBegin` :118 | Create temp dir for a Wrapped batch export |
| `app:wrappedBatchCapture` | 3163 | `wrappedBatchCapture` :122 | Capture one Wrapped page PNG into the batch dir |
| `app:wrappedBatchFinish` | 3185 | `wrappedBatchFinish` :135 | Zip the batch into `output/年度总结/*.zip`, reveal, dispose |
| `app:wrappedBatchAbort` | 3230 | `wrappedBatchAbort` :140 | Drop the batch temp dir |
| `app:openExternalUrl` | 3238 | `openExternalUrl(url)` :145 | Open `http/https/weixin` URL in the OS browser (protocol allow-list) |
| `app:getAccountInfo` | 3283 | `getAccountInfo(account)` :146 | Read on-disk account metadata |
| `app:deleteAccountData` | 3291 | `deleteAccountData(account)` :147 | Delete one account's data from disk |
| `app:checkForUpdates` | 3305 | `checkForUpdates()` :156 | Trigger electron-updater check |
| `app:downloadAndInstall` | 3309 | `downloadAndInstall()` :157 | Download update and install on completion |
| `app:installUpdate` | 3313 | `installUpdate()` :158 | `autoUpdater.quitAndInstall()`, guarded by PKI update verification |
| `app:ignoreUpdate` | 3337 | `ignoreUpdate(version)` :159 | Persist "skip this version" |
| `dialog:chooseDirectory` | 3342 | `chooseDirectory(opts)` :94 | Native directory picker |
| `dialog:chooseArchive` | 3361 | `chooseArchive(opts)` :95 | Native `.zip` account-archive picker |
| `ai:takeNavigation` | 2811 | `takeAiNavigation()` :67 | Pop a pending AI-notification navigation target |
| `ai:diagnosticFallback` | 2807 | `aiDiagnosticFallback(entries)` :68 | Write renderer-side AI diagnostic fallback log |
| **`debug:log`** (`ipcMain.on`) | 2889 | `logDebug(scope,msg,details)` :81 (via `ipcRenderer.send`, preload.cjs:5) | Fire-and-forget renderer log line into a main log file |

### 2.2 Main → renderer (push, subscribed in preload)

| Channel | Emitted at main.cjs | preload subscription | Purpose |
|---|---|---|---|
| `app:outputDirChangeProgress` | 1432 | `onOutputDirChangeProgress` :148-152 | Migration progress updates |
| `app:downloadProgress` | 1640 | `onDownloadProgress` :160-164 | Update download progress |
| `app:updateAvailable` | 1754 | `onUpdateAvailable` :165-169 | New version available |
| `app:updateDownloaded` | 1657 | `onUpdateDownloaded` :170-174 | Update ready to install |
| `app:updateError` | 1675 | `onUpdateError` :175-179 | Update failure message |
| `ai:navigate` | 3512 | `onAiNavigate` :69-73 | AI notification wants to navigate the UI |

### 2.3 Important negative finding: there are **no** ASR / PKI / native-core IPC channels

**[verified]** A repo-wide content search for `ipcMain.(handle|on)` and
`ipcRenderer.(invoke|send|on)` across **all** `app-asar/src/*.cjs` files (main.cjs, preload.cjs,
output-dir.cjs, native-core-runtime.cjs, windows-private-pki-runtime.cjs, macos-private-pki-runtime.cjs,
source-native-core-bootstrap.cjs, …) matches **only `main.cjs` and `preload.cjs`**.
Consequently there is no `asr:*`, `pki:*`, or `native:*` IPC namespace. Those concerns are handled
out-of-band:

- **ASR / voice transcription** is HTTP from the renderer (`/api/chat/media/voice/transcription/*`) and
  is executed by the Python backend (`src/wechat_decrypt_tool/voice_transcription.py`,
  `native_voice_transcription.py`, `asr_worker.py`) — **[verified router decorator / UNVERIFIED exact call path]**.
- **Private PKI** is a main-process bootstrap only: `resolvePrivatePkiRuntime()` (main.cjs:3474-3476),
  `ensureMacosPrivatePkiTrust()` (main.cjs:3477-3485),
  `configurePrivatePkiUpdateVerification()` (main.cjs:1633) — no renderer IPC ([verified]).
- **Native core** is configured into the backend child's environment by
  `configureNativeCoreRuntime(env)` (main.cjs:2138) and the `native-core-*.cjs` helpers; again no IPC ([verified]).

### 2.4 Security posture of the bridge

**[verified]** `contextIsolation: true`, `nodeIntegration: false` (main.cjs:2519-2520); the only
renderer API is the explicit `wechatDesktop` object. Two handlers additionally check the sender
window (`ai:diagnosticFallback` main.cjs:2808, `ai:takeNavigation` main.cjs:2812); the rest do not.

---

## 3. HTTP API surface as the frontend sees it

### 3.1 Base URL

**[verified]** `frontend/composables/useApiBase.js` (v2.9.0):

- Default is **same-origin `/api`** (so both the Nuxt dev proxy and the backend-mounted UI work).
- Override priority: (1) localStorage key `ui.apiBaseOverride`
  (`frontend/lib/api-settings.js: API_BASE_OVERRIDE_KEY = 'ui.apiBaseOverride'`),
  (2) `runtimeConfig.public.apiBase` ← env `NUXT_PUBLIC_API_BASE`, (3) `/api`.
- `normalizeApiBase()` auto-appends `/api` when a full origin is given without it.
- In desktop dev the stored override is **ignored** when `window.wechatDesktop.__brand` is present.

Effective URLs:

| Context | Effective base | Concrete call |
|---|---|---|
| Packaged desktop | `http://127.0.0.1:10392/api` (UI is served by the same backend) | `GET http://127.0.0.1:10392/api/health` |
| Nuxt dev (`npm run dev`) | `/api` on `http://localhost:3000` | proxied by `nitro.devProxy['/api']` → `http://127.0.0.1:10392/api` (`frontend/nuxt.config.ts`) |
| Pure web | whatever origin serves the app + `/api` | — |

Requests go through `request()` in `frontend/composables/useApi.js`:
`$fetch(url, { baseURL, ...fetchOptions })` with optional `X-WCDA-AI-Trace` / `X-WCDA-Perf-Trace`
headers for diagnostics (**note: none of these is an auth header**).

### 3.2 Auth

**[verified] There is no `Bearer` token / `Authorization` header on normal API calls.** A grep for
`Bearer|Authorization` over `frontend/composables/useApi.js` and `useAiApi.js` returns nothing.
`frontend/plugins/api-check.client.js` polls `healthCheck()` every 30 s with no credentials.

Two narrower mechanisms exist:

1. **Loopback-only guard for AI routers.** `src/wechat_decrypt_tool/routers/ai.py:20-34` defines
   `def local_only(request: Request)` which 403s unless `request.client.host` is loopback, the
   `Origin` hostname is `localhost/127.0.0.1/::1`, and `request.url.hostname` is one of those.
   Applied as a router dependency:
   `APIRouter(prefix="/api/ai", dependencies=[Depends(local_only)])` (ai.py:34),
   `prefix='/api/ai/agent'` (`routers/ai_agent.py`), `prefix='/api/ai/local-search'` (`routers/local_search.py`).
2. **MCP token (only for the LAN-reachable `/mcp` endpoint).**
   `src/wechat_decrypt_tool/routers/mcp.py` reads the token from
   `Authorization`, `X-MCP-Token`, or a `?token=` query parameter. `routers/admin.py` exposes
   `GET /api/admin/mcp-token`. This is server→server/clients, not the web UI.
   **[UNVERIFIED]** whether `/api/admin/*` enforces the same loopback guard (no dependency was
   visible next to `router = APIRouter(route_class=PathFixRoute)` in `admin.py`).

Everything else is plain unauthenticated HTTP bound to `127.0.0.1` by default; CORS is
`allow_origins=["*"]` with `allow_credentials=True` (`src/wechat_decrypt_tool/api.py`, CORSMiddleware).

### 3.3 Endpoint groups (paths as called by the frontend; all are relative to `/api`)

Paths below were extracted from `frontend/composables/useApi.js` (**line numbers are v2.9.0**),
`frontend/composables/useAiApi.js`, and confirmed against upstream router decorators. The upstream
routers spell the prefix out in every route (e.g. `@router.get("/api/chat/sessions", …)`).

| Group | Endpoints the UI calls | Frontend call site |
|---|---|---|
| Health / platform | `GET /health`, `GET /system/platform` | useApi.js:192-198 |
| WeChat detection | `GET /wechat-detection`, `GET /current-account`, `GET /wechat/status` | useApi.js:148-165 |
| Decrypt / keys | `POST /decrypt`, `GET /get_keys`, `GET /keys`, `GET /get_image_key`, `POST /get_image_key_memory`, `POST /media/keys`, `POST /media/decrypt_all` | useApi.js:169, 531, 546, 552, 977 |
| Import decrypted | `POST /import_decrypted/preview`, `POST /import_decrypted`, `POST /import_decrypted/cancel` | useApi.js:177-189 |
| Accounts | `GET /chat/accounts`, `GET /chat/account_info`, `DELETE /chat/account` | useApi.js:200-218 |
| Chat browsing | `GET /chat/sessions`, `GET /chat/messages`, `GET /chat/messages/raw`, `GET /chat/messages/around`, `GET /chat/messages/anchor`, `GET /chat/messages/daily_counts`, `GET /chat/search` | useApi.js:220-256, 310-412 |
| Search index | `GET /chat/search-index/status`, `POST /chat/search-index/build`, `GET /chat/search-index/senders` | useApi.js:328-345 |
| Realtime chat | `GET /chat/realtime/status`, `POST /chat/realtime/sync`, `POST /chat/realtime/sync_all`, SSE `GET /chat/realtime/stream` | useApi.js:259-270; router |
| Contacts | `GET /chat/contacts`, `GET /chat/contacts/profile`, `GET /chat/contacts/group_members`, `POST /chat/contacts/export` | useApi.js:840-886 |
| Chat export | `POST /chat/exports`, `GET /chat/exports`, `GET /chat/exports/{id}`, `DELETE /chat/exports/{id}`, `GET /chat/exports/targets`, `GET /chat/exports/{id}/files[/{file_id}]`, SSE `GET /chat/exports/{id}/events`, `POST /chat/exports/{id}/commit` | useApi.js:737-791; `routers/chat_export.py` |
| Media / voice | `POST /chat/media/voice/transcription[/batch]`, `GET …/status`, `…/cache_lookup`, `…/native/trigger`, `…/models/{id}/download`, `POST /chat/media/emoji/download`, `POST /chat/media/open_folder` | useApi.js (regex sweep) |
| Moments (SNS) | `GET /sns/timeline`, `/sns/users`, `/sns/snapshot/status`, POST `/sns/realtime/full_sync`, `/sns/realtime/sync_latest`, `/sns/self_info`, `/sns/media` | useApi.js:447; `routers/sns.py` |
| Favorites / records | `GET /favorites`, `POST /records/export` | useApi.js:1122-1126 |
| Official accounts (biz) | `GET /biz/list`, `/biz/messages`, `/biz/pay_records` | useApi.js:1043 |
| General records | `GET /general/{path}` (`overview`, `friend-verifications`, `mini-programs`, `finder`, `payments`, `revokes`, `search-records`) | `routers/general.py` |
| Wrapped | `GET /wrapped/annual`, `GET /wrapped/annual/meta`, `GET /wrapped/annual/cards/{id}` | `routers/wrapped.py` |
| Account archive | `POST /account/archive_export`, `GET/DELETE /account/archive_export/{id}` | useApi.js:912-931 |
| System toggles | `GET/POST /system/img_helper/{status,toggle}`, `GET/POST /system/cdn_image/{status,toggle}`, `GET /system/pick_directory` | useApi.js |
| CDN | `GET /cdn/plan`, `POST /cdn/connect`, `/cdn/redeem` | `routers/cdn.py` |
| macOS key capture | `GET /macos-key-capture/status`, `POST /macos-key-capture/{prepare,preflight,capture,cancel}` | useApi.js ~1128; `routers/keys.py` |
| AI (SSE + REST) | `${base}/ai/events`, `${base}/ai/agent/events`, `${base}/ai/local-search/events` (EventSource with `?ai_trace=`), plus `/ai/{settings,model-metadata,profiles,defaults,models,usage,conversations,tasks,rules,alerts,diagnostics/events}` and `/ai/agent/{threads,runs}`, `/ai/local-search/{status,index,device,gpu,conversations}` | `useAiApi.js`; `routers/ai.py`, `ai_agent.py`, `local_search.py` |

SSE note (**[verified]** `frontend/composables/useAiApi.js` `stream()`): the UI uses the **native
`EventSource`**, which cannot send custom headers, so the only correlation data is an `ai_trace`
query parameter. This is why those three event endpoints must be GET + query-based.

Interactive API docs are exposed by FastAPI at `/docs` and logged at startup
(`main.py`: `API文档: http://{access_host}:{port}/docs`).

---

## 4. Nuxt page list (route → feature)

Source of truth: `frontend/pages/**` in the v2.9.0 blob tree, cross-checked against the 15 route
directories actually present in `<install-dir>\WeChatDataAnalysis\resources\ui\`.
Titles/headings below were read from each page file.

| Route | File | Feature | Evidence |
|---|---|---|---|
| `/` | `frontend/pages/index.vue` | Landing / "把微信记录留在本地": 开始检测, 导入备份, 导出归档, 回看聊天; auto-redirects to `/chat` when accounts exist | `useHead` absent; template headings; `onMounted` `listChatAccounts()` |
| `/agreement` | `agreement.vue` | First-use 使用须知与免责声明 (blocks navigation until accepted) | `<h1>使用须知与免责声明</h1>`; `index.html` bootstrap script |
| `/detection-result` | `detection-result.vue` | WeChat detection result / account picker, manual data-root fallback | `<h2>可操作的微信账号</h2>`, `title: '请先登录电脑版微信'` |
| `/decrypt` | `decrypt.vue` | Database decrypt wizard + image keys + batch image decrypt + emoji download (4 tool tabs) | `<h2>数据库解密`, `<h2>图片密钥`, `<h2>批量解密图片`, `<h2>批量下载表情包` |
| `/decrypt-result` | `decrypt-result.vue` | Decryption success screen | `<h2>解密完成！</h2>` |
| `/import` | `import.vue` | Import an already-decrypted backup directory | `<h2>选择备份目录</h2>`, `<h2>数据已就绪</h2>` |
| `/chat` and `/chat/:username` | `chat/[[username]].vue` | Main chat browser: session list, message list, search, media, export, AI sidebar | `title: '聊天记录 - 微信数据库解密工具'` |
| `/contacts` | `contacts.vue` | Contacts list + HTML export | `<h1>联系人</h1>`, `title: '联系人 - 微信数据分析助手'`, `chooseDirectory` |
| `/sns` | `sns.vue` | Moments / 朋友圈 timeline + export | `<h2 id="sns-export-title">导出朋友圈</h2>`, `title: '朋友圈…'` |
| `/favorites` | `favorites.vue` | WeChat favorites (收藏), incl. voice media | `<h1>收藏</h1>` |
| `/payments` | `payments.vue` | Transfers & red packets (转账与红包) | `<h1>转账与红包</h1>` |
| `/finder` | `finder.vue` | Channels / live streams (视频号直播) | `<h1>视频号直播</h1>` |
| `/mini-programs` | `mini-programs.vue` | Mini programs (小程序) | `<h1>小程序</h1>` |
| `/revokes` | `revokes.vue` | Recalled messages / recall cache (撤回 / 可撤回缓存) | `<h1>撤回 / 可撤回缓存</h1>` |
| `/biz` | `biz.vue` | Official-account (服务号) messages — thin wrapper around `BizMessages.vue` | `title: '服务号消息 - WeChatDataAnalysis'`; 316-byte file |
| `/wrapped` | `wrapped/index.vue` | "WeChat Wrapped" annual-summary deck (card-based, exported via IPC screenshot) | `title: '年度总结 · WeChat Wrapped'` |

Routes that exist only as components/dialogs, **not** pages: Settings (`SettingsDialog.vue`),
AI settings (`AiSettings.vue`), export dialogs (`ChatExportDialog.vue`, `GlobalExportDialog.vue`,
`RecordExportDialog.vue`). **[verified]** — no corresponding `pages/` file or packaged route dir.

`frontend/middleware/first-use-agreement.global.js` + `frontend/lib/first-use-bootstrap-script.js`
implement the agreement gate; `frontend/plugins/{api-check,ai-notifications,chat-media-perf,desktop-debug}.client.js`
are the client-only plugins.

---

## 5. Minimum viable subset for a simplified clone (decrypt + browse + export chat)

The whole app spans ~65 backend top-level modules and 15 routes. A "decrypt → browse → export one
chat" clone needs only this:

### 5.1 Frontend pages (4 routes + 1 shell)

| Keep | Why |
|---|---|
| `pages/index.vue` | Entry + "open chat" |
| `pages/detection-result.vue` | Find the WeChat data root / account |
| `pages/decrypt.vue` | Run key acquisition + `POST /decrypt` (can drop the 图片密钥/表情包 tabs) |
| `pages/chat/[[username]].vue` | Session list + message list; **reuse** `composables/chat/useChatSessions.js` and `useChatMessages.js`, `components/chat/SessionListPanel.vue`, `ConversationPane.vue`, `MessageContent.vue` |
| `components/DesktopTitleBar.vue` + `lib/desktop-settings.js` | Needed only if you keep the Electron shell; a pure-web clone can drop IPC entirely |
| Drop | `sns.vue`, `favorites.vue`, `finder.vue`, `payments.vue`, `mini-programs.vue`, `revokes.vue`, `biz.vue`, `wrapped/`, `agreement.vue`, AI components/`stores/*`, `components/wrapped/**` (≈ most of the repo's frontend weight) |

### 5.2 API endpoints (9 calls)

| Step | Endpoints |
|---|---|
| Detect | `GET /api/wechat-detection`, `GET /api/current-account` |
| Get key | `GET /api/get_keys` (Windows) or `GET /api/keys` (stored keys) |
| Decrypt | `POST /api/decrypt` (optionally `GET /api/decrypt_stream` for progress) |
| Accounts | `GET /api/chat/accounts`, `GET /api/chat/account_info` |
| Browse | `GET /api/chat/sessions`, `GET /api/chat/messages` |
| Search (optional) | `GET /api/chat/search` |
| Export chat | `POST /api/chat/exports` + `GET /api/chat/exports/{id}` + `GET /api/chat/exports/{id}/download` (or generate client-side from `/chat/messages` using `lib/xlsx-export.js` / `composables/chat/useChatExport.js`) |

Chat media/voice, realtime sync, SNS, AI and CDN are **not** required.

### 5.3 Backend modules strictly needed

Routers (mount these only):

- `routers/health.py`, `routers/wechat_detection.py`, `routers/keys.py`, `routers/decrypt.py`
- `routers/chat.py` (accounts/sessions/messages/search), `routers/chat_contacts.py` (optional),
  `routers/chat_export.py`, `routers/record_export.py` (if you export favorites/records)

Core services they depend on:

- `wechat_decrypt.py` (58 KB core DB decryption), `wechat_detection.py`, `key_service.py`,
  `key_v4.py`, `isaac64.py`, `dll_key_scan.py`, `key_store.py`
- `chat_accounts.py`, `chat_helpers.py` (105 KB), `session_last_message.py`
- `chat_export_service.py` (398 KB) + `xlsx_export.py` + `export_integrity.py` for export
- `path_fix.py`, `runtime_settings.py`, `logging_config.py`, `app_paths.py`, `network_access.py`,
  `desktop_parent_watchdog.py` (process hygiene), and `api.py` to mount the routers + static UI

Explicitly droppable for the MVP: all `native_core_*.py`, `macos_*.py`, `sns_*.py`,
`voice_transcription.py`, `asr_*.py`, `cdn_image_service.py`, `image_key_*.py`,
`chat_realtime_*.py`, `chat_incremental_export.py`, `qwen_gpu_runtime.py`.

**[UNVERIFIED]** The exact transitive import closure of `chat.py` / `decrypt.py` was not executed;
some listed "droppable" modules (e.g. `native_core_broker.py`, `wcdb_realtime.py`) may still be
imported unconditionally by `chat_helpers.py` or `wechat_decrypt.py`. Verify by running
`python -c "import wechat_decrypt_tool.api"` after deleting modules, or by inspecting the import
header of each kept module before trimming.

### 5.4 Electron shell minimum (if desktop is wanted)

`main.cjs` can be reduced to: single-instance lock → `startBackend()` (spawn interpreter/`uv` with
`WECHAT_TOOL_PORT`/`WECHAT_TOOL_UI_DIR`) → `waitForBackend()` (`GET /api/health`, expect
`{"status":"healthy","service":"微信解密工具"}`, `backend-startup.cjs:47-50`) → `createMainWindow()`
with `preload.cjs` → `loadWithRedirect`/`loadWithRetry` at `http://127.0.0.1:10392/`.
Drop tray, auto-update/electron-updater, Wrapped capture, PKI, native-core policy, output-dir
migration, and all IPC except `window:*` + `debug:log`.

---

## Appendix — Claims marked UNVERIFIED

1. Whether `routers/admin.py` (`/api/admin/mcp-token`) enforces a loopback/token guard — no
   `dependencies=[...]` was visible on its `APIRouter`.
2. The exact call path from `/api/chat/media/voice/transcription/*` into
   `voice_transcription.py` / `native_voice_transcription.py` (only the router paths were read).
3. The transitive import closure analysis for the MVP module trim (§5.3) — no code was executed.
4. Exact line number of the `$fetch(url, { baseURL, ... })` statement in `useApi.js`; the file was
   read partially (helpers + endpoint-definition regions), the helper is inside `request()`
   near the top of the file.
5. Version note: the blob tree and all upstream files were fetched at tag **v2.9.0**. The
   installed Electron sources under `<repo>\app-asar\src\` were confirmed to be
   the extracted real (unminified) source, but they were **not** byte-diffed against v2.9.0 in this
   task; line numbers cited for them are from the local extracted copy.
