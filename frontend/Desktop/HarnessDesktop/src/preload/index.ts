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
}
