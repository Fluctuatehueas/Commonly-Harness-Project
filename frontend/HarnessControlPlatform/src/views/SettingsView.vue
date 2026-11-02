<script setup lang="ts">
// 系统设置：对话展示控制 + 后端运行参数（思考 / 联网搜索）+ 沙箱服务配置
import { onMounted, reactive, ref } from 'vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import { useDisplayStore } from '@/stores/display'
import { useSettingsStore } from '@/stores/settings'
import {
  checkSandboxPort,
  fetchSandboxSettings,
  updateSandboxSettings,
  type LocalEnvironment,
  type SandboxSettings,
  type StoragePaths,
} from '@/api/settings'
import type { IconName } from '@/components/console/icons'

const display = useDisplayStore()
const settings = useSettingsStore()

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
const sandboxMessage = ref('')
const showRuntimes = ref(false)
const clearKey = ref(false)

const form = reactive({
  host: '127.0.0.1',
  port: 18083,
  apiKey: '',
  image: 'myagent-sandbox:1',
  defaultTimeout: 120,
  lifetime: 86400,
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
  sandboxMessage.value = ''
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
    sandboxMessage.value = '沙箱配置已保存并重建管理器，下一轮对话生效。'
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
  sandboxMessage.value = ''
  try {
    const result = await checkSandboxPort(form.host.trim(), Number(form.port))
    if (result.reachable) {
      sandboxMessage.value = result.message
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

onMounted(() => {
  void settings.load()
  void loadSandbox()
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
          <span class="module-icon"><AppIcon name="sliders" :size="16" /></span>
          <div class="module-heading">
            <h2>对话控制</h2>
            <p>配置对话页轨迹树的可见内容</p>
          </div>
          <button class="reset" type="button" @click="display.reset()">恢复默认</button>
        </header>

          <ul class="rows">
            <li v-for="row in ROWS" :key="String(row.key)">
              <span class="icon"><AppIcon :name="row.icon" :size="15" /></span>
              <div class="text">
                <strong>{{ row.label }}</strong>
                <span>{{ row.desc }}</span>
              </div>
              <UiSwitch v-model="display.prefs[row.key]" />
            </li>
          </ul>

          <div class="foot">
            <AppIcon name="shield" :size="13" />
            展示设置保存在浏览器本地（localStorage），仅影响当前控制台，不改变 Agent 的执行行为。
          </div>
      </section>

      <section class="settings-module">
        <header class="module-head">
          <span class="module-icon"><AppIcon name="cpu" :size="16" /></span>
          <div class="module-heading">
            <h2>对话运行参数</h2>
            <p>写入后端 .env 并热更新</p>
          </div>
        </header>

          <ul class="rows">
            <li>
              <span class="icon"><AppIcon name="sparkles" :size="15" /></span>
              <div class="text">
                <strong>思考模式</strong>
                <span>开启后模型返回推理内容（reasoning_content），轨迹树会流式展示</span>
              </div>
              <UiSwitch
                :model-value="settings.thinking"
                :disabled="settings.saving || settings.online === false"
                @update:model-value="settings.setThinking"
              />
            </li>
            <li>
              <span class="icon"><AppIcon name="globe" :size="15" /></span>
              <div class="text">
                <strong>联网搜索</strong>
                <span>开启后 Agent 绑定 search_web / fetch_web；关闭后不再下发搜索工具</span>
              </div>
              <UiSwitch
                :model-value="settings.webSearch"
                :disabled="settings.saving || settings.online === false"
                @update:model-value="settings.setWebSearch"
              />
            </li>
          </ul>

          <div class="foot" :class="{ warn: settings.online === false || settings.error }">
            <AppIcon :name="settings.online === false ? 'shield' : 'server'" :size="13" />
            <span v-if="settings.online === false">后端不可达：参数开关暂时不可写，请先启动 FastAPI。</span>
            <span v-else-if="settings.error">{{ settings.error }}</span>
            <span v-else>保存后会重置已缓存的 Agent 图，下一轮对话立即生效（不会中断正在进行的一轮）。</span>
          </div>
      </section>

      <!-- 沙箱服务配置：未配置时给出安全提示 -->
      <section class="settings-module">
        <header class="module-head">
          <span class="module-icon"><AppIcon name="shield" :size="16" /></span>
          <div class="module-heading">
            <h2>沙箱服务</h2>
            <p>OpenSandbox 地址与生命周期配置，写入 .env 并热更新</p>
          </div>
          <span class="mode-chip" :class="sandbox?.configured ? 'on' : 'off'">
            {{ sandboxLoading ? '读取中…' : sandbox?.configured ? '沙箱模式' : '项目工作区模式' }}
          </span>
        </header>

        <div v-if="!sandboxLoading && !sandbox?.configured" class="risk-banner">
          <AppIcon name="shield" :size="15" />
          <div>
            <strong>未配置沙箱服务，Agent 只能读写项目文件。</strong>
            <span>
              项目内允许创建代码与脚本，但不会在本机执行；所有命令执行请求都会被拒绝。
              需要运行命令时请配置 OpenSandbox。产物落点由当前项目目录决定。
            </span>
          </div>
        </div>

        <div class="sandbox-grid">
          <label class="field">
            <span>服务地址</span>
            <input v-model="form.host" type="text" placeholder="127.0.0.1" spellcheck="false" />
          </label>
          <label class="field">
            <span>服务端口</span>
            <input v-model.number="form.port" type="number" min="1" max="65535" placeholder="18083" />
          </label>
          <label class="field">
            <span>API Key <em>{{ sandbox?.has_api_key ? '已配置，留空保持不变' : '未配置' }}</em></span>
            <input
              v-model="form.apiKey"
              type="password"
              placeholder="留空则保持现有密钥"
              autocomplete="new-password"
              spellcheck="false"
              :disabled="clearKey"
            />
            <button
              v-if="sandbox?.has_api_key"
              class="inline-link"
              type="button"
              @click="clearApiKey"
            >
              {{ clearKey ? '取消清空' : '清空密钥' }}
            </button>
          </label>
          <label class="field">
            <span>沙箱镜像</span>
            <input v-model="form.image" type="text" placeholder="myagent-sandbox:1" spellcheck="false" />
          </label>
          <label class="field">
            <span>单次命令超时（秒）</span>
            <input v-model.number="form.defaultTimeout" type="number" min="10" max="3600" />
          </label>
          <label class="field">
            <span>沙箱生命周期（秒）</span>
            <input v-model.number="form.lifetime" type="number" min="600" :max="30 * 86400" />
          </label>
        </div>

        <ul class="sandbox-switches">
          <li>
            <div class="text">
              <strong>启动预热</strong>
              <span>服务启动时创建一个备用沙箱，首次对话更快；失败会自动退回按需创建</span>
            </div>
            <UiSwitch v-model="form.prewarm" />
          </li>
          <li>
            <div class="text">
              <strong>通过生命周期服务端代理</strong>
              <span>Windows / Docker Desktop / 远程场景建议开启，客户端不直连沙箱容器 IP</span>
            </div>
            <UiSwitch v-model="form.useServerProxy" />
          </li>
        </ul>

        <div class="sandbox-actions">
          <button class="ghost-btn" type="button" :disabled="sandboxChecking" @click="checkPort">
            <AppIcon name="server" :size="12" />
            {{ sandboxChecking ? '检测中…' : '检测连接' }}
          </button>
          <button class="primary" type="button" :disabled="sandboxSaving" @click="saveSandbox">
            {{ sandboxSaving ? '保存中…' : '保存并重连' }}
          </button>
        </div>

        <p v-if="sandboxError" class="sandbox-note err">{{ sandboxError }}</p>
        <p v-else-if="sandboxMessage" class="sandbox-note ok">{{ sandboxMessage }}</p>

        <div class="runtime-box">
          <button class="runtime-head" type="button" @click="showRuntimes = !showRuntimes">
            <AppIcon name="cpu" :size="13" />
            <span>
              本机运行环境检测
              <em v-if="localEnv"> · 可用 {{ localEnv.runnable }} 项</em>
            </span>
            <span class="chev" :class="{ open: showRuntimes }">
              <AppIcon name="chevronRight" :size="12" />
            </span>
          </button>
          <ul v-if="showRuntimes && localEnv" class="runtime-list">
            <li v-for="item in localEnv.items" :key="item.command">
              <span class="runtime-name">{{ item.label }}</span>
              <span class="runtime-version">
                {{ item.available ? item.version || '已安装' : '未检测到' }}
              </span>
              <span class="runtime-state" :class="item.available && item.satisfied ? 'ok' : 'bad'">
                {{ item.available ? (item.satisfied ? '可用' : '版本过低') : '缺失' }}
              </span>
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
            <AppIcon name="folder" :size="13" />
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
          <AppIcon name="shield" :size="13" />
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
  padding: 18px 22px 26px;
}

.columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  max-width: 1180px;
}

.panel-title small {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
}

.reset {
  padding: 5px 12px;
  font-size: 11.5px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color 0.16s, border-color 0.16s;
}

.reset:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.rows {
  list-style: none;
  margin: 8px 16px 0;
  padding: 0;
}

.rows li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--line);
}

.rows li:last-child {
  border-bottom: none;
}

.icon {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  color: var(--accent-cyan);
  background: transparent;
}

.text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
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
  margin: 10px 16px 14px;
  padding: 9px 0 0;
  font-size: 11px;
  color: var(--text-3);
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
}

.foot.warn {
  color: var(--amber);
}

/* ---------- 沙箱服务 ---------- */
.panel.sandbox {
  margin-top: 16px;
  max-width: 1180px;
}

.mode-chip {
  padding: 2px 8px;
  font-size: 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  color: var(--text-3);
  flex: none;
}

.mode-chip.on {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.32);
  background: transparent;
}

.mode-chip.off {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.32);
  background: transparent;
}

.risk-banner {
  display: flex;
  gap: 10px;
  margin: 10px 16px 0;
  padding: 10px 12px;
  color: var(--amber);
  border: 1px solid rgba(242, 176, 60, 0.28);
  border-radius: var(--radius-sm);
  background: rgba(242, 176, 60, 0.05);
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

.risk-banner code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10.5px;
  color: var(--accent-cyan);
}

.sandbox-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 14px;
  padding: 14px 16px 2px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 11.5px;
  color: var(--text-2);
}

.field span {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.field em {
  font-style: normal;
  font-size: 10px;
  color: var(--text-3);
}

.field input {
  width: 100%;
  padding: 8px 11px;
  font-size: 12px;
  font-family: inherit;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--bg-deep);
  outline: none;
}

.field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px rgba(91, 140, 255, 0.22);
}

.field input:disabled {
  opacity: 0.55;
}

.inline-link {
  align-self: flex-start;
  padding: 0;
  font-size: 10px;
  color: var(--accent-cyan);
  border: none;
  background: transparent;
}

.sandbox-switches {
  list-style: none;
  margin: 8px 16px 0;
  padding: 0;
}

.sandbox-switches li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 4px;
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
  padding: 10px 16px 0;
}

.sandbox-actions button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  font-size: 12px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
}

.ghost-btn {
  color: var(--text-2);
  border-color: var(--line-strong) !important;
  background: transparent;
}

.ghost-btn:hover:not(:disabled) {
  color: var(--text-1);
  border-color: var(--accent) !important;
}

.ghost-btn:disabled,
.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary {
  color: #fff;
  background: var(--accent);
}

.sandbox-note {
  margin: 10px 16px 0;
  padding: 8px 10px;
  font-size: 11px;
  border-radius: var(--radius-sm);
}

.sandbox-note.ok {
  color: var(--accent-cyan);
  border: 1px solid rgba(55, 208, 196, 0.28);
  background: transparent;
}

.sandbox-note.err {
  color: var(--danger);
  border: 1px solid rgba(226, 96, 94, 0.32);
  background: transparent;
}

.runtime-box {
  margin: 12px 16px 0;
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

.runtime-head span {
  flex: 1;
  text-align: left;
}

.runtime-head em {
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
  grid-template-columns: 110px 1fr 70px 46px;
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

.runtime-state {
  font-size: 10px;
  text-align: center;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
}

.runtime-state.ok {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.3);
}

.runtime-state.bad {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.32);
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
  margin: 12px 16px 0;
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
  border-top: 1px solid rgba(242, 176, 60, 0.3);
}

.path-warn code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--amber);
  word-break: break-all;
}

@media (max-width: 980px) {
  .columns {
    grid-template-columns: 1fr;
  }

  .sandbox-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

/* ---------- 整页宽：一个设置模块占满一行 ---------- */
.page {
  padding: 0 0 48px;
}

.columns,
.panel.sandbox {
  max-width: none;
  margin: 0;
}

.columns {
  display: flex;
  flex-direction: column;
  gap: 0;
  align-items: stretch;
}

.columns > .panel,
.page > .panel {
  width: 100%;
}

.panel-head {
  padding: 20px 32px 0;
}

/* 同一模块内部的开关用两列铺开，避免整行只有一条窄内容 */
.rows {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 32px;
  padding: 0 32px;
}

.rows li {
  min-width: 0;
}

.foot {
  margin: 0 32px 20px;
}

.sandbox-grid {
  padding: 14px 32px 8px;
}

.runtime-list {
  padding: 0 32px;
}

@media (max-width: 900px) {
  .rows {
    grid-template-columns: 1fr;
  }

  .panel-head,
  .rows,
  .sandbox-grid,
  .runtime-list {
    padding-left: 20px;
    padding-right: 20px;
  }

  .foot {
    margin-left: 20px;
    margin-right: 20px;
  }
}

/* ---------- 模块化设置布局 ---------- */
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
  color: rgba(55, 208, 196, 0.72);
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
  border: 1px solid rgba(55, 208, 196, 0.25);
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

.settings-module .rows {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 42px;
  margin: 0;
  padding: 0;
}

.settings-module .rows li {
  padding: 15px 0;
}

.settings-module .foot {
  margin: 16px 0 0;
  padding: 14px 0 0;
  border-top: 1px solid var(--line);
  font-size: 11px;
}

.settings-module .risk-banner {
  margin: 0 0 16px;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(242, 176, 60, 0.045);
}

.settings-module .sandbox-grid {
  padding: 0;
  gap: 16px 20px;
}

.settings-module .sandbox-switches {
  margin: 10px 0 0;
}

.settings-module .sandbox-actions {
  padding: 14px 0 0;
}

.settings-module .sandbox-note {
  margin: 12px 0 0;
}

.settings-module .runtime-box,
.settings-module .paths-box {
  margin: 14px 0 0;
}

.settings-module .field input {
  background: rgba(4, 10, 20, 0.38);
  border-color: rgba(126, 154, 198, 0.24);
  backdrop-filter: blur(6px);
}

.settings-module .mode-chip,
.settings-module .reset {
  border: 1px solid var(--line-strong);
  background: transparent;
}

@media (max-width: 900px) {
  .page {
    padding: 24px 20px 64px;
  }

  .settings-module .rows {
    grid-template-columns: 1fr;
  }

  .settings-module .sandbox-grid {
    grid-template-columns: 1fr;
  }

  .module-heading h2 {
    font-size: 19px;
  }
}
</style>
