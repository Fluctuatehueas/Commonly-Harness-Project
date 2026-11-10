<script setup lang="ts">
// 系统设置：对话展示控制 + 后端运行参数（思考 / 联网搜索）+ 沙箱服务配置
import { onMounted, reactive, ref, type Component } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  Avatar,
  ChatDotRound,
  Check,
  Clock,
  Connection,
  Cpu,
  Folder,
  FolderOpened,
  Lock,
  MagicStick,
  Monitor,
  Operation,
  Reading,
  Tools,
  View,
  Warning,
} from '@element-plus/icons-vue'
import TopNav from '@/components/console/TopNav.vue'
import InfoHint from '@/components/common/InfoHint.vue'
import { useDisplayStore } from '@/stores/display'
import { useSettingsStore } from '@/stores/settings'
import {
  checkSandboxPort,
  fetchArchiveSettings,
  fetchContextSettings,
  fetchSandboxSettings,
  updateArchiveSettings,
  updateContextSettings,
  updateSandboxSettings,
  type ContextSettings,
  type LocalEnvironment,
  type SandboxSettings,
  type StoragePaths,
} from '@/api/settings'
import type { IconName } from '@/components/console/icons'

const display = useDisplayStore()
const settings = useSettingsStore()

// 展示项图标：项目独有图标改用 Element Plus 图标
const ROW_ICONS: Partial<Record<IconName, Component>> = {
  sparkles: MagicStick,
  wrench: Tools,
  book: Reading,
  bot: Avatar,
  clock: Clock,
  eye: View,
  message: ChatDotRound,
}

interface Row {
  key: keyof ReturnType<typeof useDisplayStore>['prefs']
  label: string
  desc: string
  icon: IconName
}

const ROWS: Row[] = [
  { key: 'showReasoning', label: '推理内容', desc: '在轨迹树中展示模型的思考内容（流式输出）', icon: 'sparkles' },
  { key: 'showToolCalls', label: '工具调用', desc: '显示 read_file / search_web / execute 等普通工具节点', icon: 'wrench' },
  { key: 'showSkills', label: '技能使用', desc: '显示 download_skill / assign_skill 等技能调用（仅一行摘要）', icon: 'book' },
  { key: 'showDelegation', label: '子 Agent 委派', desc: '显示 task 委派节点，以及子 Agent 的思考与工具过程', icon: 'bot' },
  { key: 'showAsyncTasks', label: '异步任务', desc: '显示 start_async_task 异步任务提交节点', icon: 'clock' },
  { key: 'expandToolArgs', label: '参数默认展开', desc: '轨迹节点默认展开参数与结果，而非折叠', icon: 'eye' },
  { key: 'typewriter', label: '打字机效果', desc: '流式 token 以逐字打字方式渲染', icon: 'message' },
]

// ---------- 沙箱服务 ----------
const sandbox = ref<SandboxSettings | null>(null)
const localEnv = ref<LocalEnvironment | null>(null)
const paths = ref<StoragePaths | null>(null)
const sandboxLoading = ref(true)
const sandboxSaving = ref(false)
const sandboxChecking = ref(false)
const sandboxError = ref('')
const showRuntimes = ref(false)
const clearKey = ref(false)

const form = reactive({
  host: '127.0.0.1',
  port: 18083 as number | undefined,
  apiKey: '',
  image: 'myagent-sandbox:1',
  defaultTimeout: 120 as number | undefined,
  lifetime: 86400 as number | undefined,
  prewarm: true,
  useServerProxy: true,
})

async function loadSandbox() {
  sandboxLoading.value = true
  sandboxError.value = ''
  try {
    const data = await fetchSandboxSettings()
    sandbox.value = data.sandbox
    localEnv.value = data.local_env
    paths.value = data.paths
    form.host = data.sandbox.host
    form.port = data.sandbox.port
    form.image = data.sandbox.image
    form.defaultTimeout = data.sandbox.default_timeout_seconds
    form.lifetime = data.sandbox.sandbox_timeout_seconds
    form.prewarm = data.sandbox.prewarm_enabled
    form.useServerProxy = data.sandbox.use_server_proxy
    form.apiKey = ''
  } catch (err) {
    sandboxError.value = (err as Error).message
  } finally {
    sandboxLoading.value = false
  }
}

async function saveSandbox() {
  if (sandboxSaving.value) return
  sandboxSaving.value = true
  sandboxError.value = ''
  try {
    await updateSandboxSettings({
      host: form.host.trim(),
      port: Number(form.port),
      api_key: form.apiKey.trim() || undefined,
      clear_api_key: clearKey.value,
      image: form.image.trim(),
      default_timeout_seconds: Number(form.defaultTimeout),
      sandbox_timeout_seconds: Number(form.lifetime),
      prewarm_enabled: form.prewarm,
      use_server_proxy: form.useServerProxy,
    })
    clearKey.value = false
    ElMessage.success('沙箱配置已保存并重建管理器，下一轮对话生效。')
    await loadSandbox()
  } catch (err) {
    sandboxError.value = (err as Error).message
  } finally {
    sandboxSaving.value = false
  }
}

async function checkPort() {
  if (sandboxChecking.value) return
  sandboxChecking.value = true
  sandboxError.value = ''
  try {
    const result = await checkSandboxPort(form.host.trim(), Number(form.port))
    if (result.reachable) {
      ElMessage.success(result.message)
    } else {
      sandboxError.value = result.message
    }
  } catch (err) {
    sandboxError.value = (err as Error).message
  } finally {
    sandboxChecking.value = false
  }
}

function clearApiKey() {
  clearKey.value = !clearKey.value
  if (clearKey.value) form.apiKey = ''
}

// ---------- 上下文压缩 ----------
const ctx = ref<ContextSettings | null>(null)
const ctxLoading = ref(true)
const ctxSaving = ref(false)
const ctxError = ref('')
const ctxForm = reactive({
  enabled: true,
  trigger: 0.7 as number | undefined,
  hard: 0.88 as number | undefined,
  keep: 0.25 as number | undefined,
  min_rounds: 6 as number | undefined,
  cooldown: 3 as number | undefined,
  max_defer: 3 as number | undefined,
  timeout_seconds: 60 as number | undefined,
  eval_enabled: true,
  eval_judge: false,
  heartbeat_seconds: 5 as number | undefined,
})

function fillContext(data: ContextSettings) {
  ctx.value = data
  ctxForm.enabled = data.enabled
  ctxForm.trigger = data.trigger
  ctxForm.hard = data.hard
  ctxForm.keep = data.keep
  ctxForm.min_rounds = data.min_rounds
  ctxForm.cooldown = data.cooldown
  ctxForm.max_defer = data.max_defer
  ctxForm.timeout_seconds = data.timeout_seconds
  ctxForm.eval_enabled = data.eval_enabled
  ctxForm.eval_judge = data.eval_judge
  ctxForm.heartbeat_seconds = data.heartbeat_seconds
}

async function loadContext() {
  ctxLoading.value = true
  ctxError.value = ''
  try {
    fillContext(await fetchContextSettings())
  } catch (err) {
    ctxError.value = (err as Error).message
  } finally {
    ctxLoading.value = false
  }
}

async function saveContext() {
  ctxSaving.value = true
  ctxError.value = ''
  try {
    fillContext(
      await updateContextSettings({
        enabled: ctxForm.enabled,
        trigger: Number(ctxForm.trigger),
        hard: Number(ctxForm.hard),
        keep: Number(ctxForm.keep),
        min_rounds: Number(ctxForm.min_rounds),
        cooldown: Number(ctxForm.cooldown),
        max_defer: Number(ctxForm.max_defer),
        timeout_seconds: Number(ctxForm.timeout_seconds),
        eval_enabled: ctxForm.eval_enabled,
        eval_judge: ctxForm.eval_judge,
        heartbeat_seconds: Number(ctxForm.heartbeat_seconds),
      }),
    )
    ElMessage.success('已保存到当前用户配置并重建 Agent 图，下一轮对话按新阈值生效')
  } catch (err) {
    ctxError.value = (err as Error).message
  } finally {
    ctxSaving.value = false
  }
}

function fmtToken(value?: number): string {
  return (value || 0).toLocaleString('zh-CN')
}

// ---------- 会话归档 ----------
const archiveDays = ref<number | undefined>(30)
const archiveLoading = ref(true)
const archiveSaving = ref(false)
const archiveError = ref('')

async function loadArchive() {
  archiveLoading.value = true
  archiveError.value = ''
  try {
    archiveDays.value = (await fetchArchiveSettings()).days
  } catch (err) {
    archiveError.value = (err as Error).message
  } finally {
    archiveLoading.value = false
  }
}

async function saveArchive() {
  if (archiveSaving.value) return
  archiveSaving.value = true
  archiveError.value = ''
  try {
    const data = await updateArchiveSettings(Number(archiveDays.value))
    archiveDays.value = data.days
    ElMessage.success(
      data.days === 0 ? '已关闭自动归档' : `已设置：${data.days} 天无新记录自动归档`,
    )
  } catch (err) {
    archiveError.value = (err as Error).message
  } finally {
    archiveSaving.value = false
  }
}

onMounted(() => {
  void settings.load()
  void loadSandbox()
  void loadContext()
  void loadArchive()
})
</script>

<template>
  <div class="app-shell">
    <TopNav />
    <div class="page">
      <header class="settings-intro">
        <p class="intro-kicker mono">SYSTEM CONFIG</p>
        <h1>系统设置</h1>
        <p>对话展示、运行参数与执行环境，按模块分组。</p>
      </header>

      <section class="settings-module">
        <header class="module-head">
          <span class="module-icon"><el-icon><ChatDotRound /></el-icon></span>
          <div class="module-heading">
            <h2>对话控制</h2>
            <p>配置对话页轨迹树的可见内容</p>
          </div>
          <el-button size="small" @click="display.reset()">恢复默认</el-button>
        </header>

        <ul class="rows">
          <li v-for="row in ROWS" :key="String(row.key)">
            <el-icon class="icon"><component :is="ROW_ICONS[row.icon] || ChatDotRound" /></el-icon>
            <span class="text">
              <strong>{{ row.label }}</strong>
              <span>{{ row.desc }}</span>
            </span>
            <el-switch
              :model-value="display.prefs[row.key]"
              @update:model-value="
                (v: string | number | boolean) => (display.prefs[row.key] = Boolean(v))
              "
            />
          </li>
        </ul>

        <div class="foot">
          <el-icon><Lock /></el-icon>
          展示设置保存在浏览器本地（localStorage），仅影响当前控制台，不改变 Agent 的执行行为。
        </div>
      </section>

      <section class="settings-module">
        <header class="module-head">
          <span class="module-icon"><el-icon><Cpu /></el-icon></span>
          <div class="module-heading">
            <h2>对话运行参数</h2>
            <p>按当前用户保存并热更新</p>
          </div>
        </header>

        <ul class="rows">
          <li>
            <el-icon class="icon"><MagicStick /></el-icon>
            <span class="text">
              <strong>思考模式</strong>
              <span>开启后模型返回推理内容（reasoning_content），轨迹树会流式展示</span>
            </span>
            <el-switch
              :model-value="settings.thinking"
              :disabled="settings.saving || settings.online === false"
              @update:model-value="(v: string | number | boolean) => settings.setThinking(Boolean(v))"
            />
          </li>
          <li>
            <el-icon class="icon"><Connection /></el-icon>
            <span class="text">
              <strong>联网搜索</strong>
              <span>开启后 Agent 绑定 search_web / fetch_web；关闭后不再下发搜索工具</span>
            </span>
            <el-switch
              :model-value="settings.webSearch"
              :disabled="settings.saving || settings.online === false"
              @update:model-value="(v: string | number | boolean) => settings.setWebSearch(Boolean(v))"
            />
          </li>
        </ul>

        <div class="foot" :class="{ warn: settings.online === false || Boolean(settings.error) }">
          <el-icon>
            <Warning v-if="settings.online === false || settings.error" />
            <Monitor v-else />
          </el-icon>
          <span v-if="settings.online === false">后端不可达：参数开关暂时不可写，请先启动 FastAPI。</span>
          <span v-else-if="settings.error">{{ settings.error }}</span>
          <span v-else>保存后会重置已缓存的 Agent 图，下一轮对话立即生效（不会中断正在进行的一轮）。</span>
        </div>
      </section>

      <!-- 上下文压缩：阈值与评估策略 -->
      <section class="settings-module">
        <header class="module-head">
          <span class="module-icon"><el-icon><Operation /></el-icon></span>
          <div class="module-heading">
            <h2>上下文压缩</h2>
            <p>软 / 硬水位、保留预算与评估策略；保存到当前用户并重建 Agent 图</p>
          </div>
          <el-button
            type="primary"
            size="small"
            :icon="Check"
            :loading="ctxSaving"
            :disabled="ctxLoading"
            @click="saveContext"
          >
            {{ ctxSaving ? '保存中…' : '保存压缩配置' }}
          </el-button>
        </header>

        <div v-if="ctxLoading" v-loading="true" class="sandbox-loading">
          <span class="loading-text">正在读取压缩配置…</span>
        </div>
        <template v-else>
          <div v-if="ctx" class="ctx-budget">
            <span>窗口 <b>{{ fmtToken(ctx.budget.window) }}</b></span>
            <span>可用 <b>{{ fmtToken(ctx.budget.usable) }}</b></span>
            <span>软水位 <b>{{ fmtToken(ctx.budget.soft) }}</b></span>
            <span>硬水位 <b>{{ fmtToken(ctx.budget.hard) }}</b></span>
            <span>保留 <b>{{ fmtToken(ctx.budget.keep) }}</b></span>
          </div>

          <el-form label-position="top" class="sandbox-grid">
            <el-form-item>
              <template #label>
                <span class="label-line">
                  软水位（相对可用预算）
                  <InfoHint title="软水位" text="上下文用掉一部分后，系统提前开始整理，避免拖到快满才动手。数值越小，整理越早、越频繁；单位是“可用预算”的比例，例如 0.70 表示用到七成时开始整理。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.trigger"
                :step="0.01"
                :min="0.05"
                :max="0.99"
                :precision="2"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  硬水位（强制压缩）
                  <InfoHint title="硬水位" text="上下文到达这个比例时必须整理，即使助手正忙也会执行。应明显高于软水位，留出提前整理的余地。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.hard"
                :step="0.01"
                :min="0.05"
                :max="1"
                :precision="2"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  保留预算
                  <InfoHint title="保留预算" text="整理后至少要原样保留的最近对话比例。留得越多越不容易丢细节，但节省的空间变少；留得太少可能丢掉刚发生的关键信息。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.keep"
                :step="0.01"
                :min="0.05"
                :max="0.99"
                :precision="2"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  首次压缩最少轮次
                  <InfoHint title="首次压缩最少轮次" text="会话至少聊过这么多轮才允许第一次整理，避免刚开始对话就被压缩。整理过一次后不再受此限制。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.min_rounds"
                :min="1"
                :max="100"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  冷却（模型调用次数）
                  <InfoHint title="冷却" text="两次整理之间至少间隔多少次模型调用，防止刚整理完又马上再整理、反复折腾上下文。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.cooldown"
                :min="0"
                :max="50"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  最大延迟次数
                  <InfoHint title="最大延迟次数" text="助手正处在工具调用链中时可以先推迟整理。这里设置最多能推迟几次，超过就强制执行，保证上下文不会溢出。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.max_defer"
                :min="0"
                :max="20"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  摘要超时（秒）
                  <InfoHint title="摘要超时" text="生成摘要的最长等待时间。超时后系统会改用“保留关键信息 + 最近消息”的兜底方案，保证对话能继续。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.timeout_seconds"
                :min="5"
                :max="300"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
            <el-form-item>
              <template #label>
                <span class="label-line">
                  进度心跳（秒）
                  <InfoHint title="进度心跳" text="整理期间系统每隔多久向页面汇报一次进度。心跳也用于判断连接是否掉线，间隔太大时会误判为断线。" />
                </span>
              </template>
              <el-input-number
                v-model="ctxForm.heartbeat_seconds"
                :min="2"
                :max="60"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
          </el-form>

          <ul class="sandbox-switches">
            <li>
              <div class="text">
                <strong>启用自动压缩</strong>
                <span>关闭后达到水位也不压缩，仅保留监控与手动 compact_conversation</span>
              </div>
              <el-switch
                :model-value="ctxForm.enabled"
                @update:model-value="(v: string | number | boolean) => (ctxForm.enabled = Boolean(v))"
              />
            </li>
            <li>
              <div class="text">
                <strong>规则评估</strong>
                <span>锚点保真、时机标签与成本回执；零额外模型调用</span>
              </div>
              <el-switch
                :model-value="ctxForm.eval_enabled"
                @update:model-value="
                  (v: string | number | boolean) => (ctxForm.eval_enabled = Boolean(v))
                "
              />
            </li>
            <li>
              <div class="text">
                <strong>
                  LLM 时机裁判
                  <InfoHint
                    title="LLM 时机裁判"
                    text="整理结束后，额外让模型回看这次整理发生得是否合适：结论已收敛算合适，推导中途算过早。结果会写进轨迹，用于后续自动调参。开启后每次整理会多一次模型调用。"
                    tone="accent"
                  />
                </strong>
                <span>压缩结束后异步调用摘要模型判断时机，额外一次调用，默认关闭</span>
              </div>
              <el-switch
                :model-value="ctxForm.eval_judge"
                @update:model-value="
                  (v: string | number | boolean) => (ctxForm.eval_judge = Boolean(v))
                "
              />
            </li>
          </ul>

          <div class="foot" :class="{ warn: Boolean(ctxError) }">
            <el-icon>
              <Warning v-if="ctxError" />
              <Monitor v-else />
            </el-icon>
            <span v-if="ctxError">{{ ctxError }}</span>
            <span v-else>修改会重建用户级 Agent 图；正在运行的一轮不受影响，下一轮按新阈值判断。</span>
          </div>
        </template>
      </section>

      <!-- 会话归档：长期无新记录的会话自动归档 -->
      <section class="settings-module">
        <header class="module-head">
          <span class="module-icon"><el-icon><FolderOpened /></el-icon></span>
          <div class="module-heading">
            <h2>会话归档</h2>
            <p>超过设定天数没有新记录的会话会在进入对话页时自动归档；归档后不再被 Agent 读取</p>
          </div>
          <el-button
            type="primary"
            size="small"
            :icon="Check"
            :loading="archiveSaving"
            :disabled="archiveLoading"
            @click="saveArchive"
          >
            {{ archiveSaving ? '保存中…' : '保存归档设置' }}
          </el-button>
        </header>

        <div v-if="archiveLoading" v-loading="true" class="sandbox-loading">
          <span class="loading-text">正在读取归档配置…</span>
        </div>
        <template v-else>
          <el-form label-position="top" class="sandbox-grid">
            <el-form-item>
              <template #label>
                <span class="label-line">
                  自动归档天数
                  <InfoHint
                    title="自动归档天数"
                    text="会话超过这么多天没有新记录时，会在你进入对话页的那一刻自动归档。检测只在进入对话页时执行一次，不会在后台常驻计时器。填 0 表示关闭自动归档；置顶会话不会被自动归档。"
                  />
                </span>
              </template>
              <el-input-number
                v-model="archiveDays"
                :min="0"
                :max="3650"
                controls-position="right"
                class="fill"
              />
            </el-form-item>
          </el-form>

          <div class="foot" :class="{ warn: Boolean(archiveError) }">
            <el-icon>
              <Warning v-if="archiveError" />
              <FolderOpened v-else />
            </el-icon>
            <span v-if="archiveError">{{ archiveError }}</span>
            <span v-else>默认 30 天；0 表示关闭。归档会话可在对话页左下角的「归档会话」中恢复或永久删除。</span>
          </div>
        </template>
      </section>

      <!-- 沙箱服务配置：未配置时给出安全提示 -->
      <section class="settings-module">
        <header class="module-head">
          <span class="module-icon"><el-icon><Lock /></el-icon></span>
          <div class="module-heading">
            <h2>沙箱服务</h2>
            <p>OpenSandbox 地址与生命周期配置，按当前用户保存并热更新</p>
          </div>
          <el-tag size="small" effect="plain" :type="sandbox?.configured ? 'success' : 'warning'">
            {{ sandboxLoading ? '读取中…' : sandbox?.configured ? '沙箱模式' : '项目工作区模式' }}
          </el-tag>
        </header>

        <div v-if="!sandboxLoading && !sandbox?.configured" class="risk-banner">
          <el-icon><Warning /></el-icon>
          <div>
            <strong>未配置沙箱服务，Agent 只能读写项目文件。</strong>
            <span>
              项目内允许创建代码与脚本，但不会在本机执行；所有命令执行请求都会被拒绝。
              需要运行命令时请配置 OpenSandbox。产物落点由当前项目目录决定。
            </span>
          </div>
        </div>

        <el-form v-loading="sandboxLoading" label-position="top" class="sandbox-grid">
          <el-form-item label="服务地址">
            <el-input v-model="form.host" placeholder="127.0.0.1" spellcheck="false" />
          </el-form-item>
          <el-form-item label="服务端口">
            <el-input-number
              v-model="form.port"
              :min="1"
              :max="65535"
              controls-position="right"
              class="fill"
            />
          </el-form-item>
          <el-form-item>
            <template #label>
              <span class="label-line">
                API Key
                <em>{{ sandbox?.has_api_key ? '已配置，留空保持不变' : '未配置' }}</em>
              </span>
            </template>
            <el-input
              v-model="form.apiKey"
              type="password"
              show-password
              placeholder="留空则保持现有密钥"
              autocomplete="new-password"
              spellcheck="false"
              :disabled="clearKey"
            />
            <el-button
              v-if="sandbox?.has_api_key"
              link
              type="primary"
              size="small"
              class="inline-link"
              @click="clearApiKey"
            >
              {{ clearKey ? '取消清空' : '清空密钥' }}
            </el-button>
          </el-form-item>
          <el-form-item label="沙箱镜像">
            <el-input v-model="form.image" placeholder="myagent-sandbox:1" spellcheck="false" />
          </el-form-item>
          <el-form-item label="单次命令超时（秒）">
            <el-input-number
              v-model="form.defaultTimeout"
              :min="10"
              :max="3600"
              controls-position="right"
              class="fill"
            />
          </el-form-item>
          <el-form-item label="沙箱生命周期（秒）">
            <el-input-number
              v-model="form.lifetime"
              :min="600"
              :max="30 * 86400"
              controls-position="right"
              class="fill"
            />
          </el-form-item>
        </el-form>

        <ul class="sandbox-switches">
          <li>
            <div class="text">
              <strong>启动预热</strong>
              <span>服务启动时创建一个备用沙箱，首次对话更快；失败会自动退回按需创建</span>
            </div>
            <el-switch
              :model-value="form.prewarm"
              @update:model-value="(v: string | number | boolean) => (form.prewarm = Boolean(v))"
            />
          </li>
          <li>
            <div class="text">
              <strong>通过生命周期服务端代理</strong>
              <span>Windows / Docker Desktop / 远程场景建议开启，客户端不直连沙箱容器 IP</span>
            </div>
            <el-switch
              :model-value="form.useServerProxy"
              @update:model-value="
                (v: string | number | boolean) => (form.useServerProxy = Boolean(v))
              "
            />
          </li>
        </ul>

        <div class="sandbox-actions">
          <el-button :icon="Monitor" :loading="sandboxChecking" @click="checkPort">
            {{ sandboxChecking ? '检测中…' : '检测连接' }}
          </el-button>
          <el-button type="primary" :loading="sandboxSaving" @click="saveSandbox">
            {{ sandboxSaving ? '保存中…' : '保存并重连' }}
          </el-button>
        </div>

        <el-alert
          v-if="sandboxError"
          :title="sandboxError"
          type="error"
          :closable="false"
          show-icon
          class="sandbox-note"
        />

        <div class="runtime-box">
          <el-button text class="runtime-head" @click="showRuntimes = !showRuntimes">
            <el-icon><Cpu /></el-icon>
            <span class="runtime-label">
              本机运行环境检测
              <em v-if="localEnv"> · 可用 {{ localEnv.runnable }} 项</em>
            </span>
            <el-icon class="chev" :class="{ open: showRuntimes }"><ArrowRight /></el-icon>
          </el-button>
          <ul v-if="showRuntimes && localEnv" class="runtime-list">
            <li v-for="item in localEnv.items" :key="item.command">
              <span class="runtime-name">{{ item.label }}</span>
              <span class="runtime-version">
                {{ item.available ? item.version || '已安装' : '未检测到' }}
              </span>
              <el-tag
                size="small"
                effect="plain"
                :type="item.available && item.satisfied ? 'success' : 'warning'"
              >
                {{ item.available ? (item.satisfied ? '可用' : '版本过低') : '缺失' }}
              </el-tag>
              <a
                v-if="!item.available || !item.satisfied"
                :href="item.download"
                target="_blank"
                rel="noreferrer"
              >安装</a>
            </li>
          </ul>
          <p v-if="showRuntimes && !localEnv" class="runtime-empty">正在检测本机环境…</p>
        </div>

        <div v-if="paths" class="paths-box">
          <header>
            <el-icon><Folder /></el-icon>
            存储位置
          </header>
          <div class="path-row">
            <span>数据与配置</span>
            <code>{{ paths.workspace_root }}</code>
          </div>
          <div class="path-row">
            <span>MCP 配置</span>
            <code>{{ paths.mcp_config }}</code>
          </div>
          <div class="path-row">
            <span>技能库</span>
            <code>{{ paths.skills_root }}</code>
          </div>
          <p v-if="paths.fallback" class="path-warn">
            默认目录 <code>{{ paths.preferred_dir }}</code> 不可写（{{ paths.error }}），
            已自动降级到 <code>{{ paths.user_data_dir }}</code>。
            如需写入 AppData，请检查受控文件夹访问/目录权限，或改用普通用户身份运行。
          </p>
        </div>

        <div class="foot">
          <el-icon><Lock /></el-icon>
          未配置沙箱时，Agent 只能读写项目文件，命令执行会被拒绝；
          项目内可以创建代码与脚本，但绝不会在本机执行。
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  width: min(1280px, 100%);
  margin: 0 auto;
  padding: 34px 40px 84px;
}

.settings-intro {
  padding-bottom: 26px;
}

.intro-kicker {
  margin: 0 0 10px;
  font-size: 9px;
  letter-spacing: 0.24em;
  color: var(--accent-cyan);
}

.settings-intro h1 {
  margin: 0 0 8px;
  font-size: clamp(28px, 3vw, 38px);
  font-weight: 600;
  letter-spacing: -0.035em;
  line-height: 1.05;
}

.settings-intro p {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-3);
}

.settings-module {
  padding: 30px 0 36px;
  border-top: 1px solid var(--line);
  background: transparent;
}

.settings-module + .settings-module {
  margin-top: 8px;
}

.module-head {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-bottom: 18px;
}

.module-icon {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: none;
  color: var(--accent-cyan);
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: transparent;
}

.module-heading {
  flex: 1;
  min-width: 0;
}

.module-heading h2 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.1;
}

.module-heading p {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-3);
}

.rows {
  list-style: none;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 42px;
  margin: 0;
  padding: 0;
}

.rows li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 15px 0;
  border-bottom: 1px solid var(--line);
}

.rows li:last-child {
  border-bottom: none;
}

.text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  color: var(--accent-cyan);
}

.loading-text {
  font-size: 11px;
  color: var(--text-3);
}

.text strong {
  font-size: 13px;
}

.text span {
  font-size: 11.5px;
  color: var(--text-3);
  line-height: 1.5;
}

.foot {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 16px 0 0;
  padding: 14px 0 0;
  font-size: 11px;
  color: var(--text-3);
  border-top: 1px solid var(--line);
}

.foot.warn {
  color: var(--amber);
}

/* ---------- 沙箱服务 ---------- */
.risk-banner {
  display: flex;
  gap: 10px;
  margin: 0 0 16px;
  padding: 12px 14px;
  color: var(--amber);
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: transparent;
}

.risk-banner div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.risk-banner strong {
  font-size: 12px;
  color: var(--amber);
}

.risk-banner span {
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-2);
}

.sandbox-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 20px;
}

.sandbox-grid :deep(.el-form-item) {
  margin-bottom: 14px;
}

.label-line {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.label-line em {
  font-style: normal;
  font-size: 10px;
  color: var(--text-3);
}

.fill {
  width: 100%;
}

.inline-link {
  align-self: flex-start;
  margin-top: 2px;
}

.sandbox-loading {
  min-height: 120px;
}

/* 压缩预算概览 */
.ctx-budget {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0 0 14px;
}

.ctx-budget span {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: transparent;
  font-size: 11px;
  color: var(--text-3);
}

.ctx-budget b {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-1);
}

.sandbox-switches {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
}

.sandbox-switches li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 0;
  border-bottom: 1px solid var(--line);
}

.sandbox-switches li:last-child {
  border-bottom: none;
}

.sandbox-switches .text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sandbox-switches strong {
  font-size: 12.5px;
}

.sandbox-switches span {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-3);
}

.sandbox-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 0 0;
}

.sandbox-note {
  margin: 12px 0 0;
}

.runtime-box {
  margin: 14px 0 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  overflow: hidden;
}

.runtime-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 9px 12px;
  font-size: 11.5px;
  color: var(--text-2);
  border: none;
  background: transparent;
}

.runtime-head:hover {
  background: var(--bg-hover);
}

.runtime-label {
  flex: 1;
  text-align: left;
}

.runtime-label em {
  font-style: normal;
  font-size: 10.5px;
  color: var(--text-3);
}

.chev {
  display: grid;
  place-items: center;
  transition: transform 0.2s;
  color: var(--text-3);
}

.chev.open {
  transform: rotate(90deg);
}

.runtime-list {
  list-style: none;
  margin: 0;
  padding: 2px 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.runtime-list li {
  display: grid;
  grid-template-columns: 110px 1fr 80px 46px;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 11px;
}

.runtime-name {
  color: var(--text-2);
}

.runtime-version {
  color: var(--text-3);
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10.5px;
}

.runtime-list a {
  font-size: 10.5px;
  color: var(--accent-cyan);
  text-decoration: none;
}

.runtime-list a:hover {
  text-decoration: underline;
}

.runtime-empty {
  margin: 0;
  padding: 0 12px 10px;
  font-size: 11px;
  color: var(--text-3);
}

/* ---------- 存储位置 ---------- */
.paths-box {
  margin: 14px 0 0;
  padding: 9px 12px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
}

.paths-box header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 7px;
  font-size: 11.5px;
  color: var(--text-2);
}

.path-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 3px 0;
  font-size: 10.5px;
}

.path-row span {
  flex: none;
  width: 72px;
  color: var(--text-3);
}

.path-row code {
  flex: 1;
  min-width: 0;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--text-2);
  word-break: break-all;
}

.path-warn {
  margin: 7px 0 0;
  padding-top: 7px;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--amber);
  border-top: 1px solid var(--line);
}

.path-warn code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--amber);
  word-break: break-all;
}

@media (max-width: 900px) {
  .page {
    padding: 24px 20px 64px;
  }

  .rows {
    grid-template-columns: 1fr;
  }

  .sandbox-grid {
    grid-template-columns: 1fr;
  }

  .module-heading h2 {
    font-size: 19px;
  }
}
</style>
