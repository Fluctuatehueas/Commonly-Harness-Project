<script setup lang="ts">
// 模型管理：选卡片 → 回填参数（含思考能力）→ 保存才写 .env 并热更新
import { onMounted } from 'vue'
import TopNav from '@/components/console/TopNav.vue'
import AppIcon from '@/components/console/AppIcon.vue'
import UiSwitch from '@/components/console/UiSwitch.vue'
import { MODEL_CATALOG, useModelStore } from '@/stores/models'

const store = useModelStore()
onMounted(() => store.load())
</script>

<template>
  <div class="app-shell">
    <TopNav />
    <div class="page">
        <div v-if="store.offline" class="notice">
          <AppIcon name="shield" :size="14" />
          后端未连接（{{ store.offline ? 'http://127.0.0.1:8000' : '' }}）：可浏览与选择模型，保存更改需要启动 FastAPI 服务。
        </div>

        <div class="columns">
          <section class="panel catalog">
            <div class="panel-head">
              <div class="panel-title">
                <span class="title-icon"><AppIcon name="cube" :size="14" /></span>
                模型目录
                <small>点击卡片选择，参数自动回填</small>
              </div>
            </div>
            <div class="grid">
              <button
                v-for="m in MODEL_CATALOG"
                :key="m.id"
                class="card"
                :class="{ selected: store.selectedId === m.id, active: store.current.model_name === m.id }"
                type="button"
                @click="store.select(m)"
              >
                <div class="card-top">
                  <span class="avatar" :style="{ background: m.gradient }">{{ m.initial }}</span>
                  <div class="meta">
                    <strong>{{ m.name }}</strong>
                    <span>{{ m.provider }}</span>
                  </div>
                  <span v-if="store.current.model_name === m.id" class="using">使用中</span>
                  <span v-else-if="store.selectedId === m.id" class="picked"><AppIcon name="check" :size="12" />已选</span>
                </div>
                <div class="ctx">
                  <AppIcon name="memory" :size="12" />
                  {{ m.context }}
                  <span class="temp">temp {{ m.temperature }}</span>
                </div>
                <div class="tags">
                  <span v-for="t in m.features" :key="t" class="tag">{{ t }}</span>
                </div>
                <code v-if="m.baseUrl" class="url">{{ m.baseUrl }}</code>
                <span v-if="m.note" class="note">{{ m.note }}</span>
              </button>
            </div>
          </section>

          <aside class="panel config">
            <div class="panel-head">
              <div class="panel-title">
                <span class="title-icon"><AppIcon name="sliders" :size="14" /></span>
                配置面板
              </div>
              <span v-if="store.dirty" class="dirty">未保存</span>
            </div>

            <div class="fields">
              <label>
                <span>模型名称 <em>DEEPSEEK_MODEL_NAME</em></span>
                <input v-model="store.form.model_name" placeholder="如 deepseek-chat" />
              </label>
              <label>
                <span>Base URL <em>DEEPSEEK_BASE_URL</em></span>
                <input v-model="store.form.base_url" placeholder="https://api.deepseek.com" />
              </label>
              <label>
                <span>API Key <em>DEEPSEEK_API_KEY</em></span>
                <input
                  v-model="store.form.api_key"
                  type="password"
                  autocomplete="off"
                  :placeholder="store.current.has_api_key ? '留空保持不变；切换模型后已清空' : '尚未配置，请输入'"
                />
                <span class="key-state">
                  <i class="dot" :class="{ ok: store.current.has_api_key }"></i>
                  {{ store.current.has_api_key ? '服务端已保存 API Key' : '服务端未配置 API Key' }}
                </span>
              </label>

              <div class="think-row">
                <div class="think-text">
                  <strong>思考能力</strong>
                  <span>开启后模型返回推理过程（thinking），适合复杂任务，但更慢、更耗额度</span>
                </div>
                <UiSwitch v-model="store.form.thinking_enabled" />
              </div>

              <div v-if="store.selected" class="preview">
                <strong>{{ store.selected.name }}</strong>
                <p>推荐温度 {{ store.selected.temperature }} · {{ store.selected.context }}{{ store.selected.note ? ' · ' + store.selected.note : '' }}</p>
              </div>

              <button class="save" type="button" :disabled="!store.dirty || store.saving" @click="store.save()">
                <AppIcon name="check" :size="14" />
                {{ store.saving ? '保存中…' : '保存更改' }}
              </button>
              <p class="hint">保存后写入 .env 并热更新运行中的模型；API Key 留空则保留原值。</p>
            </div>
          </aside>
        </div>
      </div>

      <Transition name="toast">
        <div v-if="store.toast" class="toast" :class="store.toastType">
          <AppIcon :name="store.toastType === 'ok' ? 'check' : 'close'" :size="14" />
          {{ store.toast }}
        </div>
      </Transition>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.page {
  padding: 18px 22px 26px;
}

.think-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(16, 32, 64, 0.4);
}

.think-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.think-text strong {
  font-size: 12.5px;
}

.think-text span {
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-3);
}

.notice {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  padding: 10px 14px;
  font-size: 12px;
  color: var(--amber);
  border: 1px solid rgba(255, 182, 72, 0.35);
  border-radius: var(--radius-md);
  background: rgba(255, 182, 72, 0.08);
}

.columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.catalog {
  padding-bottom: 16px;
}

.panel-title small {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
  padding: 14px 16px 0;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 13px;
  text-align: left;
  color: var(--text-1);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: all 0.18s;
}

.card:hover {
  border-color: var(--line-strong);
  background: var(--bg-card-hover);
  transform: translateY(-2px);
}

.card.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent), 0 6px 20px rgba(59, 120, 255, 0.25);
}

.card.active {
  border-color: rgba(61, 220, 132, 0.5);
}

.card-top {
  display: flex;
  align-items: center;
  gap: 9px;
}

.avatar {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  font-size: 14px;
  font-weight: 700;
  color: #fff;
  flex: none;
}

.meta {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
  min-width: 0;
  flex: 1;
}

.meta strong {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta span {
  font-size: 11px;
  color: var(--text-3);
}

.using,
.picked {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font-size: 10px;
  border-radius: 999px;
  flex: none;
}

.using {
  color: var(--green);
  border: 1px solid rgba(61, 220, 132, 0.4);
  background: rgba(61, 220, 132, 0.08);
}

.picked {
  color: #cfe3ff;
  border: 1px solid var(--accent);
  background: rgba(79, 140, 255, 0.15);
}

.ctx {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-2);
}

.ctx svg {
  color: var(--text-3);
}

.temp {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--text-3);
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag {
  padding: 2px 8px;
  font-size: 10.5px;
  color: var(--text-2);
  border: 1px solid var(--line);
  border-radius: 6px;
  background: rgba(10, 20, 40, 0.5);
}

.url {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.note {
  font-size: 10px;
  color: var(--amber);
}

.config {
  position: sticky;
  top: calc(var(--topbar-h) + 18px);
  padding-bottom: 16px;
}

.dirty {
  padding: 2px 9px;
  font-size: 10.5px;
  color: var(--amber);
  border: 1px solid rgba(255, 182, 72, 0.4);
  border-radius: 999px;
}

.fields {
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding: 16px;
}

.fields label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fields label > span {
  font-size: 11.5px;
  color: var(--text-2);
}

.fields label em {
  font-style: normal;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: var(--text-3);
  margin-left: 6px;
}

.fields input {
  padding: 9px 12px;
  font-size: 12.5px;
  font-family: 'Cascadia Code', Consolas, monospace;
  color: var(--text-1);
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: rgba(6, 13, 28, 0.7);
  outline: none;
}

.fields input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(79, 140, 255, 0.15);
}

.key-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: var(--text-3);
}

.key-state .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ff6b6b;
}

.key-state .dot.ok {
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
}

.preview {
  padding: 10px 12px;
  border: 1px dashed var(--line-strong);
  border-radius: 10px;
  background: rgba(16, 32, 64, 0.4);
}

.preview strong {
  display: block;
  font-size: 12.5px;
}

.preview p {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-3);
}

.save {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 11px;
  font-size: 13px;
  color: #fff;
  border: none;
  border-radius: 11px;
  background: linear-gradient(90deg, #3b78ff, #2a5cd6);
  box-shadow: 0 6px 18px rgba(50, 110, 255, 0.35);
  transition: all 0.18s;
}

.save:hover {
  filter: brightness(1.12);
}

.save:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  filter: none;
  box-shadow: none;
}

.hint {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--text-3);
}

.toast {
  position: fixed;
  right: 26px;
  bottom: 26px;
  z-index: 50;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 11px 18px;
  font-size: 12.5px;
  border-radius: 11px;
  border: 1px solid rgba(61, 220, 132, 0.4);
  color: #c9f5db;
  background: rgba(12, 40, 28, 0.92);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
}

.toast.err {
  border-color: rgba(255, 93, 93, 0.45);
  color: #ffd2d2;
  background: rgba(56, 16, 20, 0.92);
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(12px);
}

@media (max-width: 1180px) {
  .columns {
    grid-template-columns: 1fr;
  }
  .config {
    position: static;
  }
}
</style>
