import { ElectronAPI } from '@electron-toolkit/preload'

interface HarnessWindowApi {
  minimize: () => void
  toggleMaximize: () => void
  close: () => void
  isMaximized: () => Promise<boolean>
  onMaximizeChange: (callback: (maximized: boolean) => void) => () => void
}

interface HarnessWorkspaceApi {
  /** 打开系统目录选择框；用户取消时返回 null */
  pickDirectory: () => Promise<string | null>
  /** 在系统资源管理器中打开项目目录；失败返回 false */
  revealInFolder: (path: string) => Promise<boolean>
  /** 在资源管理器中定位并选中文件；失败返回 false */
  showItemInFolder: (path: string) => Promise<boolean>
}

interface HarnessBackendApi {
  /** 读取 Python 后端运行状态与进程完整性级别 */
  status: () => Promise<{
    running: boolean
    port: number
    integrity: string
    lowIntegrity: boolean
    hint: string
    error: string
    owned: boolean
  }>
}

interface HarnessRunningSession {
  threadId: string
  title: string
  waiting?: boolean
}

interface HarnessDesktopApi {
  /** 显示并聚焦主窗体（从托盘弹窗调用）。 */
  openWindow: () => void
  /** 显示主窗体并切换到指定会话。 */
  openSession: (threadId: string) => void
  /** 召唤 / 收起悬浮助手，返回操作后的可见状态。 */
  toggleAssistant: () => Promise<boolean>
  isAssistantVisible: () => Promise<boolean>
  /** 退出整个 CommonHarness 进程。 */
  quit: () => void
  getRunningSessions: () => Promise<HarnessRunningSession[]>
  /** 渲染窗口把当前正在工作的会话上报给主进程汇总。 */
  reportRunningSessions: (sessions: HarnessRunningSession[]) => void
  /** 托盘弹窗内容高度变化后重新贴边定位。 */
  resizeTrayPopup: (height: number) => void
  openAssistantPanel: () => void
  collapseAssistant: () => void
  getAssistantMode: () => Promise<'left' | 'right' | 'none'>
  startAssistantDrag: (revealed: boolean) => void
  endAssistantDrag: () => void
  onAssistantMode: (callback: (side: 'left' | 'right' | 'none') => void) => () => void
  onOpenSession: (callback: (threadId: string) => void) => () => void
  onRunningSessions: (callback: (sessions: HarnessRunningSession[]) => void) => () => void
  onAssistantVisibleChange: (callback: (visible: boolean) => void) => () => void
  onAssistantReset: (callback: () => void) => () => void
}

declare global {
  interface Window {
    electron: ElectronAPI
    api: unknown
    /** 仅在 Electron 中注入；浏览器环境为 undefined */
    harnessWindow?: HarnessWindowApi
    /** 仅在 Electron 中注入；浏览器环境为 undefined */
    harnessWorkspace?: HarnessWorkspaceApi
    /** 仅在 Electron 中注入；浏览器环境为 undefined */
    harnessBackend?: HarnessBackendApi
    /** 仅在 Electron 中注入；浏览器环境为 undefined */
    harnessDesktop?: HarnessDesktopApi
  }
}

export {}
