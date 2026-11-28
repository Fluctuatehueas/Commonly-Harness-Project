<script setup lang="ts">
import { computed } from 'vue'
import {
  Delete,
  Download,
  MagicStick,
  RefreshRight,
  VideoPause,
  VideoPlay,
  View,
} from '@element-plus/icons-vue'
import type { PluginCardModel } from '@/types/plugins'

const props = defineProps<{
  item: PluginCardModel
  installed: boolean
  updateAvailable?: boolean
}>()
const emit = defineEmits<{
  detail: [PluginCardModel]
  install: [PluginCardModel]
  'import-skill': [PluginCardModel]
  toggle: [PluginCardModel, boolean]
  restart: [PluginCardModel]
  uninstall: [PluginCardModel]
  update: [PluginCardModel]
}>()

const capabilities = computed(() => props.item.capabilities || [])
const permissions = computed(() => props.item.permissions || [])
const sourceLabel = computed(() => {
  const source = String(props.item.source || '')
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
const installable = computed(() => props.item.installable !== false)
const unsupported = computed(() => Boolean(props.item.unsupported))
// DSH 条目不能直接运行，带 SKILL.md 的包可转译为本地技能
const dshSkill = computed(
  () =>
    props.item.source === 'dsh' &&
    props.item.import_kind === 'skill-import' &&
    props.item.importable !== false,
)
const enabled = computed(() => Boolean(props.item.enabled))
const displayVersion = computed(
  () => props.item.installed_version || props.item.version,
)
</script>

<template>
  <article
    class="plugin-card"
    :class="{ disabled: installed && !enabled, clickable: true }"
    @click="emit('detail', item)"
  >
    <span v-if="updateAvailable" class="update-dot" title="有新版本" />

    <header class="card-head">
      <div class="title-block">
        <div class="name-row">
          <h3 class="name">{{ item.name }}</h3>
          <span v-if="updateAvailable" class="update-pill">可更新</span>
          <el-tag size="small" effect="plain" class="source-tag">{{ sourceLabel }}</el-tag>
          <el-tag v-if="item.builtin" size="small" type="info" effect="plain">系统</el-tag>
          <el-tag v-if="unsupported" size="small" type="warning" effect="plain">不支持</el-tag>
        </div>
        <div class="meta-row">
          <span class="mono">v{{ displayVersion }}</span>
          <span v-if="updateAvailable" class="mono update-hint">
            → v{{ item.version }}
          </span>
          <span v-if="installed" class="mono state" :class="{ on: enabled }">
            {{ enabled ? '已启用' : '已停用' }}
          </span>
          <span v-if="item.author?.name" class="author">by {{ item.author.name }}</span>
        </div>
      </div>
      <el-button
        text
        circle
        :icon="View"
        title="详情"
        @click.stop="emit('detail', item)"
      />
    </header>

    <p class="desc">{{ item.description || item.summary || '暂无描述' }}</p>

    <div v-if="(item.tag || []).length" class="tags">
      <span v-for="tag in item.tag" :key="tag" class="tag"># {{ tag }}</span>
    </div>

    <div class="chips">
      <span v-for="cap in capabilities" :key="cap" class="cap" :class="`cap-${cap}`">
        {{ cap }}
      </span>
      <span v-if="capabilities.length === 0" class="cap cap-none">无能力声明</span>
    </div>

    <div v-if="permissions.length" class="permissions">
      <span class="perm-label">权限</span>
      <span v-for="perm in permissions" :key="perm" class="perm mono">{{ perm }}</span>
    </div>

    <footer class="card-actions" @click.stop>
      <template v-if="installed">
        <el-button
          v-if="updateAvailable"
          size="small"
          type="primary"
          :icon="Download"
          @click="emit('update', item)"
        >
          更新
        </el-button>
        <el-button
          size="small"
          :icon="enabled ? VideoPause : VideoPlay"
          @click="emit('toggle', item, !enabled)"
        >
          {{ enabled ? '停用' : '启用' }}
        </el-button>
        <el-button size="small" :icon="RefreshRight" @click="emit('restart', item)">
          重启
        </el-button>
        <el-button
          v-if="item.deletable !== false && !item.builtin"
          size="small"
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
          v-if="dshSkill"
          size="small"
          type="primary"
          :icon="MagicStick"
          @click="emit('import-skill', item)"
        >
          转译为技能
        </el-button>
        <el-button
          v-else
          size="small"
          type="primary"
          :icon="Download"
          :disabled="!installable || unsupported"
          @click="emit('install', item)"
        >
          {{ installable && !unsupported ? '安装' : '不可安装' }}
        </el-button>
      </template>
    </footer>
  </article>
</template>

<style scoped>
.plugin-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--bg-card);
  min-height: 190px;
  transition:
    border-color 0.16s ease,
    background 0.16s ease;
}
.plugin-card.clickable {
  cursor: pointer;
}
.plugin-card:hover {
  border-color: var(--line-strong);
  background: var(--bg-card-hover);
}
.plugin-card.disabled {
  opacity: 0.72;
}
.update-dot {
  position: absolute;
  top: 9px;
  right: 9px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--danger);
  box-shadow: 0 0 0 3px rgba(226, 96, 94, 0.18);
}
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding-right: 10px;
}
.name {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
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
.meta-row {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  color: var(--text-3);
  font-size: 11px;
}
.update-hint {
  color: var(--danger);
}
.state.on {
  color: var(--green);
}
.author {
  color: var(--text-3);
  font-size: 10px;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 10px;
  color: var(--text-3);
}
.tag {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: rgba(126, 154, 198, 0.08);
  color: var(--text-2);
}
.tag::first-letter {
  color: var(--text-3);
}
.desc {
  margin: 0;
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.cap {
  padding: 1px 7px;
  border-radius: 10px;
  border: 1px solid var(--line);
  font-size: 10px;
  color: var(--text-2);
  text-transform: lowercase;
}
.cap-tool {
  color: var(--accent);
  border-color: rgba(91, 140, 255, 0.35);
}
.cap-hook {
  color: var(--amber);
  border-color: rgba(242, 176, 60, 0.35);
}
.cap-ui {
  color: var(--accent-cyan);
  border-color: rgba(55, 208, 196, 0.35);
}
.cap-none {
  color: var(--text-3);
}
.permissions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  font-size: 10px;
  color: var(--text-3);
}
.perm {
  color: var(--text-2);
}
.mono {
  font-family: var(--font-mono);
}
.source-tag {
  font-size: 10px;
}
.card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: auto;
}
</style>
