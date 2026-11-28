﻿using System;
using Proto = Agent.Proto;

namespace CommonHarnessPluginSDK.Services
{
    /// <summary>
    /// 【Q1 作用】一次待审批工具调用的快照，供宿主界面展示"AI 想做什么"。
    /// 【Q2 读取方】ToolApprovalBroker 的 ApprovalRequested 事件订阅方。
    /// 【Q3 创建时机】ToolService 判定工具需要审批时。
    /// 【Q4 释放】无（不可变数据对象）。
    /// 【Q5 线程约束】不可变，构造后线程安全。
    /// </summary>
    public sealed class ToolApprovalRequest
    {
        public required string ApprovalId { get; init; }
        public required string CallId { get; init; }
        public required string ToolName { get; init; }
        public required string DisplayName { get; init; }
        public required string ArgumentsJson { get; init; }
        public required string ThreadId { get; init; }
        public required string RunId { get; init; }
        public required bool SupportsEditArgs { get; init; }
        public DateTime ExpiresAtUtc { get; init; }
    }

    public sealed class ToolApprovalRequestedEventArgs : EventArgs
    {
        public required ToolApprovalRequest Request { get; init; }
    }

    /// <summary>
    /// 【Q1 作用】审批策略中枢：把"工具需要审批"这件事以事件形式抛给宿主（WPF），
    ///           并由 <see cref="DecisionProvider"/> 决定最终结果。
    /// <para>默认策略：没有订阅方 / 没有 DecisionProvider 时一律自动批准，
    /// 即"协议保留 + 事件保留 + 默认放行"。接审批面板时，把 DecisionProvider 换成等待用户点击即可。</para>
    /// 【Q2 调用方】ToolGrpcService（CallTool 时抛事件；ResolveApproval 时取决策）。
    /// 【Q3 创建时机】HarnessToolServer 构造时创建，进程级单例。
    /// 【Q4 释放】随宿主退出。
    /// 【Q5 线程约束】事件可能在 gRPC 线程池线程上触发；订阅方自行封回 UI 线程。
    /// </summary>
    public sealed class ToolApprovalBroker
    {
        /// <summary>工具进入待审批状态时触发（非阻塞：ToolService 仍会立即返回 NEEDS_APPROVAL）。</summary>
        public event EventHandler<ToolApprovalRequestedEventArgs>? ApprovalRequested;

        /// <summary>
        /// 决策提供者：返回 Approve / Reject / Edit。为 null 时使用 <see cref="DefaultDecision"/>。
        /// </summary>
        public Func<ToolApprovalRequest, Proto.ApprovalDecision>? DecisionProvider { get; set; }

        /// <summary>默认决策（无 DecisionProvider 时生效）；默认自动批准。</summary>
        public Proto.ApprovalDecision DefaultDecision { get; set; } = Proto.ApprovalDecision.Approve;

        /// <summary>
        /// 发出"待审批"通知（不阻塞等待）。
        /// </summary>
        public void NotifyRequested(ToolApprovalRequest request)
        {
            try
            {
                ApprovalRequested?.Invoke(this, new ToolApprovalRequestedEventArgs { Request = request });
            }
            catch
            {
                // 订阅方异常不能影响工具调用主链路
            }
        }

        /// <summary>
        /// 解析最终决策：优先 DecisionProvider，其次 DefaultDecision。
        /// </summary>
        public Proto.ApprovalDecision Resolve(ToolApprovalRequest request)
        {
            try
            {
                return DecisionProvider?.Invoke(request) ?? DefaultDecision;
            }
            catch
            {
                // 决策方异常按"拒绝"处理，保持安全默认
                return Proto.ApprovalDecision.Reject;
            }
        }
    }
}
