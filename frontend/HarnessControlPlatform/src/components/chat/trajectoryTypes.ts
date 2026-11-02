// 轨迹树节点结构：轨迹页与聊天页共用
import type { ChatMsg } from '@/stores/chat'

export interface TreeNode {
  msg: ChatMsg
  children: TreeNode[]
}
