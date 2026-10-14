# Build, Replication & Licensing Findings — `LifeArchiveProject/WeChatDataAnalysis`

**Subject:** upstream repo `LifeArchiveProject/WeChatDataAnalysis`, public, branch `main`, tag `v2.9.0`
(installed build at `<install-dir>\WeChatDataAnalysis`, app version 2.9.0).
**Prepared for:** the project owner, as build/replication documentation.
**Method:** static reading of upstream files fetched from `raw.githubusercontent.com`
(`v2.9.0` unless stated) plus the installed artifacts under
`<install-dir>\WeChatDataAnalysis\` and the extracted `app-asar` at
`<repo>\app-asar\`. No npm/uv/PyInstaller/electron-builder build was executed.

> Note on the version label: the *application* version is `2.9.0` (`desktop/package.json`), while the
> *Electron* devDependency is `^40.0.0`. "Electron 2.9.0" conflates the two; there is no Electron 2.9.0 here.

Two structural facts drive everything below:

1. **A normal Windows `Setup.exe` cannot be produced from the public source tree alone.** The GitHub
   build consumes a *separately produced* native core and a *private PKI* signing identity that are not
   in the repo (Section 8.1–8.2).
2. **The "高级版" (advanced) write/send/automation implementation is deliberately absent.** The public
   repo is the read-only edition; the advanced edition is a separate build merged into the same GitHub
   release as `advanced-*` assets (Section 8.3).

---

## 1. Repository layout for a builder

From the full recursive tree (`upstream_tree.json` / `upstream_blobs.txt`, 1 284 blobs):

| Path | Role |
| --- | --- |
| `main.py` | Source-mode launcher. Inserts the checkout's `src/` at `sys.path[0]` **before any project import**, then runs `uvicorn` on `wechat_decrypt_tool.api:app`, default `127.0.0.1:10392` (env `WECHAT_TOOL_PORT`). Prints the resolved `wechat_decrypt_tool.__file__` as "代码来源" so you can see whether you are running the checkout or a stale `site-packages` copy. |
| `pyproject.toml` | Project `wechat-decrypt-tool` 2.9.0, `requires-python >=3.11`. Runtime deps (FastAPI/uvicorn, cryptography, pycryptodome, zstandard, pypinyin, jieba, pywin32/pymem/pefile/yara-python on Windows, `wx_key>=2.0.1` via `[tool.uv] find-links = ["./tools/key_wheels/"]`, langchain/langgraph/deepagents, onnxruntime/tokenizers/sqlite-vec, python-docx/pptx/openpyxl). Extras: `build` (pyinstaller), `voice-transcription` (faster-whisper, opencc, sherpa-onnx), `voice-transcription-gpu` (torch 2.9.1 cu128, transformers). `[tool.hatch.build.targets.wheel] include` lists the prebuilt native binaries that must ship in the wheel. |
| `src/wechat_decrypt_tool/` | The FastAPI backend. `api.py` (app + router mounting), `backend_entry.py` (PyInstaller entry), `routers/` (26 modules: `chat.py` 384 KB, `chat_media.py`, `sns.py`, `general.py`, `keys.py`, `decrypt.py`, `chat_export.py`, `record_export.py`, `ai*.py`, `mcp.py`, …), `ai/` (deep-agent runtime), `native/` (prebuilt `img_helper.dll`, `wce_integrity.pyd`, SDL2/VoipEngine/VC-runtime DLLs, macOS universal `libwx_key.dylib`, `weflow_wasm/`), `native_core_*` (broker/client/lease/telemetry), `macos_*` capture helpers, `chat_export_service.py` (398 KB), `mcp/`, `wrapped/` (annual-summary cards), `local_search/`. |
| `frontend/` | Nuxt 4 app (`nuxt` ^4.0.1, Vue 3, Pinia, Tailwind 4 via `@tailwindcss/vite`). `pages/` (decrypt, chat, contacts, sns, favorites, wrapped, agreement…), `components/`, `composables/`, `lib/`, `public/` (large demo GIF/PNG assets), `tests/` (node:test + vitest). Static output is `.output/public` (`nuxt generate`). |
| `desktop/` | Electron shell + all packaging logic. `src/main.cjs` (3 577 lines in the installed asar), `src/preload.cjs`, `scripts/*` (build/packaging/signing/smoke), `resources/{ui,backend,signing}` (placeholder `.gitkeep` in the repo; populated by the build), `resources/native-core-source-{windows,macos}.json` (pinned source-runtime references), `vendor/koffi/.../koffi.node` (prebuilt). |
| `tools/` | `rebuild_wcdb_release.py` (downloads the exact private-producer Release asset), `build_ai_smoke.cjs`, `build_local_search_catalog.py`, `build_local_search_gpu_manifest.py`, `build_wce_integrity.ps1`, `generate_qwen_gpu_manifest.py`, `qwen_runtime_stdlib.py`, `smoke_native_core_real_database.py`, `verify_*`, `key_wheels/wx_key-2.0.1-cp310…cp314-win_amd64.whl`. |
| `macos-key-extractor/` | Standalone Python + LLDB Apple-Silicon WCDB key capture tool (`main.py`, `key_extractor/`, `build-macos.sh`, its own `THIRD_PARTY_NOTICES.md`). Built separately from the Electron app. |
| `website/` | Static marketing site (`index.html`, `assets/js/main.js`) **and** the `pro-demos` animation engine (`assets/js/pro-demos/scenes/{action,add-a,add-b,automation,contact,edit,group,moments}.js`). `frontend/nuxt.config.ts` aliases `@website → ../website/assets` and allows it in the Vite dev-server `fs.allow`; the app's "advanced features" dialog reuses these demo scenes. |
| `skills/wechat-mcp-copilot/` | MCP playbook bundled into the backend by PyInstaller (`--add-data skills/wechat-mcp-copilot`). |
| `docs/`, `tests/` | ~330 pytest modules at repo root (`tests/`) plus per-package tests (`frontend/tests`, `desktop/tests`), and design/acceptance notes in `docs/`. |
| `.github/workflows/` | `release.yml` (42 935 B) is the whole Windows+macOS release pipeline; plus `macos-private-build.yml`, `windows-source-runtime-promotion.yml`, `macos-source-runtime-*.yml`, `installer-unicode.yml`, `pages.yml`, etc. |

---

## 2. Exact Windows build/packaging chain

### 2.1 `desktop/package.json` scripts (verbatim behaviour)

| Script | Command |
| --- | --- |
| `dev` | `node scripts/dev.cjs` |
| `dev:gpu` | `cross-env WECHAT_TOOL_QWEN_GPU=1 node scripts/dev.cjs` |
| `dev:static` | `npm --prefix ../frontend run generate && cross-env WECHAT_TOOL_STATIC_UI=1 electron .` |
| `build:ui` | `npm --prefix ../frontend run generate && node scripts/copy-ui.cjs` |
| `build:backend` | `uv sync --no-editable --extra build --extra voice-transcription && node scripts/build-backend.cjs` |
| `build:backend:gpu` | `uv sync --no-editable --extra build --extra voice-transcription --extra voice-transcription-gpu && node scripts/build-backend.cjs --qwen-gpu` |
| `build:icon` | `node scripts/build-icon.cjs` |
| `dist` | `npm run dist:win` |
| `dist:win` | `npm run build:ui && npm run build:backend && npm run build:icon && electron-builder --win --x64 --publish never` |
| `dist:mac` / `dist:mac:arm64` | verify native → `build:ui` → `build:backend` → `build:icon` → `electron-builder --mac dmg zip --arm64` |
| `smoke:win` / `smoke:win:real` | `node scripts/smoke-windows-package.cjs` / `smoke-windows-real-database.cjs` |

So the Windows installer chain is exactly:

```
npm run dist
 └─ dist:win
     ├─ build:ui      → nuxt generate (frontend) + copy-ui.cjs → desktop/resources/ui
     ├─ build:backend → uv sync --extra build --extra voice-transcription
     │                  + build-backend.cjs → PyInstaller onefile → desktop/resources/backend/wechat-backend.exe
     │                  + desktop/resources/backend/native/** (staged, NOT inside the exe)
     ├─ build:icon    → desktop/build/{icon,installerIcon,uninstallerIcon,installerHeaderIcon}.ico
     └─ electron-builder --win --x64 --publish never
                        beforePack → native-core-before-pack.cjs
                        afterPack  → after-pack.cjs (macOS-only; returns immediately on win32)
                        afterSign  → after-sign.cjs
                        NSIS → desktop/dist/WeChatDataAnalysis-2.9.0-Setup.exe (+ .blockmap, latest.yml)
```

### 2.2 `desktop/scripts/dev.cjs` (dev orchestration)

Inputs: repo checkout, `desktop/node_modules` (it does `require("electron")`), `frontend/node_modules`.
Behaviour:
1. `ensureSourceNativeCore({ env })` from `desktop/src/source-native-core-bootstrap.cjs` **before any child
   process starts** — on Windows this delegates to `windows-source-native-core-bootstrap.cjs` (Section 6.3).
2. Picks a frontend port (`NUXT_PORT` or first free from 3000, host `NUXT_HOST`/`127.0.0.1`) and a backend
   port (`WECHAT_TOOL_PORT` or first free from 10392). If an explicitly requested port is taken it **throws**.
3. Spawns `nuxi dev` using the *same Node* (`process.execPath frontend/node_modules/@nuxt/cli/bin/nuxi.mjs dev`)
   with `NUXT_HOST`/`NUXT_PORT`/`WECHAT_TOOL_PORT`/`ELECTRON_START_URL`, waits for the URL to answer (60 s).
4. Spawns Electron (`require("electron")` with cwd `desktop`, arg `.`) with those env vars.
5. Ctrl-C / child exit kills the whole tree (`taskkill /pid <pid> /t /f` on Windows).

Outputs: a running Nuxt dev server, a running Electron window, and the Electron-spawned backend
(`desktop/src/main.cjs` lines 2173–2184): `uv run --no-dev --extra voice-transcription main.py`
(cwd = repo root). In packaged mode main.cjs instead spawns `resources/backend/wechat-backend.exe`
(lines 2155–2172) and sets `WECHAT_TOOL_UI_DIR = process.resourcesPath/ui` (line 2146).

### 2.3 `desktop/scripts/build-backend.cjs` (the heavyweight step, 27.7 KB)

Inputs/outputs:

| Stage | Input | Output |
| --- | --- | --- |
| `buildIntegrityNativeBinary()` | darwin/linux only; `WCE_INTEGRITY_ARTIFACT_DIR` or private `native/wce_integrity/Cargo.toml` | `libwce_integrity.dylib/.so` (on win32 returns `null` — Windows uses the committed `src/.../native/wce_integrity.pyd`) |
| `prepareRuntimeNativeDir()` | `src/wechat_decrypt_tool/native/` | `desktop/build/native-runtime/` with `macos/db-key`, `__pycache__`, retired ASR files and the native-core trio filtered out |
| `stageNativeCoreArtifacts()` | `WCE_NATIVE_CORE_ARTIFACT_DIR` | copies `wechatdb_client.dll`, `wechatdb_broker.exe`, `wechatdb_native_build.json` into `build/native-runtime/` |
| `stageMacosXkeyArtifacts()` | macOS key contract | macOS key helper (skipped on Windows) |
| `validateRuntimeNativeHelpers()` | — | asserts the four `weflow_wasm/` files exist (and macOS helper bits) |
| `runIntegrityPreflight()` | `uv run python -c` importing `export_integrity.load_wce_integrity_native()` | fails the build if the integrity module is missing/stale |
| PyInstaller | `src/wechat_decrypt_tool/backend_entry.py` | `desktop/resources/backend/wechat-backend.exe` |
| smoke tests | the packaged exe | `--smoke-opencc`, `--smoke-watchfiles`, `--smoke-ai` must exit 0 |
| final copy | `build/native-runtime/` | `desktop/resources/backend/native/` (deliberately **outside** the onefile exe so the broker/client have stable paths) |
| cleanup | — | removes a stale `dist/pyproject.toml` |

Hard gates that matter for replication:
* `resolveNativeCoreArtifacts()` throws `Missing WCE_NATIVE_CORE_ARTIFACT_DIR…` on `win32`/`darwin` when the
  env var is absent; `required` is true whenever native artifact names exist for the platform.
* A non-production manifest is refused unless **both** `WCE_NATIVE_CORE_ARTIFACT_DIR` **and**
  `WCE_NATIVE_CORE_ALLOW_DEVELOPMENT_ARTIFACTS=1` are set (and that override is explicitly forbidden when `CI` is set).
* `nativeCoreProductionManifestErrors()` enforces: `distributionMode === "public"`, no `distributionCapsule`,
  `developmentBuild === false`, exact 45-day build window, `readOnlyBuild === true`, `wechatActions === []` (schema 2),
  `offlineBootstrapFeatureBits === 3`, `offlineExportSealFormat === "WES2"`, non-zero distinct Windows client/broker
  signer pins, private-PKI pin fields, and a `buildId` containing no dev/test/staging label.

### 2.4 `desktop/scripts/copy-ui.cjs`

`frontend/.output/public` → `desktop/resources/ui`. Fails if `index.html` is absent (i.e. if you forgot
`npm --prefix frontend run generate`). It deletes everything in the destination except `.gitkeep`, then `cpSync` recursively.

### 2.5 `desktop/scripts/ai-packaging.cjs`

Builds the PyInstaller args for the AI stack: `--collect-all` for `deepagents, langchain, langchain_core,
langchain_openai, langchain_anthropic, langchain_google_genai, langgraph, langsmith, wcmatch, bracex, pypdf,
pypdfium2, pypdfium2_raw, tiktoken, docx, pptx, openpyxl, onnxruntime, tokenizers, sqlite_vec, huggingface_hub`;
`--add-data` for `local_search_models.json` and `local_search_gpu.json`; `--collect-submodules tiktoken_ext`;
`--hidden-import langgraph.checkpoint.sqlite.aio`. Also exports `runPackagedAiSmoke()` which runs
`wechat-backend.exe --smoke-ai` in a temp cwd and requires `{ok:true, frozen:true}`.

### 2.6 `desktop/scripts/after-pack.cjs` / `after-sign.cjs`

* `after-pack.cjs` **returns immediately unless `context.electronPlatformName === "darwin"`** — it is macOS-only
  (ad-hoc `codesign --deep --sign -`, or Developer-ID env checks for distribution builds). Windows packaging is
  unaffected by it.
* `after-sign.cjs` (5 928 B) is referenced from the builder config but was **not fetched/read in this analysis — UNVERIFIED**.

### 2.7 `desktop/scripts/integrity-native-packaging.cjs`

macOS-only pinned-artifact resolver for `libwce_integrity.dylib`. It requires an artifact directory containing
exactly `{libwce_integrity.dylib, wce_integrity_build.json, provenance.json, SHA256SUMS.txt}` plus env pins
(`WCE_INTEGRITY_ARTIFACT_DIR`, `…_REPOSITORY`, `…_SOURCE_REVISION`, `…_UI_SOURCE_REPOSITORY`,
`…_UI_SOURCE_REVISION`, `…_BUILD_ID`, `…_ARTIFACT_RUN_ID`, `…_BINARY_SHA256`), and validates the binary with
`lipo`/`nm` (`_PyInit_wce_integrity`), the workflow name `macos-integrity-production.yml`, `distributionMode === "public"`,
`development === false`, and a 45-day window. On Windows this file is not on the critical path.

### 2.8 `desktop/scripts/native-core-before-pack.cjs` (electron-builder `beforePack`)

Runs on **every** platform. It (`validatePackagedBackend`) requires `resources/backend/wechat-backend.exe` plus the
three native-core files to exist and to carry a *production* manifest (same policy as above), rejects legacy
`wcdb_api.dll`/`WCDB.dll` if present, and then stages signing evidence:
* Windows: requires the manifest `windowsSignerTrustMode === "private-pki"`, `windowsPrivateRootSha256` matching
  `WCE_WINDOWS_PRIVATE_ROOT_SHA256`, and a real cert file at `WCE_WINDOWS_PRIVATE_ROOT_CERT_PATH` whose hash matches;
  it copies the cert + `windows-private-pki.ps1` into `desktop/resources/signing/` (which the repo ships as `.gitkeep` only).
* macOS: analogous with `macosPrivateRootSha256` / `WCE_MACOS_PRIVATE_ROOT_CERT_PATH`.

This is the point where a public-source build dies even if the native core is supplied: **without the private root
certificate the hook throws.**

### 2.9 `desktop/scripts/build-icon.cjs`

Reads `frontend/public/logo.png`, pads it to a square PNG, writes `desktop/src/icon.png`, and writes the same ICO
(bytes from `png-to-ico`) to `desktop/build/{icon,installerIcon,uninstallerIcon,installerHeaderIcon}.ico` and
`desktop/resources/icon.ico`. It `require("pngjs")`, which is **not** a declared dependency in `desktop/package.json`
(it arrives transitively via `png-to-ico`) — works only because npm hoists it. Minor fragility, factual observation.

### 2.10 Other relevant scripts (not deeply analysed)

`installer-custom.nsh` (NSIS include, 16 168 B), `installer-install-dir.ps1`, `installer-output-dir.ps1`,
`windows-private-pki-sign.cjs` (the electron-builder `sign` hook), `windows-private-pki.ps1`,
`smoke-windows-package.cjs`, `smoke-windows-real-database.cjs` (63.5 KB), `Import-/Remove-WindowsCloudSigningIdentity.ps1`,
`macos-*` scripts, `verify-macos-*.cjs`, `windows-source-runtime-promotion.cjs`. Their contents were not read — **UNVERIFIED**.

---

## 3. PyInstaller invocation (precise)

There is **no committed `.spec` file**. `build-backend.cjs` calls PyInstaller through uv with a fully explicit CLI:

```
uv run pyinstaller --noconfirm --clean --name wechat-backend --onefile \
  --distpath   <repo>/desktop/resources/backend \
  --workpath   <repo>/desktop/build/pyinstaller \
  --specpath   <repo>/desktop/build/pyinstaller-spec \
  --add-data   <repo>/desktop/build/native-runtime;wechat_decrypt_tool/native \
  --add-data   <repo>/skills/wechat-mcp-copilot;skills/wechat-mcp-copilot \
  --add-data   <repo>/src/wechat_decrypt_tool/resources/macos_db_key_contract.json;wechat_decrypt_tool/resources \
  --collect-all faster_whisper --collect-all ctranslate2 --collect-all av \
  --collect-all opencc --collect-all sherpa_onnx \
  --add-data   <repo>/src/wechat_decrypt_tool/resources/voice_models.json;wechat_decrypt_tool/resources \
  --add-data   <repo>/src/wechat_decrypt_tool/resources/qwen_gpu_runtime.json;wechat_decrypt_tool/resources \
  --collect-all watchfiles \
  <…aiPackagingArgs…> \
  [--exclude-module torch --exclude-module transformers | --collect-all torch --collect-all transformers] \
  [win32 only: --version-file <generated VSVersionInfo.txt> \
               --hidden-import wechat_decrypt_tool.key_v4 --hidden-import yara \
               --hidden-import <each module from tools/qwen_runtime_stdlib.py>] \
  [--add-binary <libwce_integrity*>;wechat_decrypt_tool/native]   # darwin/linux only
  <repo>/src/wechat_decrypt_tool/backend_entry.py
```

Key points:
* **onefile** (`--onefile`), not onedir. The shipped `resources/backend/wechat-backend.exe` is 195 939 800 bytes.
* `--add-data` separator is platform-dependent (`;` on win32, `:` elsewhere) via `path.delimiter`.
* The native core is **not** inside the exe: after PyInstaller succeeds, `build/native-runtime/` is copied to
  `desktop/resources/backend/native/`, giving the installed layout
  `resources/backend/wechat-backend.exe` + `resources/backend/native/{wechatdb_client.dll, wechatdb_broker.exe,
  wechatdb_native_build.json, wce_integrity.pyd, img_helper.dll, SDL2.dll, VoipEngine.dll, msvcp140*.dll,
  vcruntime140*.dll, weflow_wasm/…}`.
* `--version-file` is generated in `desktop/build/pyinstaller/wechat-data-analysis-version.txt` with
  `CompanyName/ProductName = LifeArchiveProject / WeChatDataAnalysis`, `OriginalFilename = wechat-backend.exe`,
  translation `(2052, 1200)` (Simplified Chinese), derived from `desktop/package.json` `version`.
* Cold start is slow by design: `desktop/src/backend-startup.cjs` sets the packaged backend startup timeout to
  **180 000 ms** (`PACKAGED_BACKEND_STARTUP_TIMEOUT_MS`, lines 1–5), with a comment that captured cold starts exceeded 90 s.

---

## 4. Nuxt build → `desktop/resources/ui`

* `frontend/package.json`: `"generate": "nuxt generate"` (Nuxt ^4.0.1). `desktop` calls
  `npm --prefix ../frontend run generate`.
* Output: `frontend/.output/public`. `copy-ui.cjs` copies it verbatim to `desktop/resources/ui`, and
  `electron-builder.extraResources` maps `resources/ui → ui` inside the app.
* **Base path is `/`** — `nuxt.config.ts` does not set `app.baseURL`. Verified in the packaged
  `<install-dir>\WeChatDataAnalysis\resources\ui\index.html`, whose refs are absolute:
  `/_nuxt/entry.*.css`, `/_nuxt/*.js`, `/_payload.json?…`, `/logo.png`, `/import`, `/chat`, `/agreement?redirect=%2F`.
  Because assets are root-absolute, the UI is **served over HTTP by the Python backend**, not loaded over `file://`:
  `desktop/src/renderer-startup.cjs` lines 61–66 (`resolveDesktopUiUrl`) returns the backend URL when packaged or
  when `WECHAT_TOOL_STATIC_UI=1`, and `main.cjs` line 2146 sets `WECHAT_TOOL_UI_DIR = <resources>/ui`.
* **Prerendering:** `nuxt generate` prerenders the route tree, producing one `<route>/index.html` per page
  (confirmed in the installed `resources/ui/`: `agreement/`, `biz/`, `chat/`, `contacts/`, `decrypt/`,
  `decrypt-result/`, `detection-result/`, `favorites/`, `finder/`, `import/`, `mini-programs/`, `payments/`,
  `revokes/`, `sns/`, `wrapped/`), each with a 69-byte `_payload.json`, plus root `index.html`, `200.html`, `404.html`.
  `pages/chat/[[username]].vue` is an optional param route, so it prerenders to `chat/index.html`.
* `experimental.appManifest: false` avoids the `/_nuxt/builds/meta/dev.json` preload request.
* Dev proxy: `nitro.devProxy['/api'] → http://127.0.0.1:${WECHAT_TOOL_PORT}/api`.
* Tailwind 4 is wired through the Vite plugin `@tailwindcss/vite`; global CSS is listed under `css:`
  (`~/assets/css/tailwind.css`, FontAwesome, `chat.css`, `record-pages.css`, `export-panels.css`, `wxcdn-card.css`).
* `@website → ../website/assets` gives the app the `pro-demos` animation engine (the "advanced features" demos).

---

## 5. electron-builder configuration (`desktop/package.json` → `"build"`)

| Key | Value |
| --- | --- |
| `appId` | `com.lifearchive.wechatdataanalysis` |
| `productName` | `WeChatDataAnalysis` |
| `artifactName` | `${productName}-${version}-Setup.${ext}` → `WeChatDataAnalysis-2.9.0-Setup.exe` |
| `asar` | `true` (output `desktop/dist`) |
| `icon` | `build/icon.ico` |
| hooks | `beforePack: scripts/native-core-before-pack.cjs`, `afterPack: scripts/after-pack.cjs`, `afterSign: scripts/after-sign.cjs` |
| `electronFuses` | `runAsNode: true`, `resetAdHocDarwinSignature: true` |
| `files` | `src/**/*`, `!src/wcdb-sidecar.cjs`, `package.json`, plus a curated `node_modules` allowlist (`electron-updater`, `builder-util-runtime`, `debug`, `ms`, `sax`, `js-yaml`, `argparse`, `lazy-val`, `lodash.escaperegexp`, `lodash.isequal`, `tiny-typed-emitter`, `fs-extra`, `graceful-fs`, `jsonfile`, `universalify`, `semver`) |
| `extraResources` | `resources/ui → ui`; `resources/backend → backend`; `resources/signing → signing` filtered to `macos-private-pki-root.cer`, `windows-private-pki-root.cer`, `windows-private-pki.ps1`; `node_modules/ffmpeg-static → ffmpeg` filtered to ffmpeg + LICENSE/README |
| `win` | `forceCodeSigning: true`; `target: ["nsis"]`; `signExts: ["!wechatdb_broker.exe","!wechatdb_client.dll","wce_integrity.pyd","img_helper.dll"]`; `signtoolOptions.sign = ./scripts/windows-private-pki-sign.cjs`, `publisherName: ["WDA Private PKI"]`, `signingHashAlgorithms: ["sha256"]`, `rfc3161TimeStampServer: http://timestamp.digicert.com` |
| `nsis` | `oneClick: false`, `allowToChangeInstallationDirectory: true`, `include: scripts/installer-custom.nsh`, plus `installerIcon`/`uninstallerIcon`/`installerHeaderIcon` from `build/*.ico` |
| `mac` | dmg+zip, `minimumSystemVersion: 15.0`, hardened runtime, `entitlements(.Inherit): entitlements.mac.plist`, `sign: scripts/sign-macos.cjs` |
| `publish` | `provider: github`, `owner: LifeArchiveProject`, `repo: WeChatDataAnalysis`, `releaseType: release` |

Notes:
* `files` explicitly excludes `src/wcdb-sidecar.cjs`, but **that file does not exist in the public tree** — a leftover
  reference to a private-only module.
* `signExts` excludes the two native-core binaries from electron-builder's signing because they are pre-signed by the
  private PKI; only `wce_integrity.pyd` and `img_helper.dll` are signed by the hook.
* `forceCodeSigning: true` + a custom `sign` hook means the stock config cannot emit an installer without the private
  PKI material (Section 8.2).

---

## 6. Replicate-it runbook (Windows x64 dev machine)

### 6.1 Prerequisites

| Requirement | Evidence |
| --- | --- |
| Windows 10/11 **x64** | `windows-source-native-core-bootstrap.cjs` rejects `process.arch !== "x64"` ("Windows source runtime currently supports x64 only"). |
| Python **3.11+** | `pyproject.toml` `requires-python = ">=3.11"`; repo has `.python-version`. |
| [uv](https://docs.astral.sh/uv/) | every backend step is `uv sync` / `uv run`; `main.cjs` spawns `uv` directly. |
| Node.js + npm | `electron ^40`, `electron-builder 26.15.3`, `nuxt ^4.0.1`. **No `engines` field, so the exact Node floor is UNVERIFIED** (Electron 40 / electron-builder 26 imply a modern LTS; Node 20/22 is the safe assumption). |
| git | to clone. |
| Network | GitHub release assets (native core), npm registry, PyPI, and PyTorch cu128 index only for GPU builds. |
| ~free disk | onefile backend ≈ 196 MB; UI assets ≈ 60 MB; `desktop/dist` installer ≈ hundreds of MB. UNVERIFIED. |

### 6.2 Ordered commands

```powershell
# 1) Clone (tag v2.9.0 or main)
git clone https://github.com/LifeArchiveProject/WeChatDataAnalysis.git
cd WeChatDataAnalysis

# 2) Python env (runtime deps). --no-editable as documented in README.
uv sync --no-editable

# 3) Desktop deps — REQUIRED before dev: dev.cjs requires "electron",
#    and the Windows source-runtime bootstrap needs electron-winstaller's vendored 7z.
cd desktop
npm ci
cd ..

# 4) Frontend deps
cd frontend
npm ci
cd ..

# 5) Run full desktop dev mode (Nuxt dev server + Electron + backend)
cd desktop
npm run dev
#   GPU/profile override: npm run dev:gpu
```

`npm run dev` should end with: Nuxt on `http://127.0.0.1:3000` (or first free port), backend on
`http://127.0.0.1:10392/api` (spawned by Electron as `uv run --no-dev --extra voice-transcription main.py`),
and the Electron window loading `ELECTRON_START_URL`.

Alternative manual modes (README §2.5):
```powershell
uv run --no-sync main.py            # backend only, from repo root
cd frontend; npm run dev            # frontend dev server only
```

### 6.3 What `npm run dev` silently does on first run (Windows)

`dev.cjs → ensureSourceNativeCore → ensureWindowsSourceNativeCore`
(`desktop/src/windows-source-native-core-bootstrap.cjs`) reads
`desktop/resources/native-core-source-windows.json`:

```json
{ "schemaVersion": 3, "platform": "win32", "architecture": "x64",
  "publisherRepository": "LifeArchiveProject/WeChatDataAnalysis",
  "releaseTag": "v2.4.0",
  "assetName": "WeChatDataAnalysis-2.4.0-Setup.exe",
  "assetSha256": "c924347408a94947588b54bf26352ced396513d3bef9386595c36a8210c1410a",
  "buildId": "wcdb-prod-20260907-v240-6",
  "nativeFiles": { "wechatdb_client.dll": "59fdf95b…", "wechatdb_broker.exe": "eae2450c…",
                   "wechatdb_native_build.json": "27b41c72…" },
  "expiresAtUnix": 1792680288 }
```

Because `schemaVersion === 3` selects "installer" mode, it:
1. downloads the **public** release asset `WeChatDataAnalysis-2.4.0-Setup.exe` with `curl.exe`
   (URL `https://github.com/<publisherRepository>/releases/download/<releaseTag>/<assetName>`);
2. uses `electron-winstaller`'s vendored `vendor/7z.exe` to `e` extract `$PLUGINSDIR/app-64.7z`, then extracts
   `resources/backend/native/{wechatdb_client.dll, wechatdb_broker.exe, wechatdb_native_build.json}` from it
   (utf8-agnostic allowlist; nothing else is taken);
3. verifies the SHA-256 of each against `nativeFiles`, then re-validates the runtime policy
   (`artifactState === "source-public"`, `manifest.buildId === pin.buildId`,
   `manifest.buildExpiresAtUnix === pin.expiresAtUnix`);
4. caches the result under `WCE_NATIVE_CORE_SOURCE_CACHE_DIR` (or the default cache root), keyed
   `<releaseTag>-<assetSha256[:12]>`, and repairs a missing/modified cache from the immutable asset.

**Operational warning (verified):** `assertActivePin()` throws once `nowUnix >= expiresAtUnix`. The pin shipped at
`v2.9.0` **and on `main`** (both fetched, identical) expires at `expiresAtUnix = 1792680288` =
**2026-10-22T14:44:48Z**. `npm run dev` on Windows therefore fails closed after that timestamp unless the pin is
refreshed (`.github/workflows/windows-source-runtime-promotion.yml` exists for exactly that purpose). You can pre-seed
`WCE_NATIVE_CORE_SOURCE_CACHE_DIR` to work offline.

The same identity appears in the shipped app: `wechatdb_native_build.json` has
`buildIssuedAtUnix 1791697943` (2026-10-11T05:52:23Z) and `buildExpiresAtUnix 1795585943` (2026-11-25T05:52:23Z),
i.e. a 45-day window starting at build time.

### 6.4 Producing the installer

```powershell
cd desktop
npm run dist            # = npm run dist:win
# → desktop/dist/WeChatDataAnalysis-2.9.0-Setup.exe (+ .blockmap, latest.yml)
```

**This will fail on a stock public checkout**, in this order:
1. `build:backend` → `resolveNativeCoreArtifacts()` throws `Missing WCE_NATIVE_CORE_ARTIFACT_DIR…` (win32 requires it).
2. Even with artifacts supplied, `native-core-before-pack.cjs` requires the private root cert
   (`WCE_WINDOWS_PRIVATE_ROOT_CERT_PATH` + `WCE_WINDOWS_PRIVATE_ROOT_SHA256`) matching the manifest.
3. Even past that, `forceCodeSigning: true` + `scripts/windows-private-pki-sign.cjs` need the private-PKI signing
   identity (release.yml supplies `WCE_WINDOWS_CLIENT_CERT_THUMBPRINT`, `WCE_WINDOWS_CLIENT_SIGNER_SHA256`,
   `WCE_WINDOWS_PRIVATE_ROOT_SHA256`, `WCE_WINDOWS_PRIVATE_ROOT_CERT_PATH`, `WCE_RFC3161_TIMESTAMP_URL`,
   `WCE_WINDOWS_SIGNING_ASSURANCE`, lines 453–461).

The only documented escape hatch is a **local, non-publishable** build:
`WCE_NATIVE_CORE_ARTIFACT_DIR=<dir>` + `WCE_NATIVE_CORE_ALLOW_DEVELOPMENT_ARTIFACTS=1` (rejected when `CI` is set).
It still needs the native trio and still hits the signing gates; whether one can override
`--config.win.forceCodeSigning=false --config.win.signtoolOptions.sign=null` to get an unsigned installer is
**UNVERIFIED** (not documented, not attempted here).

### 6.5 How upstream actually builds the release (reference)

`.github/workflows/release.yml`:
* job downloads/validates the native core into
  `WCE_NATIVE_CORE_ARTIFACT_DIR=${{ runner.temp }}/wechatdb-native-windows-x64-source-public` (line 243),
  after `python tools/rebuild_wcdb_release.py --component windows-native` (lines 60–67) pulls the exact Release asset
  from the private producer repo, authenticated by secret `WCE_NATIVE_CORE_PRODUCER_TOKEN`;
* validates manifest fields, checksums, and private-PKI Authenticode pins (lines 238–400);
* runs `npm run dist:win` with cloud-signing env (line 461);
* verifies the signed `win-unpacked` binaries byte-identical to the source artifact and asserts exactly one `*Setup*.exe`
  (lines 463–600);
* downloads `advanced-*` assets from the `advanced-latest` release and publishes everything through
  `softprops/action-gh-release` (lines 139–152, 732–784).
* `docs/release-native-build.md` documents the producer side: Windows/macOS native components are rebuilt from the
  current `2977094657/WCDB` main revision by four production workflows, get a unique build ID, and **expire exactly
  45 days** after issuance; failures stop packaging.

---

## 7. Legal / licensing picture (factual, **not legal advice**)

### 7.1 License status

* **There is no `LICENSE`, `COPYING`, or project-level license file anywhere in the repository.** Verified two ways:
  the recursive tree (`upstream_tree.json`) contains only `THIRD_PARTY_NOTICES.md`,
  `macos-key-extractor/THIRD_PARTY_NOTICES.md`, `docs/licenses/lobe-icons-MIT.txt`,
  `docs/licenses/wcdb-key-tool-MIT.txt`, `frontend/components/ai-elements/LICENSE`,
  `frontend/vendor/assistant-ui-vue/LICENSE`, and `src/wechat_decrypt_tool/native/macos/WEFLOW_LICENSE.txt`; and
  `https://api.github.com/repos/LifeArchiveProject/WeChatDataAnalysis` reports `license: null` / `spdx_id: null`.
* Consequence under default copyright law: absent an explicit grant, the project's own code is **all rights
  reserved** by its authors, and there is no implied permission to copy, modify, or redistribute it. Using it locally
  for your own data is what the README contemplates; redistributing it (or a derivative installer) is a separate act
  that needs the copyright holder's permission. Trademark/brand marks (WeChat, Tencent, AI-provider logos) belong to
  their owners and are not licensed by this repo.
* `THIRD_PARTY_NOTICES.md` obligations apply **only to embedded third-party components** and grant nothing for the
  project's own code. Its concrete obligations if you redistribute the app:
  * AI provider SVG marks from `@lobehub/icons-static-svg` 1.95.0 (Lobe Icons, MIT) — keep the notice and the MIT text
    (`docs/licenses/lobe-icons-MIT.txt`). Doubao/LM Studio **PNG** icons are *not* MIT-covered.
  * Optional local voice transcription: `faster-whisper` (MIT), `CTranslate2` (MIT), `PyAV` (BSD-3-Clause),
    `opencc-python-reimplemented` (Apache-2.0), ONNX Runtime (MIT), HF tokenizers (Apache-2.0). Whisper model weights
    are **not** bundled — users fetch them under the model's own license.
  * Optional macOS WCDB passphrase capture adapts `TANGandXUE/wcdb-key-tool` (MIT); full text in
    `docs/licenses/wcdb-key-tool-MIT.txt`.
  * Local semantic search: ONNX Runtime (MIT), HF Hub (Apache-2.0), tokenizers (Apache-2.0), sqlite-vec (MIT/Apache-2.0
    dual); models pulled from pinned HF revisions (BAAI BGE Chinese, intfloat multilingual E5 Small), recorded in
    `local_search_models.json`.
  * Optional NVIDIA GPU runtime downloads pinned CUDA/cuBLAS/cuDNN/cuFFT wheels that keep their **NVIDIA** license
    terms and bundled notices; ONNX Runtime GPU stays MIT.
  * `src/wechat_decrypt_tool/native/macos/WEFLOW_LICENSE.txt` (14 601 B) accompanies the bundled WeFlow WASM/DLL
    components.
  * `macos-key-extractor/THIRD_PARTY_NOTICES.md` (376 B) — see file for its components.
* The repo also carries vendored code with its own small licenses: `frontend/vendor/assistant-ui-vue/LICENSE` and
  `frontend/components/ai-elements/LICENSE`.

### 7.2 README disclaimers (verbatim gist)

* `## 安全说明`: **仅限个人使用** — "this tool is only for decrypting your own WeChat data"; keep your key secret;
  the decrypted data is private; comply with applicable law; do not use for illegal purposes.
* `## 免责声明`: independent, unofficial project with **no affiliation/authorization/partnership with WeChat/Tencent**;
  use only on data you lawfully hold or are authorized to access; back up data/keys; memory scanning, hooking and
  third-party components carry risk of account warnings, breakage, or unexpected results; the project is provided
  "as is" with no warranty, and users bear the consequences (including version-incompatibility and third-party policy
  changes). Continuing to use it means the user accepts these terms.
* `## 高级版` commercial split and the two-edition download distinction are described in Section 8.3.

### 7.3 Practical summary

If you only run it locally on your own data: no redistribution occurs, and the README/disclaimer is the operative
document. If you fork it, publish a modified installer, or ship it in a product, you currently have **no license grant
for the project's own code**, you must satisfy every third-party notice in `THIRD_PARTY_NOTICES.md`, you must not imply
affiliation with WeChat/Tencent, and you would be bundling binaries produced under a **private PKI** that you do not
control. Consult a qualified lawyer for your specific jurisdiction and use case.

---

## 8. What is NOT in the public source (the key finding)

### 8.1 The native core is not buildable/committable from the public repo

* The public tree contains **no** `wechatdb_client.dll`, `wechatdb_broker.exe`, or `wechatdb_native_build.json`
  anywhere. `desktop/resources/backend/` ships only `.gitkeep` (line 24 of `upstream_blobs.txt`); `src/wechat_decrypt_tool/native/`
  ships `img_helper.dll`, `wce_integrity.pyd`, SDL2/VoipEngine/VC-runtime DLLs, macOS `libwx_key.dylib`, and
  `weflow_wasm/*` — but not the native-core trio. `pyproject.toml`'s wheel `include` list names those three files
  (plus the macOS equivalents), i.e. the packaging expects them, but they are produced elsewhere.
* `build-backend.cjs` **hard-requires** `WCE_NATIVE_CORE_ARTIFACT_DIR` on win32/darwin and refuses development
  manifests in CI. `tools/rebuild_wcdb_release.py` downloads the exact asset from the producer repo, and
  `docs/release-native-build.md` names the private producer repo **`2977094657/WCDB`** and its four production
  workflows, plus secret `WCE_NATIVE_CORE_PRODUCER_TOKEN`. The public repo has none of that source.
* The only public route to the artifacts is *reverse-extracting them from a public release installer* — which is
  exactly what dev mode does with the pinned `WeChatDataAnalysis-2.4.0-Setup.exe` (Section 6.3). That is a
  redistribution-relevant, license-ambiguous route, not a source build.
* macOS additionally needs a private `native/wce_integrity/Cargo.toml` (absent): `buildIntegrityNativeBinary()`
  throws "Private wce_integrity source is unavailable." On Windows the equivalent `wce_integrity.pyd` **is** prebuilt
  in the repo (6 970 212 B).

### 8.2 The signing identity is private

`desktop/resources/signing/` is `.gitkeep` only, yet `native-core-before-pack.cjs` requires a root certificate whose
SHA-256 equals the manifest pin, and electron-builder is configured with `forceCodeSigning: true` and a custom
`windows-private-pki-sign.cjs` hook with publisher `WDA Private PKI`. `release.yml` sources that material from the
protected environment `windows-private-pki-production`. **A publicly produced, signed Setup.exe is therefore not
possible without the maintainers' private keys.**

### 8.3 The "高级版" write/send/automation implementation is closed

* **README `## 高级版`** states verbatim: the regular edition focuses on read-only **decrypt/read/export/annual summary**;
  the advanced edition adds **"77 项写入、在线动作与自动化能力"** (77 write / online-action / automation capabilities)
  spanning message editing, message insertion, WeChat actions, Moments, groups, contacts, search, red packets and
  automation — and then: **"当前公开版本仅展示功能说明与演示动画，不包含高级功能的执行实现；实际使用需要获取高级版。"**
  ("The current public version only shows feature descriptions and demo animations; it does **not** include the
  execution implementation of the advanced features; actual use requires obtaining the advanced edition.")
  Acquisition is via QQ group 3 (`1109365501`).
* **`.github/release-editions.md`** states: the open-source and advanced editions are separately packaged and
  distributed; **"本仓库公开的是开源版源码；高级版为独立版本，其额外功能实现不属于本仓库的开源范围"** — this repo
  publishes the open-source edition source; the advanced edition is a separate build and its extra feature
  implementation is outside this repo's open-source scope; `advanced-`-prefixed installers are the advanced ones.
* **Release plumbing confirms it:** `release.yml` downloads assets named `advanced-*` from a separate
  `advanced-latest` release and republishes them in each version release (lines 139–152 and 732–745), and warns
  when no advanced asset is attached (line 759). The advanced binaries are built outside this repo.

### 8.4 Source-level confirmation that the public build is read-only

1. **API surface.** A scan of all 26 `src/wechat_decrypt_tool/routers/*.py` files found **83** non-GET routes. Every one
   is a local operation: decrypt (`POST /api/decrypt`, `/api/media/keys`, `/api/media/decrypt_all`), exports
   (`/api/chat/exports`, `/api/chat/contacts/export`, `/api/records/export`, `/api/sns/exports`, account archive),
   index/cache builds (`/api/chat/search-index/build`, `/api/chat/session-last-message/build`), realtime/local
   **sync/read** (`/api/chat/realtime/sync[_all]`, `/api/sns/realtime/*`), AI threads/settings, media/open-folder,
   local-search settings/models, MCP admin, voice-transcription jobs. **None** sends a WeChat message, edits a WeChat
   message row, accepts friend requests, posts Moments, or performs any other "写入/在线动作". The only deletions are
   local-project artifacts (export jobs, AI threads/rules, cached models, the imported account).
2. **Frontend is wired to a "feature unavailable" dialog.** `frontend/composables/chat/useChatEditing.js`:
   `onEditMessageClick()` → `openFeatureUnavailableDialog()`; the message is
   `FEATURE_UNAVAILABLE_MESSAGE` from `frontend/lib/developer-support.js`:
   *"当前版本仅展示该功能入口，暂时无法执行。如需使用高级版，请进 QQ 3 群 1109365501 私聊咨询群主获取。"*
   The `edit.gif`/advanced demo GIFs are static assets under `frontend/public/` and `website/assets/img/`, and the
   demo animations are JS scenes in `website/assets/js/pro-demos/scenes/{action,add-a,add-b,automation,contact,edit,group,moments}.js`.
3. **MCP is explicitly read-only-ish and routes writes away.** `skills/wechat-mcp-copilot/references/routing.md`:
   *"Backend health, logs, MCP LAN access, port, system settings, key/decrypt/import/data preparation, index/cache
   build, export, realtime sync, **local editing**, or data deletion requests: explain that these operations are not
   exposed through MCP and should be handled in the desktop/web app."*
4. **The shipped native manifest is a read-only build.** The task referenced
   `resources/backend/native/wechatdb_native_build.json`; note this path exists only in the **installed app**, not in
   the public repo (repo: `desktop/resources/backend/.gitkeep`). The installed
   `<install-dir>\WeChatDataAnalysis\resources\backend\native\wechatdb_native_build.json` (36 lines) declares:

   | Field | Value | Implication |
   | --- | --- | --- |
   | `distributionMode` | `"public"` | shared public distribution, not a recipient-bound capsule |
   | `readOnlyBuild` | `true` | the native core performs **no database writes** |
   | `databaseWriteBuild` | `false` | same, explicit |
   | `wechatActions` | `[]` | **the native core advertises zero WeChat actions** (no send/edit/insert) |
   | `offlineBootstrapFeatureBits` / `offlineExportSealFormat` | `3` / `"WES2"` | offline bootstrap + export sealing |
   | `testHooksEnabled` | `false` | production hardening on |
   | `codeSignatureEnforced` / `rootPublicKeyCompiled` | `true` / `true` | Authenticode + compiled root key |
   | `windowsSignerTrustMode` | `"private-pki"` | signed by WDA's private PKI |
   | `windowsPrivatePkiLeafRevocation` | `"build-and-lease-only"` | leaf certs are build/lease-scoped |
   | `nativeAsrAuthorization` | `"database-read"` | ASR permitted read-only |
   | `sourceRuntime` | `true` | this is a source-runtime build |

   These are not just descriptive: `build-backend.cjs::nativeCoreManifestErrors()` **fails the build** unless
   `readOnlyBuild === true` and `wechatActions` is an empty array (schema 2), and `nativeCoreProductionManifestErrors()`
   rejects `developmentBuild !== false`. A write-capable manifest cannot pass the public packaging path.

5. **License/lease gating exists in the public code.** `src/wechat_decrypt_tool/native_core_lease.py` and
   `native_core_device_credential.py` implement lease/device-credential flows, and
   `tests/test_native_core_license_policy.py` (59 KB) exercises `/v1/leases` + `/v1/challenges`, `distributionMode`
   `public` vs `controlled` (a `controlled` manifest requires a client-bound `distributionCapsule` and is rejected
   otherwise), and asserts that a production public policy needs no user configuration. Whether the shipped public
   build actually contacts a remote lease service at runtime cannot be confirmed from repo source alone (the compiled
   service URL lives in the native core) — **UNVERIFIED**.

### 8.5 Other private/absent inputs observed

* `desktop/src/wcdb-sidecar.cjs` is excluded by `build.files` but absent from the repo.
* `native/wce_integrity/Cargo.toml` (and Rust source) absent; `tools/build_wce_integrity.ps1` exists but the source it
  builds does not.
* macOS packaging depends on `.github/workflows/macos-private-build.yml` (21 901 B) and
  `src/wechat_decrypt_tool/native/macos/universal/libwx_key.dylib` etc.; not analysed here — **UNVERIFIED**.
* The release workflow relies on repo variables/secrets (`WCE_NATIVE_CORE_*`, `WCE_WINDOWS_*`, `WCE_MACOS_*`,
  `WCE_NATIVE_CORE_PRODUCER_TOKEN`) and protected environments that a fork will not have.

---

## 9. Consolidated UNVERIFIED items

1. `desktop/scripts/after-sign.cjs` and `installer-custom.nsh` contents (not fetched).
2. Exact minimum Node.js version (no `engines` field; inferred only).
3. Exact `.python-version` content (file exists, 5 bytes; not read) and whether 3.12/3.13 work.
4. Whether `build-icon.cjs`'s `require("pngjs")` resolves only via npm hoisting in a clean `npm ci`.
5. Whether an unsigned local installer is achievable by overriding `forceCodeSigning`/`signtoolOptions` (not documented).
6. Whether the shipped public build contacts a remote license/lease endpoint at runtime (service URL is inside the
   native core, not in repo source).
7. Whether upstream refreshes the Windows source-runtime pin before its 2026-10-22T14:44:48Z expiry.
8. macOS-only scripts (`macos-*`, `sign-macos.cjs`, `verify-macos-*`) and the macOS release workflow internals.
9. Disk-space/size estimates and build wall-clock times (no build was executed).
10. Precise line numbers for files I quoted from fetched text (README, `desktop/package.json`, `build-backend.cjs`,
    `useChatEditing.js`, `developer-support.js`); those quotes are symbol/section-accurate, not line-anchored.

## 10. Primary sources

* Repo/API: `https://api.github.com/repos/LifeArchiveProject/WeChatDataAnalysis` (license `null`, default branch `main`,
  `pushed_at 2026-10-11T06:06:53Z`), tree via `…/git/trees/main?recursive=1`.
* Raw files at tag `v2.9.0`: `README.md`, `THIRD_PARTY_NOTICES.md`, `pyproject.toml`, `main.py`,
  `desktop/package.json`, `desktop/resources/native-core-source-windows.json`,
  `desktop/resources/native-core-source-macos.json`, `desktop/scripts/{dev,build-backend,copy-ui,ai-packaging,
  after-pack,integrity-native-packaging,native-core-before-pack,build-icon}.cjs`, `desktop/src/{source-native-core-bootstrap,
  windows-source-native-core-bootstrap,renderer-startup}.cjs`, `frontend/nuxt.config.ts`, `frontend/package.json`,
  `frontend/composables/chat/useChatEditing.js`, `frontend/lib/developer-support.js`,
  `.github/release-editions.md`, `.github/workflows/release.yml`, `docs/release-native-build.md`,
  `skills/wechat-mcp-copilot/SKILL.md`, `skills/wechat-mcp-copilot/references/routing.md`,
  `tests/test_native_core_license_policy.py`, plus an in-memory endpoint scan of all
  `src/wechat_decrypt_tool/routers/*.py`.
* Local installed build: `<install-dir>\WeChatDataAnalysis\resources\{backend,ui,signing,app.asar}` and the
  extracted asar at `<repo>\app-asar\`.
