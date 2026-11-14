// 轨迹树节点结构：轨迹页与聊天页共用
import type { InjectionKey } from 'vue'
import type { ChatMsg } from '@/stores/chat'

export interface TreeNode {
  msg: ChatMsg
  children: TreeNode[]
}

/** 轨迹节点登记信息：让聊天页顶部的粘性折叠条能定位并操作当前顶部节点。 */
export interface TrajectoryEntry {
  /** 递归深度，从 1 开始。 */
  level: () => number
  /** 行内展示的标题。 */
  label: () => string
  /** 该节点是否有可折叠内容。 */
  collapsible: () => boolean
  /** 当前是否展开。 */
  open: () => boolean
  /** 切换展开 / 收起。 */
  toggle: () => void
}

export type TrajectoryRegistry = Map<string, TrajectoryEntry>

/** 由 ChatView 提供；TrajectoryNode 注入后自动登记自己。 */
export const TRAJECTORY_REGISTRY: InjectionKey<TrajectoryRegistry> = Symbol(
  'trajectory-registry',
)
