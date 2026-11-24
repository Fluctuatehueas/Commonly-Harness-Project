import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

/** 自绘窗口按钮桥接；渲染进程只用这 4 个能力，不暴露原始 ipcRenderer。 */
const windowApi = {
  minimize: (): void => ipcRenderer.send('window:minimize'),
  toggleMaximize: (): void => ipcRenderer.send('window:toggle-maximize'),
  close: (): void => ipcRenderer.send('window:close'),
  isMaximized: (): Promise<boolean> =>
    ipcRenderer.invoke('window:is-maximized') as Promise<boolean>,
  onMaximizeChange: (callback: (maximized: boolean) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, maximized: boolean): void => {
      callback(maximized)
    }
    ipcRenderer.on('window:maximized', listener)
    return () => ipcRenderer.removeListener('window:maximized', listener)
  }
}

// Custom APIs for renderer
const api = {}

/** 工作区设置页使用的目录选择桥接；渲染进程只拿到最终路径。 */
const workspaceApi = {
  pickDirectory: (): Promise<string | null> =>
    ipcRenderer.invoke('workspace:pick-directory') as Promise<string | null>,
  revealInFolder: (path: string): Promise<boolean> =>
    ipcRenderer.invoke('workspace:reveal-in-folder', path) as Promise<boolean>,
  showItemInFolder: (path: string): Promise<boolean> =>
    ipcRenderer.invoke('workspace:show-item-in-folder', path) as Promise<boolean>
}

/** Python 后端状态：由主进程托管，渲染进程只读取完整性/运行状态。 */
const backendApi = {
  status: (): Promise<{
    running: boolean
    port: number
    integrity: string
    lowIntegrity: boolean
    hint: string
    error: string
    owned: boolean
  }> => ipcRenderer.invoke('backend:status')
}

/** 桌面壳能力：托盘弹窗、悬浮助手与运行中会话上报。 */
const desktopApi = {
  openWindow: (): void => ipcRenderer.send('desktop:open-window'),
  openSession: (threadId: string): void =>
    ipcRenderer.send('desktop:open-session', threadId),
  toggleAssistant: (): Promise<boolean> =>
    ipcRenderer.invoke('desktop:toggle-assistant') as Promise<boolean>,
  isAssistantVisible: (): Promise<boolean> =>
    ipcRenderer.invoke('desktop:assistant-visible') as Promise<boolean>,
  quit: (): void => ipcRenderer.send('desktop:quit'),
  getRunningSessions: (): Promise<
    { threadId: string; title: string; waiting?: boolean }[]
  > => ipcRenderer.invoke('desktop:running-sessions'),
  reportRunningSessions: (
    sessions: { threadId: string; title: string; waiting?: boolean }[],
  ): void => ipcRenderer.send('desktop:report-running', sessions),
  resizeTrayPopup: (height: number): void =>
    ipcRenderer.send('desktop:tray-popup-resize', height),
  /** 图标点击：打开助手面板窗口。 */
  openAssistantPanel: (): void => ipcRenderer.send('desktop:assistant-expand'),
  /** 面板收起：隐藏面板并回到图标。 */
  collapseAssistant: (): void => ipcRenderer.send('desktop:assistant-collapse'),
  getAssistantMode: (): Promise<'left' | 'right' | 'none'> =>
    ipcRenderer.invoke('desktop:assistant-mode') as Promise<'left' | 'right' | 'none'>,
  startAssistantDrag: (revealed: boolean): void =>
    ipcRenderer.send('desktop:assistant-drag-start', revealed),
  endAssistantDrag: (): void => ipcRenderer.send('desktop:assistant-drag-end'),
  onAssistantMode: (
    callback: (side: 'left' | 'right' | 'none') => void,
  ): (() => void) => {
    const listener = (
      _event: Electron.IpcRendererEvent,
      side: 'left' | 'right' | 'none',
    ): void => callback(side)
    ipcRenderer.on('desktop:assistant-mode', listener)
    return () => ipcRenderer.removeListener('desktop:assistant-mode', listener)
  },
  onOpenSession: (callback: (threadId: string) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, threadId: string): void => {
      callback(threadId)
    }
    ipcRenderer.on('desktop:select-session', listener)
    return () => ipcRenderer.removeListener('desktop:select-session', listener)
  },
  onRunningSessions: (
    callback: (sessions: { threadId: string; title: string; waiting?: boolean }[]) => void,
  ): (() => void) => {
    const listener = (
      _event: Electron.IpcRendererEvent,
      sessions: { threadId: string; title: string; waiting?: boolean }[],
    ): void => callback(sessions)
    ipcRenderer.on('desktop:running-sessions', listener)
    return () => ipcRenderer.removeListener('desktop:running-sessions', listener)
  },
  onAssistantVisibleChange: (callback: (visible: boolean) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, visible: boolean): void => {
      callback(visible)
    }
    ipcRenderer.on('desktop:assistant-visible-changed', listener)
    return () => ipcRenderer.removeListener('desktop:assistant-visible-changed', listener)
  },
  onAssistantReset: (callback: () => void): (() => void) => {
    const listener = (): void => callback()
    ipcRenderer.on('desktop:assistant-reset', listener)
    return () => ipcRenderer.removeListener('desktop:assistant-reset', listener)
  }
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
    contextBridge.exposeInMainWorld('harnessWindow', windowApi)
    contextBridge.exposeInMainWorld('harnessWorkspace', workspaceApi)
    contextBridge.exposeInMainWorld('harnessBackend', backendApi)
    contextBridge.exposeInMainWorld('harnessDesktop', desktopApi)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
  // @ts-ignore (define in dts)
  window.harnessWindow = windowApi
  // @ts-ignore (define in dts)
  window.harnessWorkspace = workspaceApi
  // @ts-ignore (define in dts)
  window.harnessBackend = backendApi
  // @ts-ignore (define in dts)
  window.harnessDesktop = desktopApi
}
