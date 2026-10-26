"""
从 LangGraph checkpoint 恢复会话展示消息。
一、前端展示历史会话：刷新/切换后要“看到完整对话”
二、HITL 待审批恢复：用户离开后回来，要“看到还有哪些审批没处理”
三、只读、无副作用：不能为了“看一眼状态”就重跑 Agent

风险：如果框架内部发生了变化，这里的代码可能会失效
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import StateBackend # 存储在LangGraph状态中，单次会话有效
from langgraph._internal._constants import NS_END, NS_SEP
# StateSnapshot 是 aget_state() 的返回类型，是一个 NamedTuple，包含 values、tasks、interrupts、config 等字段。
from langgraph.types import StateSnapshot
from langgraph.checkpoint.base import BaseCheckpointSaver


from agent.config import get_main_model
# 中断通道常量。LangGraph 把 interrupt() 产生的待恢复信息写入
#  checkpoint 的 pending_writes，其 channel 名就是这个双下划线包裹的字符串。
INTERRUPT_CHANNEL = "__interrupt__"

class ThreadHistoryReader:
    """
    通过只读状态图重放指定会话的消息增量。

    DeepAgents 的 ``messages`` 使用 ``DeltaChannel`` 保存增量，消息必须由图重建；
    待处理中断从当前 checkpoint 补全；旧版流取消造成父记录缺失时，补读活动子图。
    该模块将恢复细节
    封装为仅接受 ``thread_id`` 的接口；它只访问持久化数据库，不调用模型或沙箱。
    """
    # * 之前的参数：可以按位置传，也可以按关键字传。
    # * 之后的参数：只能按关键字传，按位置传会直接报错。
    def __init__(self, *, checkpointer: BaseCheckpointSaver) -> None:
        """
        创建用于恢复状态的最小 DeepAgents 图
        """
        # create_deep_agent 要求显式模型：本图只用 aget_state(),不会执行模型
        self._checkpointer = checkpointer
        self._state_graph = create_deep_agent(
            model=get_main_model(),
            backend=StateBackend(),
            checkpointer=checkpointer
        )

    async def get_messages(self, thread_id: str) -> list[Any]:
        """
        重放一个会话的checkpointer，并返回完整消息序列
        """
        state = await self.get_state(thread_id)
        return list(state.values.get("messages",[]))

    async def get_state(self, thread_id: str) -> StateSnapshot:
        """
        恢复消息以及待处理任务、中断，供历史展示和安全投递共同使用  
        **内部逻辑**：  
        框架提供的标准恢复接口，在“读图与主图结构不一致”时，会静默丢失中断信息，
        而历史展示和审批恢复恰恰最不能容忍这种丢失。 
        整个函数就是围绕这个矛盾展开的——先拿标准结果，
        再识别缺口，再用两轮补读把缺口填上，最后合并返回。  
        - 第一步：先拿到“框架认为的完整状态”。| 在框架的 _aprepare_state_snapshot 内部：它会跳过无法匹配到当前图节点的任务，连带丢弃这些任务上的中断。  
        - 第二步：判断“有没有补的必要” | 
        - 第三步：第一轮补读——补当前 checkpoint 的中断
        - 第四步：第二轮补读——下沉子图补中断
        """
        # 1. 从历史会话的checkpointer中获取state
        # 从checkpointer取出对应 thread_id 的最新 CheckpointTuple。
        # 调用 _aprepare_state_snapshot() 来构建 StateSnapshot。
        # _aprepare_state_snapshot() 在构建快照时，会重放 pending_writes（将未完成的写入应用到任务状态上）。但关键在于，它在重放时会主动跳过特定类型的写入，比如 ERROR、INTERRUPT 和 RESUME 通道的写入。这意味着，框架本身就不会把 __interrupt__ 通道的 pending_writes 直接放进 StateSnapshot.interrupts 里。
        # 此外，ThreadHistoryReader 使用的是 “最小读图”，它不包含主图中的“审批 middleware 节点”。当 _aprepare_state_snapshot() 尝试将 pending_writes 匹配到任务时，如果任务所属的节点在当前图里不存在，框架会选择跳过这个任务，连带该任务上的中断信息也一并丢失。
        # 结论：state.interrupts 在这里是不完整的。这就是为什么代码紧接着开始了手动补读。
        state = await self._state_graph.aget_state(
            {"configurable":{"thread_id": thread_id}}
        )
        # 最小读图没有主图的审批 middleware 节点，SDK 会跳过未知节点的任务。
        # 仅补读同一 checkpoint 的中断写入；messages 仍由图重建 DeltaChannel。
        if not state.config.get("configurable",{}).get("checkpoint_id"):
            return state
        saved = await self._checkpointer.aget_tuple(state.config)
        if(saved is None):
            return state
        # 2. _aprepare_state_snapshot()绕过了INTERRUPT重放，这里进行补读
        interrupts = {item.id: item for item in state.interrupts}
        for _, channel, values in saved.pending_writes or []:
            if channel == INTERRUPT_CHANNEL:
                for item in values:
                    interrupts[item.id] = item
        # 3. 这里是最牛逼的地方：
        # 只处理“有错误、且自身没有中断记录”的任务。
        # 这里直接下沉到框架的checkpoint底层，直接构造了底层的任务命名空间方式来进行补全
        for task in state.tasks:
            if not task.error or task.interrupts:
                continue
            # 与 LangGraph _aprepare_state_snapshot 的子图 namespace 构造一致。
            # 只查询当前失败任务的子图，不扫描历史 namespace，避免恢复已完成的审批。
            parent_ns = state.config["configurable"].get("checkpoint_ns","")
            task_ns = f"{task.name}{NS_END}{task.id}" # task的命名空间
            if parent_ns:
                task_ns = f"{parent_ns}{NS_SEP}{task_ns}"
            child = await self._checkpointer.aget_tuple(
                {"configurable":{
                    "thread_id": thread_id,
                    "checkpoint_ns": task_ns
                }}
            )
            if child is not None:
                for _,channel, values in child.pending_writes or []:
                    if channel == INTERRUPT_CHANNEL:
                        for item in values:
                            interrupts[item.id] = item
        return state._replace(interrupts=tuple(interrupts.values()))

            
        