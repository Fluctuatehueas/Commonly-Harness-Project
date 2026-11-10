// 演示用户身份：切换后所有请求统一携带 X-User-Id，后端据此做用户级隔离。
// 真实接入登录时，只需把 localStorage 换成网关注入的身份即可。

export interface DemoUser {
  id: string
  name: string
  role: string
  accent: string
}

export const DEMO_USERS: DemoUser[] = [ 
  { id: 'console_user', name: '控制台用户', role: '控制台', accent: '#4f8cff' },
  { id: 'alice', name: 'Alice', role: '产品经理', accent: '#35e0ff' },
  { id: 'bob', name: 'Bob', role: '研发工程师', accent: '#8f7bff' },
  { id: 'carol', name: 'Carol', role: '数据分析师', accent: '#3ddc84' },
]

const STORAGE_KEY = 'harness:user-id'
const DEFAULT_USER = DEMO_USERS[0]!

export function currentUserId(): string {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored && DEMO_USERS.some((item) => item.id === stored)) return stored
  } catch {
    /* 隐私模式下 localStorage 不可用时使用默认用户 */
  }
  return DEFAULT_USER.id
}

export function currentUser(): DemoUser {
  const id = currentUserId()
  return DEMO_USERS.find((item) => item.id === id) || DEFAULT_USER
}

/** 切换演示用户：清空前端缓存并重载，确保不残留上一个用户的内存状态。 */
export function switchUser(userId: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, userId)
  } catch {
    /* ignore */
  }
  window.location.reload()
}

/** 给所有同源请求注入 X-User-Id；在 main.ts 启动时安装一次。 */
export function installUserHeader(): void {
  if ((window as { __harnessUserHeaderInstalled?: boolean }).__harnessUserHeaderInstalled) {
    return
  }
  ;(window as { __harnessUserHeaderInstalled?: boolean }).__harnessUserHeaderInstalled = true
  const original = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(
      init?.headers || (input instanceof Request ? input.headers : undefined),
    )
    headers.set('X-User-Id', currentUserId())
    if (input instanceof Request) {
      return original(new Request(input, { ...init, headers }))
    }
    return original(input, { ...init, headers })
  }
}
