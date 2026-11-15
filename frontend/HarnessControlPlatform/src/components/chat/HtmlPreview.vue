<script setup lang="ts">
// HTML 预览：用 srcdoc + sandbox iframe 隔离执行
// 关键约束：sandbox 不给 allow-same-origin，脚本能跑但拿不到父页面、cookie、localStorage
// 只支持自包含单文件：外部依赖走 CDN，相对路径资源不可用
import { computed, ref } from 'vue'

const props = defineProps<{
  content: string
  /** 容器高度；抽屉内传 100%，气泡内嵌传固定像素 */
  height?: string
}>()

const frameKey = ref(0)
const srcDoc = computed(() => {
  const text = props.content || ''
  if (!text.trim()) return '<!doctype html><html><body></body></html>'
  return text
})

function reload() {
  frameKey.value += 1
}

defineExpose({ reload })
</script>

<template>
  <div class="html-preview" :style="{ height: height || '100%' }">
    <iframe
      :key="frameKey"
      :srcdoc="srcDoc"
      class="html-frame"
      sandbox="allow-scripts allow-forms allow-modals"
      referrerpolicy="no-referrer"
      title="HTML 预览"
    ></iframe>
  </div>
</template>

<style scoped>
.html-preview {
  display: flex;
  width: 100%;
  min-height: 0;
  overflow: hidden;
  background: #0b1220;
}

.html-frame {
  flex: 1;
  width: 100%;
  height: 100%;
  border: 0;
  background: transparent;
}
</style>
