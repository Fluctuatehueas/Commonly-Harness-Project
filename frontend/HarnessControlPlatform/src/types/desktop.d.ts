/** Electron 桌面壳注入的桥接类型；浏览器环境下这些对象为 undefined。 */
interface HarnessRunningSession {
  threadId: string
  title: string
  waiting?: boolean
}

interface HarnessDesktopBridge {
  openWindow: () => void
  openSession: (threadId: string) => void
  toggleAssistant: () => Promise<boolean>
  isAssistantVisible: () => Promise<boolean>
  quit: () => void
  getRunningSessions: () => Promise<HarnessRunningSession[]>
  reportRunningSessions: (sessions: HarnessRunningSession[]) => void
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

interface Window {
  harnessDesktop?: HarnessDesktopBridge
}
