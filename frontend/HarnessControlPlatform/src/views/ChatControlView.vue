<script setup lang="ts">
// 对话控制：配置对话测试页可见的消息类型
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import { useDisplayStore } from '@/stores/display'
import type { IconName } from '@/components/console/icons'

const display = useDisplayStore()

interface Row {
  key: keyof ReturnType<typeof useDisplayStore>['prefs']
  label: string
  desc: string
  icon: IconName
}

const ROWS: Row[] = [
  { key: 'showReasoning', label: '推理内容', desc: '显示模型处理中的状态提示（推理中 / 思考动画）', icon: 'sparkles' },
  { key: 'showToolCalls', label: '工具调用', desc: '显示 read_file / web_search / execute 等普通工具卡片', icon: 'wrench' },
  { key: 'showSkills', label: '技能使用', desc: '显示 download_skill / assign_skill 等技能操作卡片', icon: 'book' },
  { key: 'showDelegation', label: '子 Agent 委派', desc: '显示 task 同步委派记录与子 Agent 名称', icon: 'bot' },
  { key: 'showAsyncTasks', label: '异步任务', desc: '显示 start_async_task 异步任务提交卡片', icon: 'clock' },
  { key: 'expandToolArgs', label: '参数默认展开', desc: '工具卡片默认展开参数与结果，而非折叠', icon: 'eye' },
  { key: 'typewriter', label: '打字机效果', desc: '流式 token 以逐字打字方式渲染', icon: 'message' },
]
</script>

<template>
  <div class="app-shell">
    <TopNav />
    <div class="page">
        <section class="panel control">
          <div class="panel-head">
            <div class="panel-title">
              <span class="title-icon"><AppIcon name="sliders" :size="14" /></span>
              对话控制
              <small>配置对话测试页可见的 AI 消息类型</small>
            </div>
            <button class="reset" type="button" @click="display.reset()">恢复默认</button>
          </div>

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
            设置保存在浏览器本地（localStorage），仅影响当前控制台的展示，不改变 Agent 的实际执行行为。
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

.control {
  max-width: 760px;
  padding-bottom: 14px;
}

.panel-title small {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
}

.reset {
  padding: 6px 14px;
  font-size: 12px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  transition: all 0.18s;
}

.reset:hover {
  color: var(--text-1);
  border-color: var(--line-strong);
}

.rows {
  list-style: none;
  margin: 10px 16px 0;
  padding: 0;
}

.rows li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 6px;
  border-bottom: 1px solid rgba(86, 140, 255, 0.08);
}

.rows li:last-child {
  border-bottom: none;
}

.icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  color: var(--accent-cyan);
  background: rgba(53, 224, 255, 0.09);
  flex: none;
}

.text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.text strong {
  font-size: 13px;
}

.text span {
  font-size: 11.5px;
  color: var(--text-3);
}

.foot {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 12px 16px 0;
  padding: 10px 12px;
  font-size: 11px;
  color: var(--text-3);
  border: 1px dashed var(--line);
  border-radius: 10px;
  background: rgba(16, 32, 64, 0.35);
}
</style>
