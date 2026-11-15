<script setup lang="ts">
// Agent 的组籍抽屉：加入/退出组；队长关系需在组模式里处理。
import { computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import HoloPanel from '@/components/console/HoloPanel.vue'
import { useAgentStore } from '@/stores/agents'

const props = defineProps<{
  modelValue: boolean
  agentId: string
}>()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  changed: []
}>()

const agents = useAgentStore()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

function isMember(groupId: string): boolean {
  const group = agents.groups.find((item) => item.group_id === groupId)
  return Boolean(group?.members.some((member) => member.agent_id === props.agentId))
}

function isLeader(groupId: string): boolean {
  return agents.groups.find((item) => item.group_id === groupId)?.leader_agent_id === props.agentId
}

function leaderName(groupId: string): string {
  const group = agents.groups.find((item) => item.group_id === groupId)
  if (!group?.leader_agent_id) return '未设队长'
  return agents.byId(group.leader_agent_id)?.name || group.leader_agent_id
}

async function toggle(groupId: string, next: boolean) {
  if (isLeader(groupId)) {
    ElMessage.warning('队长不能在这里退出组，请先在组模式转移或取消队长')
    return
  }
  try {
    if (next) await agents.addGroupMember(groupId, props.agentId)
    else await agents.removeGroupMember(groupId, props.agentId)
    emit('changed')
  } catch (error) {
    ElMessage.error(`操作失败：${(error as Error).message}`)
  }
}

watch(visible, async (open) => {
  if (open) await agents.loadGroups(true)
})
</script>

<template>
  <HoloPanel
    v-model="visible"
    title="组管理"
    kicker="AGENT DOCK // GROUPS"
    subtitle="加入组后，该 Agent 才能被队长发现与委派；队长身份请在「组」模式中设置。"
  >
    <div class="drawer-body">
      <div v-for="group in agents.groups" :key="group.group_id" class="group-row">
        <span class="group-mark">{{ group.name.slice(0, 1) }}</span>
        <span class="group-meta">
          <strong>{{ group.name }}</strong>
          <small>{{ group.members.length }} 人 · 队长 {{ leaderName(group.group_id) }}</small>
        </span>
        <el-tag v-if="isLeader(group.group_id)" size="small" type="warning" effect="plain">
          队长
        </el-tag>
        <el-switch
          v-else
          :model-value="isMember(group.group_id)"
          @update:model-value="(value: string | number | boolean) => toggle(group.group_id, Boolean(value))"
        />
      </div>
      <el-empty
        v-if="!agents.groups.length"
        description="还没有组，去「组」模式新建"
        :image-size="50"
      />
    </div>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </HoloPanel>
</template>

<style scoped>
.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.group-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 6px 4px;
  border-bottom: 1px solid var(--line);
}

.group-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: none;
  font-size: 12px;
  font-weight: 700;
  color: #041018;
  border-radius: 50%;
  background: linear-gradient(135deg, #f6c85f, #f08a5d);
}

.group-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
  line-height: 1.3;
}

.group-meta strong {
  font-size: 12.5px;
  color: var(--text-1);
}

.group-meta small {
  font-size: 10px;
  color: var(--text-3);
}
</style>
