import { app, dialog, shell, BrowserWindow, ipcMain } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { getBackendStatus, startBackend, stopBackend } from './backend'

/** 自绘窗口按钮所需的 IPC：全部以事件来源窗口为准，避免多窗口时操作错对象。 */
function registerWindowControls(): void {
  ipcMain.on('window:minimize', (event) => {
    BrowserWindow.fromWebContents(event.sender)?.minimize()
  })

  ipcMain.on('window:toggle-maximize', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender)
    if (!win) return
    if (win.isMaximized()) win.unmaximize()
    else win.maximize()
  })

  ipcMain.on('window:close', (event) => {
    BrowserWindow.fromWebContents(event.sender)?.close()
  })

  ipcMain.handle('window:is-maximized', (event) => {
    return BrowserWindow.fromWebContents(event.sender)?.isMaximized() ?? false
  })
}

function createWindow(): void {
  // Create the browser window.
  const mainWindow = new BrowserWindow({
    // 控制台是宽幅仪表盘布局，默认窗口给足宽度，并允许用户缩放。
    width: 1280,
    height: 860,
    minWidth: 1024,
    minHeight: 680,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#070f22',
    // 禁用系统默认标题栏与边框，窗口按钮由渲染进程自绘（见 WindowControls.vue）
    frame: false,
    // Windows 下保留圆角与阴影；无边框窗口同样需要这两个开关
    roundedCorners: true,
    hasShadow: true,
    icon,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    },
  })

  // 最大化状态变化通知渲染进程，用于切换「最大化 / 还原」图标
  const pushMaximizeState = (): void => {
    if (!mainWindow.isDestroyed()) {
      mainWindow.webContents.send('window:maximized', mainWindow.isMaximized())
    }
  }
  mainWindow.on('maximize', pushMaximizeState)
  mainWindow.on('unmaximize', pushMaximizeState)

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
  // Set app user model id for windows
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))

  // 无边框窗口的自绘按钮（最小化 / 最大化 / 关闭）
  registerWindowControls()

  // 工作区设置页选择本地目录；用户取消时返回 null。
  ipcMain.handle('workspace:pick-directory', async (event) => {
    const win = BrowserWindow.fromWebContents(event.sender)
    const options: Electron.OpenDialogOptions = {
      properties: ['openDirectory', 'createDirectory']
    }
    const result = win
      ? await dialog.showOpenDialog(win, options)
      : await dialog.showOpenDialog(options)
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })

  // 项目菜单打开磁盘目录；失败返回 false，由前端回退为复制路径。
  ipcMain.handle('workspace:reveal-in-folder', async (_event, target: unknown) => {
    if (typeof target !== 'string' || !target.trim()) return false
    try {
      const error = await shell.openPath(target)
      return !error
    } catch {
      return false
    }
  })

  // 在资源管理器中定位并选中文件（不直接打开文件）。
  ipcMain.handle('workspace:show-item-in-folder', async (_event, target: unknown) => {
    if (typeof target !== 'string' || !target.trim()) return false
    try {
      if (!existsSync(target)) return false
      shell.showItemInFolder(target)
      return true
    } catch {
      return false
    }
  })

  // 渲染进程读取 Python 后端状态（含进程完整性级别）。
  ipcMain.handle('backend:status', () => getBackendStatus())

  createWindow()

  // 由 Electron 主进程托管 Python 后端：正常启动时子进程继承 Medium 完整性，
  // 不会被文件系统沙箱降级。启动失败/完整性过低会记录到状态里。
  void startBackend()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('before-quit', () => {
  void stopBackend()
})
app.on('will-quit', () => {
  void stopBackend()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
