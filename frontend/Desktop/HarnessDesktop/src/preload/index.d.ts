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
  }
}
