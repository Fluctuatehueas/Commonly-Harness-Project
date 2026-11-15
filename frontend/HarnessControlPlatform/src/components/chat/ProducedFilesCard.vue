<script setup lang="ts">
// 本轮产出文件卡片：横条 + 可展开列表；列表项点击交给上层打开右侧抽屉
import { computed, ref } from 'vue'
import { ArrowDown, ArrowRight } from '@element-plus/icons-vue'
import AppIcon from '@/components/console/AppIcon.vue'
import type { FileMeta } from '@/stores/files'

const props = defineProps<{ files: FileMeta[] }>()
const emit = defineEmits<{ 'open-file': [file: FileMeta] }>()

const expanded = ref(false)
const count = computed(() => props.files.length)

function fileName(path: string): string {
  return path.split('/').filter(Boolean).pop() || path
}

function dirName(path: string): string {
  const parts = path.split('/').filter(Boolean)
  parts.pop()
  return parts.length ? `/${parts.join('/')}` : '/'
}
</script>

<template>
  <div class="produced" :class="{ expanded }">
    <button type="button" class="produced-head" @click="expanded = !expanded">
      <span class="produced-icon"><AppIcon name="file" :size="15" /></span>
      <span class="produced-title">产出 {{ count }} 个文件，请查看</span>
      <el-icon class="produced-arrow">
        <ArrowDown v-if="expanded" />
        <ArrowRight v-else />
      </el-icon>
    </button>

    <div v-if="expanded" class="produced-list">
      <button
        v-for="file in files"
        :key="`${file.origin}:${file.path}`"
        type="button"
        class="produced-item"
        @click="emit('open-file', file)"
      >
        <span class="item-icon"><AppIcon name="file" :size="13" /></span>
        <span class="item-main">
          <span class="item-name">{{ fileName(file.path) }}</span>
          <span class="item-path">{{ dirName(file.path) }}</span>
        </span>
        <span class="item-origin" :class="file.origin">
          {{ file.origin === 'project' ? '本机' : '沙箱' }}
        </span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.produced {
  margin-top: 8px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(6, 18, 36, 0.72);
  width: 100%; /* 或者 width: 80%; */
  margin: 8px auto 0; /* 保证上下间距的同时，左右 auto 实现居中 */
}

.produced-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  font: inherit;
  color: var(--text-1);
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.produced-head:hover {
  background: rgba(53, 224, 255, 0.05);
}

.produced-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 26px;
  height: 26px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.08);
  border-radius: 7px;
}

.produced-title {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
}

.produced-arrow {
  flex: none;
  font-size: 13px;
  color: var(--text-3);
}

.produced-list {
  max-height: 240px;
  padding: 4px 6px 6px;
  overflow-y: auto;
  border-top: 1px solid var(--line);
}

.produced-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  font: inherit;
  color: var(--text-1);
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 7px;
}

.produced-item:hover {
  background: rgba(53, 224, 255, 0.06);
}

.item-icon {
  display: inline-flex;
  flex: none;
  color: var(--accent-cyan);
}

.item-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.item-name {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-path {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-3);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-origin {
  flex: none;
  padding: 1px 6px;
  font-size: 9.5px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 999px;
}

.item-origin.project {
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.08);
  border-color: rgba(53, 224, 255, 0.3);
}
</style>
