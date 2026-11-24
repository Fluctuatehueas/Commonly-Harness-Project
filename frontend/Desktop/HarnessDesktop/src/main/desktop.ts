import {
  app,
  BrowserWindow,
  ipcMain,
  nativeImage,
  screen,
  Tray,
  type Rectangle,
} from 'electron'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { is } from '@electron-toolkit/utils'

/** 当前正在工作的会话快照：由各渲染窗口上报，托盘弹窗汇总展示。 */
export interface RunningSessionInfo {
  threadId: string
  title: string
  /** 是否正在等待人工介入。 */
  waiting?: boolean
}

const POPUP_WIDTH = 300
const POPUP_MIN_HEIGHT = 150
const POPUP_MAX_HEIGHT = 560
/** 图标窗口略大于图标，给呼吸灯与跃迁动画留出完整绘制空间。 */
const ORB_WINDOW_SIZE = 210
const ORB_ICON_SIZE = 96
/** 贴边时悬停/点击后图标向屏幕内滑出的距离。 */
const EDGE_REVEAL = 96
const SNAP_DISTANCE = 56
const PANEL_WIDTH = 420
const PANEL_HEIGHT = 640
const EDGE_MARGIN = 12

type AssistantSide = 'left' | 'right' | 'none'
type AssistantMode = 'orb' | 'panel'

interface AssistantWindowState {
  orbSide: AssistantSide
  orbCenter: { x: number; y: number }
  panelBounds: Rectangle | null
  lastMode: AssistantMode
}

let tray: Tray | null = null
let popup: BrowserWindow | null = null
let orbWindow: BrowserWindow | null = null
let panelWindow: BrowserWindow | null = null
let popupHeight = 360
let assistantVisible = false
let quitting = false
let getMainWindow: () => BrowserWindow | null = () => null

// 图标窗口位置：orbCenter 是图标中心（屏幕坐标），窗口以它为中心展开。
let orbSide: AssistantSide = 'right'
let orbCenter = { x: 0, y: 0 }
// 面板 bounds 与最后的展示形态，用于收起/退出后恢复。
let panelBoundsState: Rectangle | null = null
let lastMode: AssistantMode = 'orb'
let stateSaveTimer: NodeJS.Timeout | null = null
let dragTimer: NodeJS.Timeout | null = null
let dragOffset = { x: 0, y: 0 }

/** 每个渲染进程上报的运行中会话；key 为 webContents.id。 */
const runningReports = new Map<number, RunningSessionInfo[]>()
const watchedWebContents = new Set<number>()

export function isQuitting(): boolean {
  return quitting
}

export function markQuitting(): void {
  quitting = true
}

/** 主窗口的关闭行为：拦截为隐藏，立即从任务栏移除，托盘仍在。 */
export function attachMainWindowBehavior(win: BrowserWindow): void {
  win.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    win.setSkipTaskbar(true)
    win.hide()
  })
  win.on('show', () => {
    win.setSkipTaskbar(false)
  })
}

/** 挂载托盘、托盘弹窗与悬浮助手：应用 ready 后调用一次。 */
export function setupDesktop(resolveMainWindow: () => BrowserWindow | null): void {
  getMainWindow = resolveMainWindow
  loadAssistantState()
  createTray()
  registerDesktopIpc()
}

function stateFilePath(): string {
  return join(app.getPath('userData'), 'assistant-window-state.json')
}

function pointOnSomeDisplay(point: { x: number; y: number }): boolean {
  return screen.getAllDisplays().some((display) => {
    const area = display.workArea
    return (
      point.x >= area.x - ORB_ICON_SIZE &&
      point.x <= area.x + area.width + ORB_ICON_SIZE &&
      point.y >= area.y - ORB_ICON_SIZE &&
      point.y <= area.y + area.height + ORB_ICON_SIZE
    )
  })
}

function boundsVisible(bounds: Rectangle): boolean {
  return screen.getAllDisplays().some((display) => {
    const area = display.workArea
    const width =
      Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x)
    const height =
      Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y)
    return width >= 160 && height >= 160
  })
}

/** 读取上次的图标位置、面板 bounds 与展示形态。 */
function loadAssistantState(): void {
  const area = screen.getPrimaryDisplay().workArea
  orbSide = 'right'
  orbCenter = {
    x: area.x + area.width,
    y: Math.round(area.y + area.height * 0.42),
  }
  panelBoundsState = null
  lastMode = 'orb'
  try {
    const raw = readFileSync(stateFilePath(), 'utf8')
    const data = JSON.parse(raw) as Partial<AssistantWindowState>
    if (data.orbSide === 'left' || data.orbSide === 'right' || data.orbSide === 'none') {
      orbSide = data.orbSide
    }
    if (
      data.orbCenter &&
      Number.isFinite(data.orbCenter.x) &&
      Number.isFinite(data.orbCenter.y) &&
      pointOnSomeDisplay(data.orbCenter)
    ) {
      orbCenter = { x: Math.round(data.orbCenter.x), y: Math.round(data.orbCenter.y) }
    }
    if (data.panelBounds && boundsVisible(data.panelBounds)) {
      panelBoundsState = {
        x: Math.round(data.panelBounds.x),
        y: Math.round(data.panelBounds.y),
        width: Math.round(data.panelBounds.width),
        height: Math.round(data.panelBounds.height),
      }
    }
    if (data.lastMode === 'panel' || data.lastMode === 'orb') {
      lastMode = data.lastMode
    }
  } catch {
    // 首次启动或状态文件损坏时使用默认位置
  }
}

function scheduleSaveState(): void {
  if (stateSaveTimer) clearTimeout(stateSaveTimer)
  stateSaveTimer = setTimeout(() => {
    stateSaveTimer = null
    try {
      const state: AssistantWindowState = {
        orbSide,
        orbCenter,
        panelBounds: panelBoundsState,
        lastMode,
      }
      writeFileSync(stateFilePath(), JSON.stringify(state))
    } catch {
      // 状态保存失败不影响运行
    }
  }, 250)
}

function trayIconPath(): string {
  const candidates = [
    process.env['HARNESS_TRAY_ICON'],
    join(process.resourcesPath, 'ICON.png'),
    resolve(app.getAppPath(), '../../HarnessControlPlatform/resources/ICON.png'),
    resolve(app.getAppPath(), '../HarnessControlPlatform/resources/ICON.png'),
    resolve(process.cwd(), 'frontend/HarnessControlPlatform/resources/ICON.png'),
    join(__dirname, '../../resources/icon.png'),
  ].filter((item): item is string => Boolean(item))
  return candidates.find((item) => existsSync(item)) || ''
}

function createTray(): void {
  if (tray) return
  const path = trayIconPath()
  if (!path) {
    console.warn('[desktop] 找不到托盘图标 ICON.png')
    return
  }
  const image = nativeImage.createFromPath(path)
  tray = new Tray(image.isEmpty() ? image : image.resize({ width: 20, height: 20 }))
  tray.setToolTip('CommonHarness')
  // 左键直接打开主窗体；右键弹出全息操作面板。
  tray.on('click', () => showMainWindow())
  tray.on('double-click', () => showMainWindow())
  tray.on('right-click', () => toggleTrayPopup())
}

function showMainWindow(): void {
  const win = getMainWindow()
  if (!win || win.isDestroyed()) return
  if (win.isMinimized()) win.restore()
  win.setSkipTaskbar(false)
  win.show()
  win.focus()
}

function loadPanelRenderer(win: BrowserWindow): void {
  win.webContents.on('did-fail-load', (_event, code, description, url) => {
    console.error(`[desktop] assistant 面板加载失败 (${code}) ${description} ${url}`)
  })
  const devUrl = process.env['ELECTRON_RENDERER_URL']
  if (is.dev && devUrl) {
    void win.loadURL(`${devUrl.replace(/\/$/, '')}/assistant`)
    return
  }
  void win.loadFile(join(__dirname, '../renderer/index.html'), { hash: '/assistant' })
}

function loadOrbRenderer(win: BrowserWindow): void {
  win.webContents.on('did-fail-load', (_event, code, description, url) => {
    console.error(`[desktop] 悬浮图标加载失败 (${code}) ${description} ${url}`)
  })
  const devUrl = process.env['ELECTRON_RENDERER_URL']
  if (is.dev && devUrl) {
    void win.loadURL(`${devUrl.replace(/\/$/, '')}/orb.html`)
    return
  }
  void win.loadFile(join(__dirname, '../renderer/orb.html'))
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

// ---------- 托盘弹窗 ----------

function ensurePopup(): BrowserWindow {
  if (popup && !popup.isDestroyed()) return popup
  popup = new BrowserWindow({
    width: POPUP_WIDTH,
    height: popupHeight,
    show: false,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
    },
  })
  popup.setAlwaysOnTop(true, 'pop-up-menu')
  popup.on('blur', () => {
    if (popup && !popup.webContents.isDevToolsOpened()) popup.hide()
  })
  popup.on('closed', () => {
    popup = null
  })
  const devUrl = process.env['ELECTRON_RENDERER_URL']
  if (is.dev && devUrl) {
    void popup.loadURL(`${devUrl.replace(/\/$/, '')}/tray-popup`)
  } else {
    void popup.loadFile(join(__dirname, '../renderer/index.html'), {
      hash: '/tray-popup',
    })
  }
  return popup
}

function toggleTrayPopup(): void {
  if (popup && popup.isVisible()) {
    popup.hide()
    return
  }
  const win = ensurePopup()
  positionPopup()
  win.show()
  win.focus()
  // 透明窗口首次显示时仍需重新抬升一次，否则可能落在主窗口后面。
  win.setAlwaysOnTop(true, 'pop-up-menu')
  win.moveTop()
  win.webContents.send('desktop:running-sessions', runningSnapshot())
  win.webContents.send('desktop:assistant-visible-changed', assistantVisible)
}

function positionPopup(): void {
  if (!popup || popup.isDestroyed()) return
  const cursor = screen.getCursorScreenPoint()
  const display = screen.getDisplayNearestPoint(cursor)
  const area = display.workArea
  const trayBounds = tray?.getBounds()
  const width = POPUP_WIDTH
  const height = popupHeight

  let x =
    trayBounds && trayBounds.width > 0
      ? trayBounds.x + trayBounds.width / 2 - width / 2
      : cursor.x - width / 2
  x = clamp(x, area.x + 8, area.x + area.width - width - 8)

  let y: number
  if (trayBounds && trayBounds.height > 0) {
    // 任务栏在屏幕上半部分时向下弹出，否则向上弹出。
    const above = trayBounds.y + trayBounds.height / 2 < area.y + area.height / 2
    y = above ? trayBounds.y + trayBounds.height + 8 : trayBounds.y - height - 8
  } else {
    y = cursor.y + 14
  }
  y = clamp(y, area.y + 8, area.y + area.height - height - 8)

  popup.setBounds({
    x: Math.round(x),
    y: Math.round(y),
    width,
    height,
  })
}

// ---------- 悬浮助手：图标窗口 + 面板窗口 ----------

function ensureOrbWindow(): BrowserWindow {
  if (orbWindow && !orbWindow.isDestroyed()) return orbWindow
  orbWindow = new BrowserWindow({
    width: ORB_WINDOW_SIZE,
    height: ORB_WINDOW_SIZE,
    show: false,
    frame: false,
    transparent: true,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
    },
  })
  orbWindow.setAlwaysOnTop(true, 'floating')
  orbWindow.setSkipTaskbar(true)
  orbWindow.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    hideOrbWindow()
  })
  orbWindow.on('closed', () => {
    orbWindow = null
    refreshAssistantVisible()
  })
  loadOrbRenderer(orbWindow)
  return orbWindow
}

function ensurePanelWindow(): BrowserWindow {
  if (panelWindow && !panelWindow.isDestroyed()) return panelWindow
  panelWindow = new BrowserWindow({
    width: PANEL_WIDTH,
    height: PANEL_HEIGHT,
    minWidth: 340,
    minHeight: 420,
    maxWidth: 760,
    maxHeight: 900,
    show: false,
    frame: false,
    // 面板使用不透明窗口：透明窗口在 Windows 上无法获得系统缩放光标与原生缩放。
    transparent: false,
    backgroundColor: '#04101e',
    resizable: true,
    thickFrame: true,
    roundedCorners: true,
    hasShadow: true,
    skipTaskbar: true,
    alwaysOnTop: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
    },
  })
  panelWindow.setAlwaysOnTop(true, 'floating')
  panelWindow.setSkipTaskbar(true)
  const persist = (): void => {
    if (!panelWindow || panelWindow.isDestroyed() || !panelWindow.isVisible()) return
    panelBoundsState = panelWindow.getBounds()
    lastMode = 'panel'
    scheduleSaveState()
  }
  panelWindow.on('move', persist)
  panelWindow.on('resize', persist)
  panelWindow.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    collapsePanelToOrb()
  })
  panelWindow.on('closed', () => {
    panelWindow = null
    refreshAssistantVisible()
  })
  loadPanelRenderer(panelWindow)
  return panelWindow
}

function displayAreaFor(point: { x: number; y: number }): Rectangle {
  return screen.getDisplayNearestPoint(point).workArea
}

function orbWindowBounds(): Rectangle {
  return {
    x: Math.round(orbCenter.x - ORB_WINDOW_SIZE / 2),
    y: Math.round(orbCenter.y - ORB_WINDOW_SIZE / 2),
    width: ORB_WINDOW_SIZE,
    height: ORB_WINDOW_SIZE,
  }
}

/** 首次展开时的默认面板位置：贴近图标所在显示器。 */
function defaultPanelBounds(): Rectangle {
  const area = displayAreaFor(orbCenter)
  const width = Math.min(PANEL_WIDTH, Math.max(320, area.width - EDGE_MARGIN * 2))
  const height = Math.min(PANEL_HEIGHT, Math.max(420, area.height - EDGE_MARGIN * 2))
  return {
    x: Math.round(
      clamp(
        orbCenter.x - width + ORB_ICON_SIZE / 2,
        area.x + EDGE_MARGIN,
        area.x + area.width - width - EDGE_MARGIN,
      ),
    ),
    y: Math.round(
      clamp(
        orbCenter.y - height / 2,
        area.y + EDGE_MARGIN,
        area.y + area.height - height - EDGE_MARGIN,
      ),
    ),
    width,
    height,
  }
}

function resolvePanelBounds(): Rectangle {
  if (panelBoundsState && boundsVisible(panelBoundsState)) return panelBoundsState
  return defaultPanelBounds()
}

function notifyOrbMode(): void {
  if (orbWindow && !orbWindow.isDestroyed()) {
    orbWindow.webContents.send('desktop:assistant-mode', orbSide)
  }
}

function refreshAssistantVisible(): void {
  const orbVisible = Boolean(
    orbWindow && !orbWindow.isDestroyed() && orbWindow.isVisible(),
  )
  const panelVisible = Boolean(
    panelWindow && !panelWindow.isDestroyed() && panelWindow.isVisible(),
  )
  assistantVisible = orbVisible || panelVisible
  broadcastAssistantVisible()
}

function hideOrbWindow(): void {
  if (orbWindow && !orbWindow.isDestroyed()) orbWindow.hide()
  refreshAssistantVisible()
}

function showOrbWindow(): void {
  const win = ensureOrbWindow()
  if (panelWindow && !panelWindow.isDestroyed()) panelWindow.hide()
  const area = displayAreaFor(orbCenter)
  if (orbSide === 'right') orbCenter.x = area.x + area.width - EDGE_REVEAL
  if (orbSide === 'left') orbCenter.x = area.x + EDGE_REVEAL
  orbCenter.y = Math.round(
    clamp(
      orbCenter.y,
      area.y + ORB_ICON_SIZE / 2,
      area.y + area.height - ORB_ICON_SIZE / 2,
    ),
  )
  win.setBounds(orbWindowBounds())
  win.showInactive()
  win.setAlwaysOnTop(true, 'floating')
  win.moveTop()
  win.webContents.send('desktop:assistant-mode', orbSide)
  win.webContents.send('desktop:assistant-reset')
  lastMode = 'orb'
  refreshAssistantVisible()
  scheduleSaveState()
}

function openPanelWindow(): void {
  const win = ensurePanelWindow()
  if (orbWindow && !orbWindow.isDestroyed()) orbWindow.hide()
  win.setBounds(resolvePanelBounds())
  let revealed = false
  const reveal = (): void => {
    if (revealed || win.isDestroyed()) return
    revealed = true
    win.show()
    win.focus()
    win.setAlwaysOnTop(true, 'floating')
    win.moveTop()
    refreshAssistantVisible()
  }
  // 首次打开时等待页面就绪，避免先闪一个空白的深色窗口。
  if (win.webContents.isLoadingMainFrame()) {
    const fallback = setTimeout(reveal, 800)
    win.once('ready-to-show', () => {
      clearTimeout(fallback)
      reveal()
    })
  } else {
    reveal()
  }
  lastMode = 'panel'
  refreshAssistantVisible()
  scheduleSaveState()
}

function collapsePanelToOrb(): void {
  if (panelWindow && !panelWindow.isDestroyed()) {
    if (panelWindow.isVisible()) panelBoundsState = panelWindow.getBounds()
    panelWindow.hide()
  }
  showOrbWindow()
}

function hideAssistant(): void {
  if (panelWindow && !panelWindow.isDestroyed() && panelWindow.isVisible()) {
    panelBoundsState = panelWindow.getBounds()
  }
  if (panelWindow && !panelWindow.isDestroyed()) panelWindow.hide()
  if (orbWindow && !orbWindow.isDestroyed()) orbWindow.hide()
  refreshAssistantVisible()
  scheduleSaveState()
}

function toggleAssistant(): boolean {
  if (assistantVisible) {
    hideAssistant()
    return false
  }
  if (lastMode === 'panel') openPanelWindow()
  else showOrbWindow()
  return true
}

/** 拖动图标：主进程轮询鼠标位置移动窗口，避免渲染层坐标与 DPI 换算。 */
function startAssistantDrag(revealed: boolean): void {
  const win = orbWindow
  if (!win || win.isDestroyed() || !win.isVisible()) return
  if (dragTimer) {
    clearInterval(dragTimer)
    dragTimer = null
  }
  // 未滑出的贴边图标：先把窗口平移到视觉中心，避免开始拖拽时图标跳一下。
  let shift = 0
  if (!revealed && orbSide === 'right') shift = EDGE_REVEAL
  if (!revealed && orbSide === 'left') shift = -EDGE_REVEAL
  if (shift !== 0) {
    orbCenter.x += shift
    win.setPosition(
      Math.round(orbCenter.x - ORB_WINDOW_SIZE / 2),
      Math.round(orbCenter.y - ORB_WINDOW_SIZE / 2),
      false,
    )
  }
  if (orbSide !== 'none') {
    orbSide = 'none'
    notifyOrbMode()
  }
  const cursor = screen.getCursorScreenPoint()
  const bounds = win.getBounds()
  dragOffset = {
    x: cursor.x - (bounds.x + ORB_WINDOW_SIZE / 2),
    y: cursor.y - (bounds.y + ORB_WINDOW_SIZE / 2),
  }
  dragTimer = setInterval(() => {
    const target = orbWindow
    if (!target || target.isDestroyed()) return
    const point = screen.getCursorScreenPoint()
    const area = screen.getDisplayNearestPoint(point).workArea
    const centerX = clamp(point.x - dragOffset.x, area.x, area.x + area.width)
    const centerY = clamp(
      point.y - dragOffset.y,
      area.y + ORB_ICON_SIZE / 2,
      area.y + area.height - ORB_ICON_SIZE / 2,
    )
    orbCenter = { x: Math.round(centerX), y: Math.round(centerY) }
    target.setPosition(
      Math.round(orbCenter.x - ORB_WINDOW_SIZE / 2),
      Math.round(orbCenter.y - ORB_WINDOW_SIZE / 2),
      false,
    )
  }, 16)
}

function endAssistantDrag(): void {
  if (dragTimer) {
    clearInterval(dragTimer)
    dragTimer = null
  }
  const win = orbWindow
  if (!win || win.isDestroyed()) return
  const area = screen.getDisplayNearestPoint(orbCenter).workArea
  if (orbCenter.x - area.x <= SNAP_DISTANCE) orbSide = 'left'
  else if (area.x + area.width - orbCenter.x <= SNAP_DISTANCE) orbSide = 'right'
  else orbSide = 'none'
  if (orbSide === 'left') orbCenter.x = area.x + EDGE_REVEAL
  if (orbSide === 'right') orbCenter.x = area.x + area.width - EDGE_REVEAL
  orbCenter.y = Math.round(
    clamp(
      orbCenter.y,
      area.y + ORB_ICON_SIZE / 2,
      area.y + area.height - ORB_ICON_SIZE / 2,
    ),
  )
  win.setBounds(orbWindowBounds())
  notifyOrbMode()
  lastMode = 'orb'
  scheduleSaveState()
}

function broadcastAssistantVisible(): void {
  if (popup && !popup.isDestroyed()) {
    popup.webContents.send('desktop:assistant-visible-changed', assistantVisible)
  }
}

// ---------- 运行中会话汇总 ----------

function runningSnapshot(): RunningSessionInfo[] {
  const merged = new Map<string, RunningSessionInfo>()
  for (const list of runningReports.values()) {
    for (const item of list) merged.set(item.threadId, item)
  }
  return [...merged.values()]
}

function broadcastRunningSessions(): void {
  if (popup && !popup.isDestroyed() && popup.isVisible()) {
    popup.webContents.send('desktop:running-sessions', runningSnapshot())
  }
}

function normalizeRunning(payload: unknown): RunningSessionInfo[] {
  if (!Array.isArray(payload)) return []
  return payload
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      threadId: String(item['threadId'] || ''),
      title: String(item['title'] || '新对话'),
      waiting: Boolean(item['waiting']),
    }))
    .filter((item) => Boolean(item.threadId))
}

// ---------- IPC ----------

function registerDesktopIpc(): void {
  ipcMain.on('desktop:open-window', () => {
    popup?.hide()
    showMainWindow()
  })

  ipcMain.on('desktop:open-session', (_event, threadId: unknown) => {
    if (typeof threadId !== 'string' || !threadId) return
    popup?.hide()
    showMainWindow()
    const win = getMainWindow()
    if (!win || win.isDestroyed()) return
    const send = (): void => win.webContents.send('desktop:select-session', threadId)
    if (win.webContents.isLoading()) win.webContents.once('did-finish-load', send)
    else send()
  })

  ipcMain.handle('desktop:toggle-assistant', () => toggleAssistant())
  ipcMain.handle('desktop:assistant-visible', () => assistantVisible)
  ipcMain.handle('desktop:running-sessions', () => runningSnapshot())
  ipcMain.handle('desktop:assistant-mode', () => orbSide)
  ipcMain.on('desktop:assistant-expand', () => openPanelWindow())
  ipcMain.on('desktop:assistant-collapse', () => collapsePanelToOrb())
  ipcMain.on('desktop:assistant-drag-start', (_event, revealed: unknown) =>
    startAssistantDrag(Boolean(revealed)),
  )
  ipcMain.on('desktop:assistant-drag-end', () => endAssistantDrag())

  ipcMain.on('desktop:quit', () => {
    popup?.hide()
    quitting = true
    app.quit()
  })

  ipcMain.on('desktop:report-running', (event, payload: unknown) => {
    const id = event.sender.id
    runningReports.set(id, normalizeRunning(payload))
    if (!watchedWebContents.has(id)) {
      watchedWebContents.add(id)
      event.sender.once('destroyed', () => {
        watchedWebContents.delete(id)
        runningReports.delete(id)
        broadcastRunningSessions()
      })
    }
    broadcastRunningSessions()
  })

  ipcMain.on('desktop:tray-popup-resize', (event, height: unknown) => {
    if (!popup || event.sender !== popup.webContents) return
    popupHeight = clamp(Math.round(Number(height) || 0), POPUP_MIN_HEIGHT, POPUP_MAX_HEIGHT)
    if (popup.isVisible()) positionPopup()
  })
}
