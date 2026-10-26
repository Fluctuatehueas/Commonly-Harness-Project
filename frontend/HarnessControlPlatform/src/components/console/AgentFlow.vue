<script setup lang="ts">
// 智能体协作流程图
import AppIcon from './AppIcon.vue'
import { FLOW_KNOWLEDGE, FLOW_MAIN } from '@/mock/dashboard'
</script>

<template>
  <section class="panel flow">
    <div class="panel-head">
      <div class="panel-title">
        <span class="title-icon"><AppIcon name="workflow" :size="14" /></span>
        智能体协作
        <small>多智能体协同，完成复杂任务</small>
      </div>
      <a class="panel-link" href="#">查看工作流 <AppIcon name="chevronRight" :size="12" /></a>
    </div>

    <div class="diagram">
      <template v-for="(node, i) in FLOW_MAIN" :key="node.key">
        <div class="node" :style="{ gridColumn: i * 2 + 1 }">
          <div class="node-icon"><AppIcon :name="node.icon" :size="16" /></div>
          <div class="node-text">
            <strong>{{ node.title }}</strong>
            <span>{{ node.desc }}</span>
          </div>
        </div>
        <div v-if="i < FLOW_MAIN.length - 1" class="arrow" :style="{ gridColumn: i * 2 + 2 }">
          <AppIcon name="chevronRight" :size="16" />
        </div>
      </template>

      <div class="node knowledge">
        <div class="node-icon"><AppIcon :name="FLOW_KNOWLEDGE.icon" :size="16" /></div>
        <div class="node-text">
          <strong>{{ FLOW_KNOWLEDGE.title }}</strong>
          <span>{{ FLOW_KNOWLEDGE.desc }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.flow {
  padding-bottom: 18px;
}

.panel-title small {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
}

.diagram {
  display: grid;
  grid-template-columns: 1fr 44px 1fr 44px 1fr 44px 1fr;
  align-items: center;
  gap: 6px 0;
  padding: 16px 18px 2px;
}

.node {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: all 0.2s;
}

.node:hover {
  border-color: var(--line-strong);
  background: var(--bg-card-hover);
}

.node-icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  color: #bcd8ff;
  background: rgba(47, 100, 200, 0.22);
  border: 1px solid var(--line-strong);
  flex: none;
}

.node-text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
  min-width: 0;
}

.node-text strong {
  font-size: 12.5px;
}

.node-text span {
  font-size: 10.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.arrow {
  display: grid;
  place-items: center;
  color: var(--accent);
  opacity: 0.85;
}

/* 知识智能体挂在规划与执行之间，带竖向虚线连接 */
.knowledge {
  grid-column: 3 / 6;
  justify-self: center;
  position: relative;
  margin-top: 14px;
  min-width: 210px;
}

.knowledge::before {
  content: '';
  position: absolute;
  top: -16px;
  left: 50%;
  width: 0;
  height: 16px;
  border-left: 1px dashed var(--line-strong);
}

@media (max-width: 1100px) {
  .diagram {
    grid-template-columns: 1fr;
  }
  .node,
  .knowledge {
    grid-column: 1;
  }
  .arrow {
    transform: rotate(90deg);
    height: 26px;
    grid-column: 1 !important;
  }
  .knowledge {
    margin-top: 6px;
  }
  .knowledge::before {
    display: none;
  }
}
</style>
