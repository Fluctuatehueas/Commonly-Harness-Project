# Vue3 + Vite 项目 Electron 桌面化实施方案

> 目标项目：`F:\Python\HarnessEngineering\HarnessDesktop`
> 技术栈：Vue 3 + Vite，前端通过 HTTP 调用一个 Python 后端 API
> 目标平台：Windows、Linux
> 方案定位：**业务代码零改动**，只增加外壳层与构建配置

---

## 0. 先明确一件事：这个项目的"原封不动"能到什么程度

你的项目是**标准 SPA + 远程 HTTP 后端**，没有任何 Node 原生依赖的迹象（这类项目里前端通常不会 import `fs`/`sqlite3`）。这个形态是 Electron 化最理想的情况：

- 页面源码、组件、路由表、Pinia store、API 调用层 —— **一行都不用改**
- 需要动的只有三类：**资源基址**、**路由模式**、**加载方式（协议）**
- 需要"新增"的只有：一个主进程文件、一个 preload 文件、一个 electron-builder 配置

只要第 3 节的三处改造做对，白屏、刷新 404、跨域失败这三个高频坑都不会出现。

---

## 1. 目标架构

```
┌──────────────────────── Electron 主进程 (Node) ────────────────────────┐
│  main.cjs                                                             │
│   · 单实例锁 · 创建 BrowserWindow · 注册 app:// 自定义协议              │
│   · 托盘/菜单 · 自动更新 · 生命周期                                    │
│   · 可选：拉起并守护 Python 后端子进程                                  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ preload.cjs（contextBridge 白名单）
┌───────────────────────────────┴───────────────────────────────────────┐
│                    渲染进程（你的 Vue 3 应用，原样运行）                 │
│   app://index.html  ← dist/ 静态产物                                   │
│        │                                                              │
│        └── fetch/axios ──► http://127.0.0.1:端口/api/...  (Python 后端) │
└───────────────────────────────────────────────────────────────────────┘
```

关键决策：**不用 `file://`，改用自定义协议 `app://`**。理由：

| | `file://` | `app://`（本方案） |
|---|---|---|
| history 路由刷新 | 直接 404，需额外 hack | 天然支持，回退 index.html |
| Cookie / localStorage | 行为受限、易被清 | 正常，等同于安全上下文 |
| fetch 跨域 | Origin 为 `null`，后端难放行 | Origin 稳定为 `app://xxx`，可精确放行 |
| 打包后相对路径 | 容易解析错 | 由协议处理器统一映射 |

---

## 2. 目录结构（改造后）

```
HarnessDesktop/
├── package.json                 ← 加 main 字段 + electron 相关脚本与依赖
├── vite.config.ts               ← 改 base: './'（若用 app:// 其实 '/' 也可，见 3.1）
├── index.html
├── src/                         ← 完全不动
│   ├── main.ts
│   ├── App.vue
│   ├── router/index.ts          ← 视情况确认 history 模式（见 3.2）
│   └── api/                     ← 确认 baseURL 来源（见 3.3）
├── electron/                    ← 新增
│   ├── main.cjs                 主进程入口
│   ├── preload.cjs              渲染进程桥接
│   └── protocol.cjs             app:// 协议注册与 SPA 回退
├── build/                       ← 新增：图标等打包资源
│   ├── icon.ico                 Windows ≥256×256
│   └── icons/512x512.png        Linux
├── electron-builder.yml         ← 新增：打包配置
└── dist/                        ← vite build 产物，作为 app:// 的资源根
```

---

## 3. 三处必要的前端侧改造（其余业务代码不动）

### 3.1 资源基址

`vite.config.ts` 里 `base` 的取值决定了产物中 `<script src>`、`<link href>`、CSS 里 `url()` 的写法。

```ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // 用 app:// 自定义协议时，'/' 也能正确解析到协议根；
  // 但设成 './' 兼容性最好：以后若要临时用 file:// 直接双击 index.html 调试也能活。
  base: './',
  build: {
    outDir: 'dist',
    // 关闭 build 时的 sourcemap 可显著减小安装包；排障期可临时开启
    sourcemap: false
  },
  server: {
    // 开发期仍然跑 Vite Dev Server，Electron 用 loadURL 连它，热更新照旧可用
    port: 5173,
    strictPort: true
  }
})
```

> 注意：`base: './'` 与 `history` 路由配合时，如果**直接**用 `file://` 打开会出现二级路由 404。本方案用 `app://` + 回退解决了这一点，所以两者不冲突。

### 3.2 路由模式

如果 `src/router/index.ts` 用的是：

```ts
history: createWebHistory(import.meta.env.BASE_URL)
```

**保持不动即可** —— `app://` 协议处理器会对任何未命中静态文件的路径回退到 `index.html`，行为与 Nginx `try_files` 一致。

如果用的是 `createWebHashHistory()`，也不用改，`app://index.html#/xxx` 正常工作。

**唯一要检查的**：路由里有没有 `scrollBehavior` 依赖真实滚动容器、或有没有用 `window.location.href = ...` 做跳转。后者在桌面端应改为 `router.push()`，否则协议处理会把它当成重新加载。

### 3.3 后端 API 地址（这是本项目最需要注意的一点）

前端通过 HTTP 调 Python 后端，桌面端页面不再来自 `http://localhost:5173`，于是：

1. **baseURL 不能是相对路径 `/api`**（会解析成 `app://api`）
2. **后端必须放行 Origin**，否则浏览器内核按 CORS 规则拦截

推荐做法 —— 让 baseURL 可配置，并区分开发/生产：

```ts
// src/api/http.ts（唯一允许改动的业务相关文件，改动极小）
import axios from 'axios'

// 开发期走 Vite 代理（vite.config 的 server.proxy），生产走本地/远端后端
const baseURL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? '/api' : 'http://127.0.0.1:8000/api')

export const http = axios.create({ baseURL, timeout: 30_000 })
```

```ts
// vite.config.ts 补充开发期代理，避免开发时跨域
server: {
  port: 5173,
  strictPort: true,
  proxy: {
    '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true }
  }
}
```

后端侧（FastAPI / Flask，二选一写法）：

```python
# FastAPI：放行桌面端 Origin
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://*", "http://localhost:5173"],  # 生产可收紧为具体 origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

```python
# Flask
from flask_cors import CORS
CORS(app, resources={r"/api/*": {"origins": ["app://*", "http://localhost:5173"]}},
     supports_credentials=True)
```

> 若后端改为由 Electron **自己拉起**（见 6.2），端口固定为 `127.0.0.1:8000`，上面配置一并生效。

---

## 4. 主进程实现（可直接使用）

### 4.1 `electron/protocol.cjs` —— 自定义协议 + SPA 回退

```js
// 所在进程：主进程（Node 环境）
// 职责：把 app://<host>/<path> 映射到磁盘上的 dist 目录；
//      未命中真实文件（如 /dashboard/123）时回退 index.html，
//      让 Vue Router 的 history 模式在桌面端与浏览器行为一致。
// 调用方：main.cjs 在 app.whenReady() 之前调用 registerSchemes()，
//        在 ready 之后调用 registerHandler()。
const path = require('node:path')
const fs = require('node:fs')
const { protocol, net } = require('electron')
const { pathToFileURL } = require('node:url')

// 必须在 ready 之前声明，否则 fetch/XHR 在该协议下会被限制
function registerSchemes () {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: 'app',
      privileges: {
        standard: true,      // 启用 URL 标准解析（相对路径、Origin 需要）
        secure: true,        // 视为安全上下文，localStorage/ServiceWorker 可用
        supportFetchAPI: true,
        corsEnabled: true,
        stream: true
      }
    }
  ])
}

// 单线程事件循环内同步查找文件即可，dist 目录规模有限，无性能问题
function registerHandler (distDir) {
  protocol.handle('app', (request) => {
    const { pathname } = new URL(request.url)

    // 归一化，阻断 ../ 穿越到 dist 之外
    const safePath = path.normalize(decodeURIComponent(pathname)).replace(/^(\.\.[/\\])+/, '')
    let filePath = path.join(distDir, safePath)

    // 目录或未命中 → 回退 index.html（SPA fallback）
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(distDir, 'index.html')
    }

    // net.fetch 支持 file://，且能正确处理 Range 请求（视频/大文件需要）
    return net.fetch(pathToFileURL(filePath).toString())
  })
}

module.exports = { registerSchemes, registerHandler }
```

### 4.2 `electron/preload.cjs` —— 最小能力桥

```js
// 所在进程：渲染进程的 preload 隔离世界（在页面脚本执行之前运行）
// 职责：只暴露白名单能力给 Vue 代码，不暴露 ipcRenderer 本体，
//      避免 XSS 时被任意调用主进程通道。
// 调用方：main.cjs 中通过 webPreferences.preload 指定；
//        Vue 侧通过 window.desktop.xxx 使用（需自行声明 d.ts 类型）。
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('desktop', {
  // 平台信息：前端做快捷键/路径分隔符差异时可用
  platform: process.platform,
  // 应用版本：用于"关于"页与更新检查
  getVersion: () => ipcRenderer.invoke('app:get-version'),
  // 用系统默认浏览器打开外链（必须由主进程发起）
  openExternal: (url) => ipcRenderer.invoke('shell:open-external', url),
  // 选择本地文件（未来若要"导入文件"功能可复用）
  pickFile: (options) => ipcRenderer.invoke('dialog:pick-file', options)
})
```

### 4.3 `electron/main.cjs` —— 主进程入口

```js
// 所在进程：主进程（Node 环境，单线程事件循环）
// 职责：应用生命周期、窗口管理、协议注册、IPC 处理器、外链与导航拦截。
// 与渲染进程的关系：完全隔离；所有通信经由 ipcMain / preload 的 contextBridge。
const { app, BrowserWindow, ipcMain, shell, dialog, Menu } = require('electron')
const path = require('node:path')
const { registerSchemes, registerHandler } = require('./protocol.cjs')

const isDev = !app.isPackaged                 // 打包后为 false
const DEV_URL = 'http://localhost:5173'       // 与 vite.config 的 server.port 保持一致
const DIST_DIR = path.join(__dirname, '..', 'dist')

// 必须最早执行：协议特权声明要早于 app ready
registerSchemes()

// 单实例锁：避免用户重复双击产生两个实例，共享同一个 userData 目录导致
// localStorage / IndexedDB 写入竞争。第二个实例会直接退出并聚焦已有窗口。
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  let mainWindow = null

  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  function createWindow () {
    mainWindow = new BrowserWindow({
      width: 1440,
      height: 900,
      minWidth: 1024,
      minHeight: 680,
      show: false,                            // 首帧就绪再显示，消除白屏闪烁
      backgroundColor: '#ffffff',
      icon: path.join(__dirname, '..', 'build', 'icon.ico'),
      webPreferences: {
        preload: path.join(__dirname, 'preload.cjs'),
        contextIsolation: true,               // 必开：渲染进程与 preload 隔离
        nodeIntegration: false,               // 必关：Vue 代码里不能 require
        sandbox: false,                       // preload 用到 require，需关闭 sandbox
        webSecurity: true,                    // 生产环境务必保持 true
        spellcheck: false
      }
    })

    mainWindow.once('ready-to-show', () => mainWindow.show())

    // 开发：连 Vite Dev Server，享受 HMR；生产：走 app:// 协议
    if (isDev) {
      mainWindow.loadURL(DEV_URL)
      mainWindow.webContents.openDevTools({ mode: 'detach' })
    } else {
      mainWindow.loadURL('app://bundle/index.html')
    }

    // 拦截 window.open / target=_blank：交给系统浏览器，不在应用内开裸窗口
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:/.test(url)) shell.openExternal(url)
      return { action: 'deny' }
    })

    // 拦截站内导航逃逸：非 app:// 且非开发地址的顶级跳转一律外抛，
    // 防止误点外链把整个应用窗口替换成网页
    mainWindow.webContents.on('will-navigate', (event, url) => {
      const allowed = isDev ? url.startsWith(DEV_URL) : url.startsWith('app://')
      if (!allowed) {
        event.preventDefault()
        shell.openExternal(url)
      }
    })

    mainWindow.on('closed', () => { mainWindow = null })
  }

  // ---------- IPC 处理器 ----------
  // 与 preload 中的 invoke 一一对应，全部返回 Promise
  ipcMain.handle('app:get-version', () => app.getVersion())
  ipcMain.handle('shell:open-external', (_e, url) => {
    // 只放行 http/https，避免被构造成 file:// 或自定义协议执行
    if (/^https?:\/\//i.test(url)) return shell.openExternal(url)
    return false
  })
  ipcMain.handle('dialog:pick-file', async (_e, options = {}) => {
    const { canceled, filePaths } = await dialog.showOpenDialog({
      properties: ['openFile'],
      ...options
    })
    return canceled ? null : filePaths[0]
  })

  app.whenReady().then(() => {
    registerHandler(DIST_DIR)
    Menu.setApplicationMenu(null)             // 生产环境隐藏默认菜单栏；需要时保留
    createWindow()

    // macOS 习惯：点 Dock 图标时重建窗口（Windows/Linux 不触发）
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })
}
```

---

## 5. 构建与打包

### 5.1 `package.json` 改动

```json
{
  "name": "harness-desktop",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "main": "electron/main.cjs",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",

    "electron:dev": "concurrently -k \"vite\" \"wait-on http://localhost:5173 && cross-env NODE_ENV=development electron .\"",
    "electron:start": "npm run build && electron .",
    "dist:win": "npm run build && electron-builder --win --x64",
    "dist:linux": "npm run build && electron-builder --linux --x64",
    "dist:all": "npm run build && electron-builder --win --linux"
  },
  "devDependencies": {
    "vite": "^5.4.0",
    "@vitejs/plugin-vue": "^5.1.0",
    "electron": "^31.7.0",
    "electron-builder": "^24.13.3",
    "concurrently": "^9.0.0",
    "wait-on": "^8.0.0",
    "cross-env": "^7.0.3"
  }
}
```

> `"type": "module"` 与 `.cjs` 后缀的搭配是刻意的：前端源码走 ESM，主进程走 CommonJS，互不干扰。Electron 对 ESM 主进程的支持仍在演进，用 `.cjs` 最稳。

### 5.2 `electron-builder.yml`

```yaml
appId: com.harness.desktop
productName: HarnessDesktop
copyright: Copyright © 2025

directories:
  output: release
  buildResources: build

# 只打包必要内容：源码不进包，避免体积膨胀与源码泄露
files:
  - dist/**/*
  - electron/**/*
  - package.json
  - "!**/*.map"
  - "!**/node_modules/**/{test,__tests__,tests,example,examples}/**"

asar: true
compression: maximum

win:
  target:
    - target: nsis
      arch: [x64]
  icon: build/icon.ico

nsis:
  oneClick: false                 # 走安装向导，允许用户选目录
  perMachine: false               # 默认按当前用户安装，免管理员权限
  allowToChangeInstallationDirectory: true
  createDesktopShortcut: true
  createStartMenuShortcut: true
  shortcutName: HarnessDesktop

linux:
  target:
    - AppImage                    # 免安装，最通用
    - deb
  icon: build/icons
  category: Utility
  maintainer: you@example.com
  desktop:
    StartupWMClass: HarnessDesktop
```

### 5.3 打包命令的实操要点

| 目标 | 在哪台机器打 | 备注 |
|---|---|---|
| Windows (nsis) | Windows | 跨平台打 Windows 包需要 wine，不推荐 |
| Linux (AppImage/deb) | Linux 或 WSL | AppImage 需 `chmod +x` 后运行 |
| 签名 | — | Windows 建议后续加代码签名证书，否则 SmartScreen 会告警 |

---

## 6. Python 后端的两种集成方式（本项目的核心决策点）

桌面端调后端，用户机器上得有后端在跑。选哪种，直接决定交付难度：

### 6.1 方式 A：远程后端（最简单，推荐先做）

Electron 只做壳，API 指向公司服务器（`https://api.example.com/api`）。

- 改动量：只改 `VITE_API_BASE_URL`，加后端 CORS 放行
- 优点：无进程管理、无 Python 运行时打包、可独立升级
- 缺点：**必须联网**；内网/离线场景不可用

### 6.2 方式 B：本地捆绑后端（离线可用，交付复杂度高）

Electron 启动时拉起 Python 后端，退出时优雅关闭。

**第 1 步：把 Python 后端打成单文件可执行程序**

```bash
# 在 Python 环境里，进到后端项目根目录
pyinstaller --noconfirm --onefile --name backend \
  --add-data "app/templates:templates" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  entry.py
```

把产物 `backend.exe`（Windows）/ `backend`（Linux）放进 `electron/resources/`，并在 builder 里声明为额外资源：

```yaml
extraResources:
  - from: electron/resources/backend.exe
    to: backend.exe
  - from: electron/resources/backend
    to: backend
```

**第 2 步：主进程管理子进程生命周期**

```js
// electron/backend.cjs
// 所在进程：主进程
// 职责：启动/守护/关闭 Python 后端进程；退出时确保不留孤儿进程。
// 线程关系：Node 的 child_process 不阻塞主进程事件循环，
//          stdout/stderr 通过流式事件回调送回主进程。
const { spawn } = require('node:child_process')
const path = require('node:path')
const http = require('node:http')
const { app } = require('electron')

let child = null

function backendPath () {
  // 打包后在 resources 下；开发期指向本地构建产物或直接用 python 解释器
  const name = process.platform === 'win32' ? 'backend.exe' : 'backend'
  return app.isPackaged
    ? path.join(process.resourcesPath, name)
    : path.join(__dirname, 'resources', name)
}

// 轮询健康检查，比固定 sleep 可靠：避免机器慢时前端先请求到未就绪的后端
function waitForHealth (url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const tick = () => {
      http.get(url, (res) => {
        res.resume()
        res.statusCode < 500 ? resolve(true) : retry()
      }).on('error', retry)
    }
    const retry = () => {
      if (Date.now() > deadline) return reject(new Error('backend start timeout'))
      setTimeout(tick, 300)          // 300ms 重试间隔
    }
    tick()
  })
}

async function start () {
  if (child) return
  child = spawn(backendPath(), ['--host', '127.0.0.1', '--port', '8000'], {
    windowsHide: true,               // 关键：Windows 上不弹黑框
    stdio: ['ignore', 'pipe', 'pipe']
  })

  // 后端日志转发到主进程日志，便于排障
  child.stdout.on('data', (d) => console.log('[backend]', d.toString().trim()))
  child.stderr.on('data', (d) => console.error('[backend]', d.toString().trim()))

  child.on('exit', (code) => {
    console.warn('[backend] exited with code', code)
    child = null
    // 生产环境可在此加一次自动重启（限次数），或提示用户
  })

  await waitForHealth('http://127.0.0.1:8000/health')
}

function stop () {
  if (!child) return
  // Windows 无 SIGTERM 语义，需用 taskkill 杀进程树，否则 uvicorn 的
  // reload/worker 子进程会残留
  if (process.platform === 'win32') {
    spawn('taskkill', ['/pid', String(child.pid), '/f', '/t'])
  } else {
    child.kill('SIGTERM')
    // 3 秒未退出则强杀，避免关不掉导致卸载失败
    setTimeout(() => child && child.kill('SIGKILL'), 3000)
  }
  child = null
}

module.exports = { start, stop }
```

然后在 `main.cjs` 中接入：

```js
// 在 app.whenReady() 里 registerHandler 之后：
await require('./backend.cjs').start()

// 生命周期收尾：一定要挂在 before-quit 和 will-quit，覆盖所有退出路径
app.on('before-quit', () => require('./backend.cjs').stop())
app.on('will-quit', () => require('./backend.cjs').stop())
```

**方式 B 必须提前预判的四个坑：**

1. **端口冲突**：8000 被占就起不来。改为让后端监听 `0`（随机端口），启动后把真实端口写进 stdout，主进程解析后通过 `ipcMain` 告诉渲染进程，前端动态设置 baseURL。
2. **杀毒软件误杀**：PyInstaller 单文件产物常被 Windows Defender 判为可疑。做代码签名，或改用 `--onedir` 模式（体积大但误杀率低）。
3. **首启动延迟**：PyInstaller onefile 每次启动都要解压到临时目录，冷启动可能 3–8 秒。加一个启动闪屏（`BrowserWindow` 加载本地 splash.html），等 health 通过再切主窗口。
4. **孤儿进程**：用户强制关机/崩溃时 `before-quit` 不触发。后端要自己实现心跳，父进程消失后自行退出。

**建议**：先用方式 A 把壳跑通、验证所有页面在 Electron 里表现正常（这一步能暴露 90% 的兼容问题），再决定是否投入方式 B。

---

## 7. 分阶段实施计划

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| **P0 勘察** | 读 `package.json`、`vite.config`、路由文件、API 层，确认 history/hash 模式与 baseURL 写法 | 一份改造清单（实际只有 2–3 处） | 0.5 天 |
| **P1 壳跑通** | 加 `electron/` 三个文件 + `base: './'`，`npm run electron:start` 能打开首页 | 本地可运行的桌面窗口 | 1 天 |
| **P2 三大坑修复** | 验证：刷新任意子路由不 404、所有 API 请求通、静态资源（图片/字体/图标）全部正常 | 无白屏、无 404、无 CORS 报错 | 1 天 |
| **P3 体验打磨** | 窗口尺寸/最小尺寸、`ready-to-show` 防白屏、外链外抛、菜单、图标、开发者工具仅在 dev 开启 | 可交付的内部版本 | 1 天 |
| **P4 打包分发** | `electron-builder.yml`，出 Windows nsis + Linux AppImage，两台机器实机安装测试 | 安装包 | 1–2 天 |
| **P5 后端集成** | 选 A 或 B；若选 B，做 PyInstaller 打包 + 子进程守护 + 启动闪屏 | 离线可用安装包 | 2–4 天 |
| **P6 生产化** | 代码签名、自动更新（`electron-updater`）、崩溃日志（`crashReporter`）、错误上报 | 正式发布版 | 2–3 天 |

**关键里程碑**：P2 结束就是"技术上成立"的分界线。如果 P2 全绿，后面全是工程量，没有不可控风险。

---

## 8. 验收清单（逐条实测，不要跳过）

**功能**
- [ ] 首页正常渲染，无白屏、无控制台红色报错
- [ ] 每个路由**手动刷新**（Ctrl+R）后仍正常，不 404
- [ ] 浏览器前进/后退按钮行为正确
- [ ] 所有接口请求成功，Network 面板无 CORS 错误
- [ ] 登录态/token 刷新后仍保持（Cookie 或 localStorage 持久化正常）
- [ ] 图片、字体、iconfont、SVG sprite 全部正常显示（这类最容易因路径失效而缺失）
- [ ] 文件上传/下载功能正常（下载默认落系统下载目录）
- [ ] 拖拽本地文件到窗口的行为符合预期

**稳定性**
- [ ] 断网启动，应用不崩溃（远程后端方案下应给出友好提示而非白屏）
- [ ] 多次快速开关窗口，无进程残留
- [ ] 重复双击图标，只起一个实例
- [ ] 长时间运行（≥2 小时）内存无持续增长

**打包产物**
- [ ] 在**干净的** Win10/11 机器上安装并启动（无 Node、无 Python 环境）
- [ ] 在干净 Ubuntu 22.04 上运行 AppImage
- [ ] 安装路径含中文/空格时正常
- [ ] 卸载干净，不留 userData 残留（或按需保留）
- [ ] 安装包体积符合预期（纯壳约 80–120 MB，含 Python 后端再加 30–60 MB）

**安全**
- [ ] `nodeIntegration: false`、`contextIsolation: true`、`sandbox` 按需
- [ ] 生产构建禁用 DevTools（或用快捷键隐藏入口）
- [ ] `will-navigate` 与 `setWindowOpenHandler` 拦截生效
- [ ] CSP 头在 `app://` 响应中设置（可在 `protocol.handle` 返回的 Response 上加 `Content-Security-Policy`）

---

## 9. 常见问题速查

| 现象 | 根因 | 修复 |
|---|---|---|
| 打包后白屏 | `base` 为 `/`，产物引用 `/assets/...` 在 `app://` 下解析失败 | `base: './'` 后重新 `vite build` |
| 刷新子路由 404 | 无 SPA 回退 | 用本方案 `protocol.handle` 的回退逻辑 |
| 所有 API 报 CORS | 后端未放行 `app://` Origin | 后端加 `allow_origins=["app://*"]` |
| API 请求地址变成 `app://api/...` | baseURL 写成相对路径 `/api` | 改为绝对地址或 `import.meta.env` 注入 |
| 图标字体显示成方块 | CSS 里 `url()` 路径被重写后未命中 | 检查 `base`，确认字体文件在 `dist/assets` 内 |
| `require is not defined` | `nodeIntegration` 关闭后前端仍在 `require` | 通过 preload 暴露能力，或在 Vite 里换成浏览器可用库 |
| `localStorage` 数据丢失 | 换了 Electron 版本 / `userData` 目录变化 | 固定 `app.setPath('userData', ...)`，或做首次迁移 |
| Linux 上中文显示为豆腐块 | 系统缺中文字体 | 打包时内置思源黑体等字体，或 DPI/字体回退配置 |
| Windows 安装包被 SmartScreen 拦 | 未签名 | 购买代码签名证书，或用 EV 证书积累信誉 |

---

## 10. 第一天的具体动作（可直接照做）

```bash
# 1. 进项目根目录
cd /d F:\Python\HarnessEngineering\HarnessDesktop

# 2. 装依赖（Electron 二进制较大，国内建议先设镜像）
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm i -D electron@^31 electron-builder@^24 concurrently wait-on cross-env

# 3. 按本文创建三个文件：electron/main.cjs、electron/preload.cjs、electron/protocol.cjs
# 4. 改 vite.config.ts 的 base 与 proxy
# 5. 改 package.json 的 main 字段与 scripts
# 6. 先只验证生产模式（避免 dev 模式的变量混淆）
npm run electron:start
```

窗口正常打开首页 → P1 完成。此时在窗口里按 Ctrl+R 刷新首页、点进任意子页面再刷新 → 若都正常，P2 的一大半也就过了。

---

## 附录：需要你确认的两个细节

这两点决定方案里哪些部分要微调，但**不影响整体架构**：

1. **路由模式**：`src/router/index.ts` 里是 `createWebHistory` 还是 `createWebHashHistory`？
   - history → 本方案的 `app://` + 回退刚好完全对齐，无需改动
   - hash → 也能跑，但可顺便简化协议处理器
2. **后端归属**：Python 后端是"公司服务器上的远程服务"，还是"需要在用户电脑本地跑"？
   - 远程 → 走 6.1，1 天可交付
   - 本地 → 走 6.2，预留 2–4 天

把这两个答案告诉我，或者直接把 `package.json` + `vite.config.ts` + `src/router/index.ts` 三个文件贴给我，我可以把配置改成适配你项目的最终版本。
