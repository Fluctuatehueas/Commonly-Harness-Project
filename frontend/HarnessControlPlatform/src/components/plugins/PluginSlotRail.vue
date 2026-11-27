<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ArrowLeft, ArrowRight, Close } from '@element-plus/icons-vue'
import { API_BASE } from '@/api/client'
import { fetchPlugins, pluginUiUrl } from '@/api/plugins'
import type { PluginInstalledItem, PluginUISlot } from '@/types/plugins'

interface MountedSlot {
  plugin: PluginInstalledItem
  slot: PluginUISlot
  height: number
}

const props = withDefaults(defineProps<{ slot?: string }>(), { slot: 'chat.sidebar' })

const plugins = ref<PluginInstalledItem[]>([])
const collapsed = ref(false)
const heights = reactive<Record<string, number>>({})
const loading = ref(false)

const apiOrigin = (() => {
  try {
    return new URL(API_BASE).origin
  } catch {
    return ''
  }
})()

const mounted = computed<MountedSlot[]>(() => {
  const result: MountedSlot[] = []
  for (const plugin of plugins.value) {
    if (!plugin.enabled || !plugin.has_ui) continue
    for (const slot of plugin.ui_slots || []) {
      if (slot.slot !== props.slot) continue
      result.push({
        plugin,
        slot,
        height: heights[`${plugin.plugin_id}:${slot.entry}`] || 220,
      })
    }
  }
  return result
})

async function load() {
  loading.value = true
  try {
    const data = await fetchPlugins({ enabled: true })
    plugins.value = data.items
  } catch {
    // 聊天页的插件栏失败时静默，不影响主流程。
    plugins.value = []
  } finally {
    loading.value = false
  }
}

function onMessage(event: MessageEvent) {
  if (apiOrigin && event.origin !== apiOrigin) return
  const data = event.data
  if (!data || typeof data !== 'object') return
  if (data.type !== 'plugin:resize') return
  const pluginId = String(data.plugin_id || '')
  const entry = String(data.entry || '')
  const height = Number(data.height)
  if (!pluginId || !Number.isFinite(height)) return
  const key = `${pluginId}:${entry}`
  heights[key] = Math.max(80, Math.min(Math.round(height), 900))
}

function onPluginsChanged() {
  void load()
}

onMounted(() => {
  void load()
  window.addEventListener('message', onMessage)
  window.addEventListener('harness:plugins-changed', onPluginsChanged)
})

onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage)
  window.removeEventListener('harness:plugins-changed', onPluginsChanged)
})
</script>

<template>
  <aside v-if="mounted.length || loading" class="plugin-rail" :class="{ collapsed }">
    <button
      class="rail-toggle"
      type="button"
      :title="collapsed ? '展开插件面板' : '收起插件面板'"
      @click="collapsed = !collapsed"
    >
      <el-icon><ArrowLeft v-if="!collapsed" /><ArrowRight v-else /></el-icon>
    </button>

    <div v-if="!collapsed" class="rail-body">
      <header class="rail-head">
        <span>插件面板</span>
        <el-button
          text
          circle
          size="small"
          :icon="Close"
          title="隐藏面板"
          @click="collapsed = true"
        />
      </header>
      <div class="slot-list">
        <section v-for="entry in mounted" :key="`${entry.plugin.plugin_id}:${entry.slot.entry}`" class="slot">
          <div class="slot-title">
            <span class="name">{{ entry.slot.title || entry.plugin.name }}</span>
            <span class="id mono">{{ entry.plugin.plugin_id }}</span>
          </div>
          <iframe
            class="slot-frame"
            :src="pluginUiUrl(entry.plugin.plugin_id, entry.slot.entry)"
            :style="{ height: `${entry.height}px` }"
            sandbox="allow-scripts allow-forms"
            referrerpolicy="no-referrer"
            loading="lazy"
            :title="entry.slot.title || entry.plugin.name"
          />
        </section>
      </div>
    </div>

    <div v-else class="rail-collapsed-label">插件</div>
  </aside>
</template>

<style scoped>
.plugin-rail {
  position: relative;
  width: 300px;
  flex: 0 0 300px;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--line);
  background: var(--bg-rail);
  min-height: 0;
  transition: flex-basis 0.18s ease, width 0.18s ease;
}
.plugin-rail.collapsed {
  width: 34px;
  flex-basis: 34px;
}
.rail-toggle {
  position: absolute;
  top: 10px;
  left: -13px;
  z-index: 2;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  background: var(--bg-elevated);
  color: var(--text-2);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.rail-toggle:hover {
  color: var(--accent);
}
.rail-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
.rail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 10px 8px 14px;
  border-bottom: 1px solid var(--line);
  color: var(--text-2);
  font-size: 12px;
}
.slot-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px;
  overflow-y: auto;
}
.slot {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg-panel);
  overflow: hidden;
}
.slot-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--line);
  font-size: 11px;
}
.slot-title .name {
  color: var(--text-1);
}
.slot-title .id {
  color: var(--text-3);
  font-size: 10px;
}
.slot-frame {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
}
.rail-collapsed-label {
  writing-mode: vertical-rl;
  margin: 42px auto auto;
  color: var(--text-3);
  font-size: 11px;
  letter-spacing: 2px;
}
.mono {
  font-family: var(--font-mono);
}
</style>
