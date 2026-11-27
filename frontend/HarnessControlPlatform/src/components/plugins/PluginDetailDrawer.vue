<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Close, Delete, Download, RefreshRight, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import type { PluginCardModel } from '@/types/plugins'

const props = defineProps<{
  modelValue: boolean
  item: PluginCardModel | null
  installed: boolean
  updateAvailable?: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [boolean]
  install: [PluginCardModel]
  toggle: [PluginCardModel, boolean]
  restart: [PluginCardModel]
  uninstall: [PluginCardModel]
  update: [PluginCardModel]
}>()

const width = ref(480)
const resizing = ref(false)
let startX = 0
let startWidth = 0

const enabled = computed(() => Boolean(props.item?.enabled))
const installable = computed(() => props.item?.installable !== false)
const unsupported = computed(() => Boolean(props.item?.unsupported))
const capabilities = computed(() => props.item?.capabilities || [])
const permissions = computed(() => props.item?.permissions || [])
const uiSlots = computed(() => props.item?.ui_slots || props.item?.ui?.slots || [])
const versions = computed(() => props.item?.versions || [])
const displayVersion = computed(
  () => props.item?.installed_version || props.item?.version,
)
const sourceLabel = computed(() => {
  const source = String(props.item?.source || '')
  const labels: Record<string, string> = {
    builtin: '系统内置',
    user: '用户安装',
    community: '社区',
    private: '私有市场',
    local: '本地目录',
    public: '公共市场',
    dsh: 'DSH 转译',
    remote: '远程服务',
    project: '项目级',
  }
  return labels[source] || source || '未知来源'
})

function onResizeMove(event: MouseEvent) {
  width.value = Math.min(820, Math.max(380, startWidth + (startX - event.clientX)))
}

function stopResize() {
  resizing.value = false
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
}

function startResize(event: MouseEvent) {
  event.preventDefault()
  resizing.value = true
  startX = event.clientX
  startWidth = width.value
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', stopResize)
}

function close() {
  emit('update:modelValue', false)
}

onBeforeUnmount(stopResize)
</script>

<template>
  <Teleport to="body">
    <Transition name="plugin-drawer">
      <div v-if="modelValue && item" class="drawer-mask" @click.self="close">
        <aside class="drawer" :class="{ resizing }" :style="{ width: `${width}px` }">
          <div class="resizer" title="拖动调整宽度" @mousedown="startResize" />

          <header class="drawer-head">
            <div>
              <div class="title-row">
                <h2 class="drawer-title">{{ item.name }}</h2>
                <span v-if="updateAvailable" class="update-pill">可更新</span>
              </div>
              <p class="drawer-sub mono">{{ item.plugin_id }} · v{{ displayVersion }}</p>
            </div>
            <el-button text circle :icon="Close" title="关闭" @click="close" />
          </header>

          <div class="drawer-body">
            <p class="desc">{{ item.description || item.summary || '暂无描述' }}</p>

            <el-alert
              v-if="unsupported"
              type="warning"
              :closable="false"
              show-icon
              :title="item.unsupported_reason || '本系统不支持该市场条目'"
            />
            <el-alert
              v-else-if="!installed && !installable"
              type="info"
              :closable="false"
              show-icon
              title="该条目不可直接安装"
            />

            <el-alert
              v-if="updateAvailable"
              type="success"
              :closable="false"
              show-icon
              :title="`当前 v${item.installed_version}，可更新到 v${item.version}`"
            />

            <section class="grid">
              <div class="cell">
                <span class="label">来源</span>
                <span class="value">{{ sourceLabel }}</span>
              </div>
              <div class="cell">
                <span class="label">协议版本</span>
                <span class="value mono">{{ item.protocolVersion || '-' }}</span>
              </div>
              <div class="cell">
                <span class="label">运行形态</span>
                <span class="value mono">{{ item.runtime?.kind || '-' }}</span>
              </div>
              <div class="cell">
                <span class="label">传输</span>
                <span class="value mono">{{ item.transport?.kind || '-' }}</span>
              </div>
              <div v-if="installed" class="cell">
                <span class="label">状态</span>
                <span class="value" :class="{ on: enabled }">{{ enabled ? '已启用' : '已停用' }}</span>
              </div>
              <div v-if="installed" class="cell">
                <span class="label">工具数</span>
                <span class="value mono">{{ item.tool_count ?? 0 }}</span>
              </div>
            </section>

            <section class="block">
              <h4>能力</h4>
              <div class="chips">
                <el-tag v-for="cap in capabilities" :key="cap" size="small" effect="plain">
                  {{ cap }}
                </el-tag>
                <span v-if="!capabilities.length" class="empty">无能力声明</span>
              </div>
            </section>

            <section class="block">
              <h4>权限</h4>
              <div class="chips">
                <el-tag
                  v-for="perm in permissions"
                  :key="perm"
                  size="small"
                  type="info"
                  effect="plain"
                >
                  {{ perm }}
                </el-tag>
                <span v-if="!permissions.length" class="empty">不需要额外权限</span>
              </div>
            </section>

            <section v-if="versions.length" class="block">
              <h4>版本</h4>
              <div class="chips">
                <span v-for="version in versions" :key="version" class="version mono">
                  v{{ version }}
                </span>
              </div>
            </section>

            <section v-if="uiSlots.length" class="block">
              <h4>UI 槽位</h4>
              <div class="chips">
                <span v-for="slot in uiSlots" :key="slot.slot" class="slot mono">
                  {{ slot.slot }} · {{ slot.entry }}
                </span>
              </div>
            </section>

            <section v-if="item.path" class="block">
              <h4>安装位置</h4>
              <p class="path mono">{{ item.path }}</p>
            </section>

            <section v-if="item.health" class="block">
              <h4>健康</h4>
              <p class="mono">
                {{ item.health.state }}
                <template v-if="item.health.latency_ms !== undefined">
                  · {{ item.health.latency_ms }} ms
                </template>
                <template v-if="item.health.error"> · {{ item.health.error }}</template>
              </p>
            </section>

            <section v-if="item.error" class="block">
              <h4>最近错误</h4>
              <p class="error-text">{{ item.error }}</p>
            </section>

            <section v-if="item.repository" class="block">
              <h4>仓库</h4>
              <a :href="item.repository" target="_blank" rel="noreferrer">{{ item.repository }}</a>
            </section>
          </div>

          <footer class="drawer-foot">
            <template v-if="installed">
              <el-button
                v-if="updateAvailable"
                type="primary"
                :icon="Download"
                @click="emit('update', item)"
              >
                更新到 v{{ item.version }}
              </el-button>
              <el-button
                :icon="enabled ? VideoPause : VideoPlay"
                @click="emit('toggle', item, !enabled)"
              >
                {{ enabled ? '停用' : '启用' }}
              </el-button>
              <el-button :icon="RefreshRight" @click="emit('restart', item)">重启</el-button>
              <el-button
                v-if="item.deletable !== false && !item.builtin"
                type="danger"
                plain
                :icon="Delete"
                @click="emit('uninstall', item)"
              >
                卸载
              </el-button>
            </template>
            <template v-else>
              <el-button
                type="primary"
                :icon="Download"
                :disabled="!installable || unsupported"
                @click="emit('install', item)"
              >
                {{ installable && !unsupported ? '安装插件' : '不可安装' }}
              </el-button>
            </template>
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  z-index: 2600;
  background: rgba(4, 8, 15, 0.5);
  display: flex;
  justify-content: flex-end;
}
.drawer {
  position: relative;
  max-width: 92vw;
  height: 100%;
  background: var(--bg-panel);
  border-left: 1px solid var(--line-strong);
  display: flex;
  flex-direction: column;
  box-shadow: -18px 0 40px rgba(0, 0, 0, 0.35);
}
.drawer.resizing {
  user-select: none;
}
.resizer {
  position: absolute;
  top: 0;
  left: -3px;
  width: 6px;
  height: 100%;
  cursor: col-resize;
  z-index: 3;
}
.resizer:hover {
  background: rgba(91, 140, 255, 0.25);
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 20px 12px;
  border-bottom: 1px solid var(--line);
}
.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.drawer-title {
  margin: 0;
  font-size: 17px;
  color: var(--text-1);
}
.update-pill {
  padding: 0 7px;
  height: 18px;
  line-height: 18px;
  border-radius: 9px;
  background: rgba(226, 96, 94, 0.16);
  border: 1px solid rgba(226, 96, 94, 0.45);
  color: var(--danger);
  font-size: 10px;
}
.drawer-sub {
  margin: 4px 0 0;
  color: var(--text-3);
  font-size: 11px;
}
.drawer-body {
  padding: 16px 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.desc {
  margin: 0;
  color: var(--text-2);
  line-height: 1.7;
  font-size: 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}
.label {
  color: var(--text-3);
  font-size: 10px;
}
.value {
  color: var(--text-1);
  font-size: 12px;
}
.value.on {
  color: var(--green);
}
.block h4 {
  margin: 0 0 7px;
  font-size: 12px;
  color: var(--text-2);
  font-weight: 500;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.version,
.slot {
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--line);
  color: var(--text-2);
  font-size: 10px;
}
.path {
  margin: 0;
  word-break: break-all;
  color: var(--text-2);
  font-size: 11px;
}
.error-text {
  margin: 0;
  color: var(--danger);
  font-size: 11px;
  word-break: break-all;
}
.empty {
  color: var(--text-3);
  font-size: 11px;
}
.drawer-foot {
  display: flex;
  gap: 8px;
  padding: 12px 20px 18px;
  border-top: 1px solid var(--line);
  justify-content: flex-end;
  flex-wrap: wrap;
}
.mono {
  font-family: var(--font-mono);
}
.plugin-drawer-enter-active,
.plugin-drawer-leave-active {
  transition: opacity 0.18s ease;
}
.plugin-drawer-enter-from,
.plugin-drawer-leave-to {
  opacity: 0;
}
a {
  color: var(--accent);
  font-size: 11px;
  word-break: break-all;
}
</style>
