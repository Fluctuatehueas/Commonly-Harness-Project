import { app } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

/** 后端健康状态；渲染进程通过 IPC 读取。 */
export interface BackendStatus {
  running: boolean
  port: number
  /** 后端进程完整性级别（medium / low / ...）；未知时为 unknown。 */
  integrity: string
  /** 是否低于 Medium：为 true 时后端无法写入普通目录。 */
  lowIntegrity: boolean
  /** 完整性提示；空串表示正常。 */
  hint: string
  error: string
  owned: boolean
}

const HOST = process.env['HARNESS_API_HOST'] || '127.0.0.1'
const PORT = Number(process.env['HARNESS_API_PORT'] || 8000)
const HEALTH_URL = `http://${HOST}:${PORT}/health`

let child: ChildProcess | null = null
let owned = false
let status: BackendStatus = {
  running: false,
  port: PORT,
  integrity: 'unknown',
  lowIntegrity: false,
  hint: '',
  error: '',
  owned: false,
}

/** 后端工程根：开发期是仓库根（HarnessDesktop 向上三层），打包后由资源目录提供。 */
function backendRoot(): string {
  const override = process.env['HARNESS_BACKEND_ROOT']
  if (override) return resolve(override)
  if (app.isPackaged) return join(process.resourcesPath, 'backend')
  return resolve(app.getAppPath(), '../../..')
}

/** 优先使用项目自带虚拟环境，其次回退到 PATH 里的 python。 */
function pythonPath(root: string): string {
  const override = process.env['HARNESS_PYTHON']
  if (override) return override
  const candidates =
    process.platform === 'win32'
      ? [join(root, '.venv', 'Scripts', 'python.exe')]
      : [join(root, '.venv', 'bin', 'python')]
  return candidates.find((item) => existsSync(item)) || 'python'
}

interface HealthPayload {
  integrity?: string
  low_integrity?: boolean
  integrity_hint?: string
}

async function probe(): Promise<HealthPayload | null> {
  try {
    const res = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(1500) })
    if (!res.ok) return null
    return (await res.json()) as HealthPayload
  } catch {
    return null
  }
}

async function waitForHealth(timeoutMs = 20_000): Promise<HealthPayload | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const payload = await probe()
    if (payload) return payload
    await new Promise((resolveTimer) => setTimeout(resolveTimer, 300))
  }
  return null
}

function applyHealth(payload: HealthPayload | null): void {
  if (!payload) {
    status = {
      ...status,
      running: false,
      error: child ? '后端启动超时，请查看控制台日志' : '后端不可达',
    }
    return
  }
  const integrity = String(payload.integrity || 'unknown')
  const lowIntegrity = Boolean(payload.low_integrity)
  status = {
    ...status,
    running: true,
    integrity,
    lowIntegrity,
    hint: String(payload.integrity_hint || ''),
    error: '',
    owned,
  }
  if (lowIntegrity) {
    console.error('[backend] 进程完整性过低：', status.hint)
  }
}

export async function startBackend(): Promise<BackendStatus> {
  const existing = await probe()
  if (existing) {
    // 已有后端在运行（可能是用户手动启动）：直接复用，不接管生命周期。
    owned = false
    applyHealth(existing)
    return status
  }

  const root = backendRoot()
  const entry = join(root, 'main.py')
  if (!existsSync(entry)) {
    status = { ...status, running: false, error: `找不到后端入口：${entry}` }
    return status
  }

  try {
    child = spawn(pythonPath(root), ['main.py'], {
      cwd: root,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        HARNESS_API_HOST: HOST,
        HARNESS_API_PORT: String(PORT),
      },
    })
    owned = true
    child.stdout?.on('data', (chunk: Buffer) =>
      console.log('[backend]', chunk.toString().trim()),
    )
    child.stderr?.on('data', (chunk: Buffer) =>
      console.error('[backend]', chunk.toString().trim()),
    )
    child.on('exit', (code) => {
      console.warn('[backend] 退出，code =', code)
      child = null
      if (owned) {
        status = { ...status, running: false, owned: false, error: `后端已退出（code ${code}）` }
        owned = false
      }
    })
  } catch (error) {
    status = { ...status, running: false, error: `启动后端失败：${String(error)}` }
    return status
  }

  applyHealth(await waitForHealth())
  return status
}

export function getBackendStatus(): BackendStatus {
  return status
}

export async function stopBackend(): Promise<void> {
  if (!child || !owned) return
  const pid = child.pid
  child = null
  owned = false
  if (!pid) return
  if (process.platform === 'win32') {
    // Windows 没有 SIGTERM 语义，必须杀进程树，避免 uvicorn 子进程残留。
    spawn('taskkill', ['/pid', String(pid), '/f', '/t'], { windowsHide: true })
    return
  }
  try {
    process.kill(-pid, 'SIGTERM')
  } catch {
    // 进程可能已退出
  }
}
