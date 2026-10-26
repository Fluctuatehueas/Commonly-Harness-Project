<script setup lang="ts">
// 首页：对话测试（顶栏菜单 + 左侧会话栏 + SSE 流式 + 文件预览）
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import ToolCallCard from '@/components/chat/ToolCallCard.vue'
import InterruptCard from '@/components/chat/InterruptCard.vue'
import SessionPanel from '@/components/chat/SessionPanel.vue'
import FilePanel from '@/components/chat/FilePanel.vue'
import MarkdownView from '@/components/chat/MarkdownView.vue'
import { API_BASE } from '@/api/client'
import {
  deleteContextFile,
  fetchContextFormats,
  importContextUrl,
  uploadContextFile,
  type ContextFile,
  type ContextFormats,
} from '@/api/context'
import { categoryOf, useChatStore, type ChatMsg } from '@/stores/chat'
import { USER_ID, useSessionStore } from '@/stores/sessions'
import { useDisplayStore } from '@/stores/display'

const chat = useChatStore()
const display = useDisplayStore()
const sessions = useSessionStore()

const draft = ref('')
const scroller = ref<HTMLElement | null>(null)
const showFiles = ref(false)
const fileRefreshKey = ref(0)
// null=检测中 true=在线 false=离线（离线时保持演示模式）
const backendOnline = ref<boolean | null>(null)

// ---------- 上下文附件 ----------
const formats = ref<ContextFormats>({
  accept: '',
  max_files: 6,
  max_file_mb: 20,
  groups: {},
})
const pendingFiles = ref<ContextFile[]>([])
const previews = ref<Record<string, string>>({})
const uploading = ref(false)
const uploadError = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
// 附件来源下拉：文件 / 图片 / 网络链接
const attachMenu = ref(false)
const fileAccept = ref('')
const urlMode = ref(false)
const urlDraft = ref('')
const urlLoading = ref(false)

const SUGGESTIONS = [
  '介绍一下 Harness 架构的六层设计',
  '帮我把“你好”写入 /notes.md 文件',
  '查看当前可用的技能',
  '演示一次人工介入流程',
]

onMounted(async () => {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(4000) })
    backendOnline.value = res.ok
  } catch {
    backendOnline.value = false
  }
  if (backendOnline.value) {
    chat.demoMode = false
    void sessions.refresh()
    formats.value = await fetchContextFormats()
  }
})

// ---------- 上传交互 ----------
// 下拉里的三种来源分别对应不同的 accept，避免选择框里出现无关文件
function attachAccept(source: 'file' | 'image'): string {
  const groups = formats.value.groups || {}
  if (source === 'image') {
    return (groups.image || []).join(',') || '.png,.jpg,.jpeg,.webp,.gif'
  }
  const exts = [...(groups.document || []), ...(groups.text || [])]
  return exts.join(',') || formats.value.accept
}

async function pickAttach(source: 'file' | 'image') {
  if (uploading.value || chat.sending) return
  attachMenu.value = false
  fileAccept.value = attachAccept(source)
  // 浏览器要求点击必须发生在用户手势里，等 input 的 accept 更新后再触发
  await nextTick()
  fileInput.value?.click()
}

function openUrlMode() {
  attachMenu.value = false
  uploadError.value = ''
  urlMode.value = true
}

async function submitUrl() {
  const url = urlDraft.value.trim()
  if (!url || urlLoading.value) return
  if (!/^https?:\/\//i.test(url)) {
    uploadError.value = '链接需要以 http:// 或 https:// 开头'
    return
  }
  if (pendingFiles.value.length >= formats.value.max_files) {
    uploadError.value = `一次最多上传 ${formats.value.max_files} 个文件`
    return
  }
  uploadError.value = ''
  urlLoading.value = true
  try {
    const record = await importContextUrl(url, USER_ID)
    pendingFiles.value.push(record)
    urlDraft.value = ''
    urlMode.value = false
  } catch (error) {
    uploadError.value = (error as Error).message
  } finally {
    urlLoading.value = false
  }
}

function clearPending() {
  for (const file of pendingFiles.value) {
    const url = previews.value[file.id]
    if (url) URL.revokeObjectURL(url)
    void deleteContextFile(file.id, USER_ID)
  }
  pendingFiles.value = []
  previews.value = {}
  uploadError.value = ''
}

async function onFilesPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  uploadError.value = ''
  if (files.length === 0) return

  const limit = formats.value.max_file_mb * 1024 * 1024
  const allowed = formats.value.accept
    ? formats.value.accept.split(',').map((item) => item.trim()).filter(Boolean)
    : []

  for (const file of files) {
    if (pendingFiles.value.length >= formats.value.max_files) {
      uploadError.value = `一次最多上传 ${formats.value.max_files} 个文件`
      break
    }
    if (file.size > limit) {
      uploadError.value = `${file.name} 超过 ${formats.value.max_file_mb} MB`
      continue
    }
    const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`
    if (allowed.length && !allowed.includes(ext)) {
      uploadError.value = `不支持的文件类型 ${ext}`
      continue
    }
    uploading.value = true
    try {
      const record = await uploadContextFile(file, USER_ID)
      pendingFiles.value.push(record)
      if (record.kind === 'image') {
        previews.value[record.id] = URL.createObjectURL(file)
      }
    } catch (error) {
      uploadError.value = (error as Error).message
    } finally {
      uploading.value = false
    }
  }
}

async function removePending(id: string) {
  pendingFiles.value = pendingFiles.value.filter((file) => file.id !== id)
  const url = previews.value[id]
  if (url) {
    URL.revokeObjectURL(url)
    delete previews.value[id]
  }
  await deleteContextFile(id, USER_ID)
}

// ---------- 会话切换 ----------
interface HistoryMessage {
  id: string
  role: string
  content?: string
  tool_name?: string
  tool_status?: string
  tool_call_id?: string
  text?: string
  args?: string
  source?: string
}

async function selectSession(threadId: string) {
  if (sessions.activeThreadId === threadId) return
  clearPending()
  sessions.activeThreadId = threadId
  try {
    const res = await fetch(
      `${API_BASE}/history/${threadId}/messages?user_id=${USER_ID}`,
    )
    if (!res.ok) throw new Error(String(res.status))
    const data = (await res.json()) as {
      messages: HistoryMessage[]
      interrupt: Record<string, unknown> | null
    }
    const msgs: ChatMsg[] = data.messages.map((m, i) => {
      if (m.role === 'user') return { id: `h-u-${i}`, kind: 'user', content: m.content }
      if (m.role === 'assistant') {
        return { id: m.id || `h-a-${i}`, kind: 'assistant', content: m.content }
      }
      const name = m.tool_name || 'tool'
      return {
        id: `h-t-${i}`,
        kind: 'tool',
        toolCallId: m.tool_call_id || m.id || `h-t-${i}`,
        toolName: name,
        category: categoryOf(name),
        args: m.args || '',
        result: m.text || '',
        status: m.tool_status === 'error' ? 'error' : m.tool_status === 'calling' ? 'calling' : 'done',
        source: m.source,
        subagentName: m.role === 'delegation' && m.source && m.source !== 'main' ? m.source : undefined,
      }
    })
    if (data.interrupt) {
      const it = data.interrupt as { interrupt_id?: string; interrupt_type?: string }
      msgs.push({
        id: `h-i-${Date.now()}`,
        kind: 'interrupt',
        interruptId: it.interrupt_id,
        interruptType: it.interrupt_type || 'unknown',
        payload: data.interrupt,
      })
    }
    chat.loadHistory(msgs, threadId)
    fileRefreshKey.value++
  } catch {
    chat.newChat()
  }
}

async function createSession() {
  clearPending()
  const id = await sessions.createSession()
  sessions.activeThreadId = id
  chat.newChat()
  fileRefreshKey.value++
}

async function removeSession(threadId: string) {
  await sessions.removeSession(threadId)
  if (sessions.activeThreadId === threadId) {
    sessions.activeThreadId = null
    chat.newChat()
  }
}

// 运行中 / 等待介入 标识同步到会话栏
watch(
  () => [chat.sending, sessions.activeThreadId, chat.threadId] as const,
  ([sending, active, tid]) => {
    const key = sending ? active || tid : null
    if (sending && key) sessions.markRunning(key, true)
    if (!sending) {
      if (active) sessions.markRunning(active, false)
      if (tid) sessions.markRunning(tid, false)
    }
  },
)

watch(
  () => [chat.pendingInterrupt, sessions.activeThreadId] as const,
  ([interrupt, active]) => {
    sessions.waitingThreads.forEach((t) => sessions.markWaiting(t, false))
    if (interrupt && active) sessions.markWaiting(active, true)
  },
)

// 一轮结束后刷新会话列表（标题/条数）与文件面板
watch(
  () => chat.sending,
  (now, prev) => {
    if (prev && !now && backendOnline.value) {
      void sessions.refresh()
      fileRefreshKey.value++
    }
  },
)

// ---------- 消息渲染 ----------
function visible(msg: ChatMsg): boolean {
  if (msg.kind !== 'tool') return true
  const prefs = display.prefs
  if (msg.category === 'skill' && !prefs.showSkills) return false
  if (msg.category === 'delegation') {
    const asyncTask = msg.toolName === 'start_async_task'
    if (asyncTask && !prefs.showAsyncTasks) return false
    if (!prefs.showDelegation) return false
  }
  if (msg.category === 'tool' && !prefs.showToolCalls) return false
  return true
}

const shown = computed(() => chat.messages.filter(visible))

const backendStatusLabel = computed(
  () => (backendOnline.value === null ? '检测后端…' : backendOnline.value ? '后端在线' : '后端离线'),
)
const backendStatusClass = computed(() =>
  backendOnline.value === null ? 'checking' : backendOnline.value ? 'online' : 'offline',
)

async function scrollBottom() {
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(() => [chat.messages.length, shown.value.map((m) => (m.content || '') + (m.result || '') + (m.status || ''))], scrollBottom, { deep: true })

function submit() {
  if (!draft.value.trim() && pendingFiles.value.length === 0) return
  const files = pendingFiles.value
  chat.send(draft.value, files)
  draft.value = ''
  // 已发送的附件不再重复引用；预览 URL 保留给消息气泡显示。
  pendingFiles.value = []
  uploadError.value = ''
}
</script>

<template>
  <div class="app-shell">
    <TopNav>
      <template #actions>
        <span class="status-chip" :class="backendStatusClass">
          <i class="dot"></i>{{ backendStatusLabel }}
        </span>
        <span class="demo-toggle" title="后端未启动时用脚本事件预览全部 UI；后端在线时自动关闭">
          <UiSwitch v-model="chat.demoMode" />
          <em>演示模式</em>
        </span>
      </template>
    </TopNav>

    <div class="chat-layout">
      <SessionPanel
        @select="selectSession"
        @create="createSession"
        @remove="removeSession"
      />

      <section class="panel chat">
        <header class="chat-head">
          <div class="title">
            <span class="title-icon"><AppIcon name="message" :size="14" /></span>
            <div>
              <strong>对话测试</strong>
              <small>SSE 流式 · 工具 / 技能可视化 · HITL 人工介入</small>
            </div>
          </div>
          <div class="controls">
            <span v-if="chat.threadId" class="chip">thread {{ chat.threadId.slice(0, 8) }}…</span>
            <button class="ghost" type="button" @click="showFiles = !showFiles">
              <AppIcon name="file" :size="13" /> 文件
            </button>
            <button class="ghost" type="button" @click="createSession()">
              <AppIcon name="plus" :size="13" /> 新会话
            </button>
          </div>
        </header>

        <div ref="scroller" class="messages">
          <div v-if="shown.length === 0" class="empty">
            <div class="empty-orb"><AppIcon name="sparkles" :size="26" /></div>
            <h3>Harness 对话测试台</h3>
            <p>发送一条消息，观察模型推理、工具调用、技能使用与人工介入的完整链路。</p>
            <div class="suggestions">
              <button v-for="s in SUGGESTIONS" :key="s" type="button" @click="chat.send(s)">{{ s }}</button>
            </div>
          </div>

          <template v-for="msg in shown" :key="msg.id">
            <div v-if="msg.kind === 'user'" class="row user">
              <div class="bubble user-bubble">
                <div v-if="msg.attachments?.length" class="bubble-atts">
                  <span v-for="att in msg.attachments" :key="att.id" class="bubble-att">
                    <img v-if="previews[att.id]" :src="previews[att.id]" alt="" />
                    <AppIcon v-else name="file" :size="11" />
                    <span>{{ att.name }}</span>
                  </span>
                </div>
                <span v-if="msg.content">{{ msg.content }}</span>
              </div>
            </div>

            <div v-else-if="msg.kind === 'assistant'" class="row">
              <div class="avatar ai">AI</div>
              <div class="bubble ai-bubble">
                <MarkdownView v-if="msg.content" :content="msg.content" />
                <span v-if="msg.streaming" class="cursor"></span>
              </div>
            </div>

            <div v-else-if="msg.kind === 'tool'" class="row indent">
              <ToolCallCard :msg="msg" />
            </div>

            <div v-else-if="msg.kind === 'interrupt'" class="row indent">
              <InterruptCard :msg="msg" />
            </div>

            <div v-else class="row center">
              <span class="system-note">{{ msg.content }}</span>
            </div>
          </template>

          <div v-if="chat.thinking && display.prefs.showReasoning" class="row">
            <div class="avatar ai">AI</div>
            <div class="bubble thinking-bubble">
              <i class="s-dot"></i><i class="s-dot"></i><i class="s-dot"></i>
              <span>模型推理中…</span>
            </div>
          </div>
          <div v-else-if="chat.thinking" class="row center">
            <span class="system-note">模型处理中…</span>
          </div>
        </div>

        <footer class="input-bar">
          <div v-if="pendingFiles.length" class="pending-row">
            <span v-for="att in pendingFiles" :key="att.id" class="pending-chip">
              <img v-if="previews[att.id]" :src="previews[att.id]" alt="" />
              <AppIcon v-else name="file" :size="12" />
              <span class="p-name">{{ att.name }}</span>
              <button type="button" title="移除" @click="removePending(att.id)">
                <AppIcon name="close" :size="10" />
              </button>
            </span>
          </div>
          <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>

          <div v-if="urlMode" class="url-row">
            <AppIcon name="globe" :size="14" />
            <input
              v-model="urlDraft"
              type="url"
              placeholder="https://example.com/article"
              :disabled="urlLoading"
              @keydown.enter.prevent="submitUrl"
              @keydown.esc="urlMode = false"
            />
            <button
              class="url-ok"
              type="button"
              :disabled="urlLoading || !urlDraft.trim()"
              @click="submitUrl"
            >
              {{ urlLoading ? '抓取中…' : '导入' }}
            </button>
            <button class="url-cancel" type="button" title="取消" @click="urlMode = false">
              <AppIcon name="close" :size="11" />
            </button>
          </div>

          <div class="input-row">
            <div class="attach-wrap">
              <button
                class="attach"
                :class="{ active: attachMenu }"
                type="button"
                :disabled="uploading || chat.sending"
                title="添加上下文：文件 / 图片 / 网络链接"
                @click="attachMenu = !attachMenu"
              >
                <AppIcon name="paperclip" :size="17" />
                <span v-if="uploading" class="attach-spin"></span>
              </button>
              <Transition name="pop">
                <div v-if="attachMenu" class="attach-menu">
                  <button type="button" @click="pickAttach('file')">
                    <AppIcon name="file" :size="14" />
                    <span>文件</span>
                    <small>文档 / 文本</small>
                  </button>
                  <button type="button" @click="pickAttach('image')">
                    <AppIcon name="image" :size="14" />
                    <span>图片</span>
                    <small>随消息多模态发送</small>
                  </button>
                  <button type="button" @click="openUrlMode">
                    <AppIcon name="globe" :size="14" />
                    <span>网络链接</span>
                    <small>抓取网页正文</small>
                  </button>
                </div>
              </Transition>
            </div>
            <div v-if="attachMenu" class="menu-mask" @click="attachMenu = false"></div>
            <input
              ref="fileInput"
              class="file-input"
              type="file"
              multiple
              :accept="fileAccept || formats.accept"
              @change="onFilesPicked"
            />
            <textarea
              v-model="draft"
              rows="1"
              placeholder="输入消息，可附图片/文档/网页链接；Enter 发送 / Shift+Enter 换行"
              :disabled="chat.sending"
              @keydown.enter.exact.prevent="submit"
            ></textarea>
            <button v-if="chat.sending" class="stop" type="button" title="中止" @click="chat.stop()">
              <AppIcon name="stop" :size="15" />
            </button>
            <button
              v-else
              class="send"
              type="button"
              :disabled="(!draft.trim() && pendingFiles.length === 0) || uploading"
              @click="submit"
            >
              <AppIcon name="send" :size="15" />
            </button>
          </div>
        </footer>
      </section>

      <Transition name="slide">
        <FilePanel
          v-if="showFiles"
          :thread-id="chat.threadId"
          :refresh-key="fileRefreshKey"
          @close="showFiles = false"
        />
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.chat-layout {
  flex: 1;
  display: flex;
  gap: 14px;
  align-items: stretch;
  height: calc(100vh - var(--topbar-h));
  padding: 14px 18px 18px;
  min-width: 0;
}

.chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  max-height: 90vh;
}

.chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 13px 18px;
  border-bottom: 1px solid var(--line);
}

.title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.1);
}

.title strong {
  display: block;
  font-size: 15px;
}

.title small {
  font-size: 11px;
  color: var(--text-3);
}

.controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 11px;
  font-size: 11px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(16, 32, 64, 0.5);
  color: var(--text-2);
}

.status-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status-chip.online {
  color: var(--green);
  border-color: rgba(61, 220, 132, 0.35);
}

.status-chip.online .dot {
  box-shadow: 0 0 6px var(--green);
}

.status-chip.offline {
  color: var(--amber);
  border-color: rgba(255, 182, 72, 0.35);
}

.status-chip.checking {
  color: var(--text-3);
}

.demo-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.demo-toggle em {
  font-style: normal;
  font-size: 11.5px;
  color: var(--text-2);
}

.chip {
  padding: 4px 10px;
  font-size: 10.5px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(16, 32, 64, 0.5);
}

.ghost {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  transition: all 0.18s;
}

.ghost:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty {
  margin: auto;
  max-width: 460px;
  text-align: center;
}

.empty-orb {
  display: grid;
  place-items: center;
  width: 58px;
  height: 58px;
  margin: 0 auto 14px;
  border-radius: 50%;
  color: var(--accent-cyan);
  background: radial-gradient(circle at 35% 30%, rgba(53, 224, 255, 0.25), rgba(30, 70, 150, 0.25));
  box-shadow: 0 0 28px rgba(53, 224, 255, 0.25);
}

.empty h3 {
  margin: 0 0 6px;
  font-size: 17px;
}

.empty p {
  margin: 0 0 16px;
  font-size: 12.5px;
  color: var(--text-3);
}

.suggestions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}

.suggestions button {
  padding: 7px 13px;
  font-size: 12px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(16, 32, 64, 0.5);
  transition: all 0.18s;
}

.suggestions button:hover {
  color: var(--text-1);
  border-color: var(--accent);
  background: rgba(59, 120, 255, 0.12);
}

.row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  animation: fadein 0.3s ease;
}

@keyframes fadein {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}

.row.user {
  justify-content: flex-end;
}

.row.indent {
  padding-left: 40px;
}

.row.center {
  justify-content: center;
}

.avatar {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  flex: none;
}

.avatar.ai {
  color: #cfe3ff;
  background: linear-gradient(135deg, #2f6bff, #16306e);
  box-shadow: 0 0 12px rgba(63, 122, 255, 0.35);
}

.bubble {
  max-width: 72%;
  padding: 10px 14px;
  font-size: 13px;
  line-height: 1.7;
  border-radius: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}

.ai-bubble {
  border: 1px solid var(--line);
  background: var(--bg-card);
  border-top-left-radius: 4px;
  /* Markdown 渲染后由块级元素控制换行，不能再沿用 pre-wrap */
  white-space: normal;
}

.user-bubble {
  color: #eaf2ff;
  background: linear-gradient(120deg, #2f6bff, #2451b8);
  border-top-right-radius: 4px;
  box-shadow: 0 4px 16px rgba(47, 100, 220, 0.3);
}

.cursor {
  display: inline-block;
  width: 7px;
  height: 15px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--accent-cyan);
  animation: blink 0.9s steps(1) infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.thinking-bubble {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-2);
  font-size: 12px;
}

.s-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-cyan);
  animation: bounce 1.2s infinite;
}

.s-dot:nth-child(2) { animation-delay: 0.15s; }
.s-dot:nth-child(3) { animation-delay: 0.3s; }

@keyframes bounce {
  0%, 60%, 100% { transform: none; opacity: 0.5; }
  30% { transform: translateY(-4px); opacity: 1; }
}

.system-note {
  font-size: 11.5px;
  color: var(--text-3);
}

.input-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  background: rgba(8, 16, 34, 0.6);
}

.input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

/* 附件选择：原生 input 隐藏，按钮触发 */
.file-input {
  display: none;
}

.attach {
  position: relative;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  flex: none;
  color: var(--text-2);
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: rgba(6, 13, 28, 0.7);
  transition: all 0.18s;
}

/* 附件下拉容器：菜单向上弹出，避免遮挡输入框 */
.attach-wrap {
  position: relative;
  flex: none;
}

.attach.active {
  color: var(--accent-cyan);
  border-color: var(--accent);
  background: rgba(53, 224, 255, 0.1);
}

.attach-menu {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 0;
  z-index: 41;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 210px;
  padding: 6px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: rgba(10, 22, 44, 0.97);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(12px);
}

.attach-menu button {
  display: grid;
  grid-template-columns: 18px 1fr;
  grid-template-rows: auto auto;
  column-gap: 8px;
  align-items: center;
  padding: 8px 10px;
  text-align: left;
  color: var(--text-2);
  border: none;
  border-radius: 8px;
  background: transparent;
  transition: all 0.15s;
}

.attach-menu button:hover {
  color: var(--text-1);
  background: rgba(53, 224, 255, 0.09);
}

.attach-menu button svg,
.attach-menu button :deep(svg) {
  grid-row: 1 / span 2;
  color: var(--accent-cyan);
}

.attach-menu button span {
  font-size: 12.5px;
}

.attach-menu button small {
  font-size: 10.5px;
  color: var(--text-3);
}

/* 点击空白关闭下拉 */
.menu-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
}

/* 网络链接输入行 */
.url-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  color: var(--accent-cyan);
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: rgba(6, 13, 28, 0.78);
}

.url-row input {
  flex: 1;
  min-width: 0;
  padding: 2px 4px;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--text-1);
  border: none;
  background: transparent;
  outline: none;
}

.url-row input::placeholder {
  color: var(--text-3);
}

.url-ok {
  flex: none;
  padding: 4px 12px;
  font-size: 11.5px;
  color: #fff;
  border: none;
  border-radius: 7px;
  background: linear-gradient(120deg, #3b78ff, #2a5cd6);
}

.url-ok:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.url-cancel {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  flex: none;
  color: var(--text-3);
  border: none;
  border-radius: 5px;
  background: transparent;
}

.url-cancel:hover {
  color: #ff6b6b;
  background: rgba(255, 93, 93, 0.14);
}

.pop-enter-active,
.pop-leave-active {
  transition: all 0.16s ease;
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.97);
}

.attach:hover:not(:disabled) {
  color: var(--accent-cyan);
  border-color: var(--accent);
  background: rgba(53, 224, 255, 0.08);
}

.attach:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.attach-spin {
  position: absolute;
  width: 34px;
  height: 34px;
  border: 2px solid transparent;
  border-top-color: var(--accent-cyan);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.pending-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pending-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px 3px 6px;
  font-size: 11px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(16, 32, 64, 0.55);
}

.pending-chip img {
  width: 24px;
  height: 24px;
  object-fit: cover;
  border-radius: 4px;
}

.pending-chip .p-name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-chip button {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  color: var(--text-3);
  border: none;
  border-radius: 4px;
  background: transparent;
}

.pending-chip button:hover {
  color: #ff6b6b;
  background: rgba(255, 93, 93, 0.14);
}

.upload-error {
  margin: 0;
  font-size: 11px;
  color: var(--amber);
}

.bubble-atts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.bubble-att {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px 3px 4px;
  font-size: 10.5px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.18);
}

.bubble-att img {
  width: 26px;
  height: 26px;
  object-fit: cover;
  border-radius: 4px;
}

.input-row textarea {
  flex: 1;
  max-height: 120px;
  padding: 10px 14px;
  font-size: 13px;
  font-family: inherit;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: rgba(6, 13, 28, 0.7);
  resize: none;
  outline: none;
}

.input-row textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(79, 140, 255, 0.15);
}

.send,
.stop {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 12px;
  color: #fff;
  transition: all 0.18s;
  flex: none;
}

.send {
  background: linear-gradient(120deg, #3b78ff, #2a5cd6);
  box-shadow: 0 4px 14px rgba(50, 110, 255, 0.4);
}

.send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.stop {
  background: linear-gradient(120deg, #ff5d5d, #c8302f);
  box-shadow: 0 4px 14px rgba(255, 93, 93, 0.35);
}

.slide-enter-active,
.slide-leave-active {
  transition: all 0.25s ease;
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
</style>
