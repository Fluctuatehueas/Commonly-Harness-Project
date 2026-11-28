namespace CommonHarnessPluginSDK.Models
{
    /// <summary>
    /// 聊天模式：Chat 禁止调用 C# 工具；Task 允许调用（对应 proto 的 ChatMode）。
    /// </summary>
    public enum HarnessChatMode
    {
        Chat = 0,
        Task = 1,
    }

    /// <summary>
    /// 工具执行状态（对应 proto 的 ToolStatus）。
    /// </summary>
    public enum HarnessToolStatus
    {
        Unspecified = 0,
        Ok = 1,
        Error = 2,
        Denied = 3,
        NeedsApproval = 4,
        Cancelled = 5,
    }

    /// <summary>
    /// 会话结束状态（对应 proto 的 ChatStatus）。
    /// </summary>
    public enum HarnessChatStatus
    {
        Unspecified = 0,
        Ok = 1,
        Interrupted = 2,
        Error = 3,
        Cancelled = 4,
    }

    /// <summary>
    /// ChatChunk 的 CLR 投影：把 proto 的 oneof 分支归一化为一个可被 UI 直接消费的事件对象。
    /// </summary>
    public enum HarnessChatEventKind
    {
        TextDelta = 0,
        Message = 1,
        ToolCall = 2,
        ToolResult = 3,
        Trajectory = 4,
        Done = 5,
        Error = 6,

        /// <summary>模型思考增量</summary>
        Reasoning = 7,

        /// <summary>人工介入请求</summary>
        Hitl = 8,

        /// <summary>计划全量快照</summary>
        Plan = 9,
    }

    /// <summary>
    /// 【Q1 作用】计划中单个步骤的强类型投影（对应 proto 的 <c>PlanStepInfo</c>）。
    /// 【Q2 读写方】HarnessChatClient 解析 ChatChunk.plan 时创建；WPF 计划树只读绑定。
    /// 【Q3 创建时机】收到 <see cref="HarnessChatEventKind.Plan"/> 事件时。
    /// 【Q4 释放】随事件对象回收。
    /// 【Q5 线程约束】不可变对象，跨线程只读安全。
    /// </summary>
    public sealed class PlanStepDto
    {
        public string StepId { get; init; } = "";
        public string Domain { get; init; } = "";
        public string Goal { get; init; } = "";
        public IReadOnlyList<string> DependsOn { get; init; } = Array.Empty<string>();
        public IReadOnlyList<string> ExpectedTools { get; init; } = Array.Empty<string>();
        public string Acceptance { get; init; } = "";

        /// <summary>是否为视觉验收步骤（true 时界面用"验收"样式）。</summary>
        public bool IsVisualCheck { get; init; }

        /// <summary>pending / running / done / failed / skipped。</summary>
        public string Status { get; init; } = "pending";
        public string Error { get; init; } = "";
    }

    /// <summary>
    /// 【Q1 作用】HITL 推荐选项的强类型投影（对应 proto 的 <c>HitlOption</c>）。
    /// 【Q2 读写方】HarnessChatClient 解析 ChatChunk 时创建；WPF 卡片只读绑定。
    /// 【Q3 创建时机】收到 <see cref="HarnessChatEventKind.Hitl"/> 事件时。
    /// 【Q4 释放】随事件对象回收。
    /// 【Q5 线程约束】不可变对象，跨线程只读安全。
    /// </summary>
    public sealed class HarnessHitlOption
    {
        public string Value { get; init; } = "";
        public string Label { get; init; } = "";
        public string Description { get; init; } = "";

        /// <summary>是否为后端推荐项（UI 高亮标记）。</summary>
        public bool Recommended { get; init; }
    }

    /// <summary>
    /// 【Q1 作用】HITL 表单字段的强类型投影（对应 proto 的 <c>HitlField</c>）。
    ///           <c>Type</c> 取值：text / textarea / number / boolean / select / multiselect。
    /// 【Q2 读写方】HarnessChatClient 解析 ChatChunk 时创建；WPF 表单按 Type 选控件。
    /// 【Q3 创建时机】收到 <see cref="HarnessChatEventKind.Hitl"/> 事件时。
    /// 【Q4 释放】随事件对象回收。
    /// 【Q5 线程约束】不可变对象，跨线程只读安全。
    /// </summary>
    public sealed class HarnessHitlField
    {
        public string Name { get; init; } = "";
        public string Label { get; init; } = "";
        public string Type { get; init; } = "text";
        public bool Required { get; init; }

        /// <summary>默认值的 JSON 表示（空串表示没有默认值）。</summary>
        public string DefaultJson { get; init; } = "";
        public string Placeholder { get; init; } = "";
        public string Description { get; init; } = "";
        public IReadOnlyList<HarnessHitlOption> Options { get; init; } = Array.Empty<HarnessHitlOption>();
    }

    /// <summary>
    /// 【Q1 作用】聊天流事件：承载 ChatService 流式响应的一个分支。
    /// 【Q2 读取方】WPF 的 AI 会话 ViewModel（按 Kind 分派到不同消息/轨迹项）。
    /// 【Q3 创建时机】HarnessChatClient 解析每个 ChatChunk 时。
    /// 【Q4 释放】随集合项回收。
    /// 【Q5 线程约束】在 gRPC 异步流的消费线程创建；UI 侧需自行封回 UI 线程。
    /// </summary>
    public sealed class HarnessChatEvent
    {
        public HarnessChatEventKind Kind { get; init; }

        /// <summary>文本增量（TextDelta）。</summary>
        public string Text { get; init; } = "";

        // ---- Message ----
        public string MessageId { get; init; } = "";
        public string Role { get; init; } = "";
        public string Content { get; init; } = "";
        public string Name { get; init; } = "";
        public string CreatedAt { get; init; } = "";

        // ---- Reasoning ----
        public string ReasoningDelta { get; init; } = "";
        public string ReasoningStepId { get; init; } = "";

        // ---- ToolCall / ToolResult ----
        public string CallId { get; init; } = "";
        public string RunId { get; init; } = "";
        public string ToolName { get; init; } = "";
        public string ArgumentsJson { get; init; } = "";
        public string ResultJson { get; init; } = "";
        public string ErrorJson { get; init; } = "";
        public HarnessToolStatus ToolStatus { get; init; }

        /// <summary>ToolCall：true 表示 <see cref="ArgumentsJson"/> 只是增量片段，需按 CallId 累积。</summary>
        public bool IsPartial { get; init; }

        // ---- HITL（P2）----
        public string HitlId { get; init; } = "";
        public string HitlKind { get; init; } = "";
        public string HitlTitle { get; init; } = "";
        public string HitlDescription { get; init; } = "";
        public IReadOnlyList<HarnessHitlField> HitlFields { get; init; } = Array.Empty<HarnessHitlField>();
        public IReadOnlyList<HarnessHitlOption> HitlOptions { get; init; } = Array.Empty<HarnessHitlOption>();
        public bool HitlAllowMultiple { get; init; }
        public bool HitlAllowFreeText { get; init; }
        public string HitlSubmitLabel { get; init; } = "";
        public string HitlCancelLabel { get; init; } = "";
        public string HitlResumeSchemaJson { get; init; } = "";
        public string HitlMetadataJson { get; init; } = "";

        // ---- Plan（P4）----
        public string PlanId { get; init; } = "";
        public int PlanVersion { get; init; }
        public string PlanStatus { get; init; } = "";
        public string ReplanReason { get; init; } = "";
        public string PlanCreatedAt { get; init; } = "";
        public IReadOnlyList<PlanStepDto> PlanSteps { get; init; } = Array.Empty<PlanStepDto>();

        // ---- Trajectory ----
        public string EventId { get; init; } = "";
        public string ParentId { get; init; } = "";
        public string TrajectoryType { get; init; } = "";
        public string TrajectoryStatus { get; init; } = "";
        public string TrajectoryArgsJson { get; init; } = "";
        public string TrajectoryResultJson { get; init; } = "";
        public string StartedAt { get; init; } = "";
        public string EndedAt { get; init; } = "";
        public string MetadataJson { get; init; } = "";

        // ---- Done ----
        public string DoneMessage { get; init; } = "";
        public string ThreadId { get; init; } = "";
        public string FinishReason { get; init; } = "";
        public string UsageJson { get; init; } = "";
        public HarnessChatStatus ChatStatus { get; init; }

        // ---- Error ----
        public string ErrorCode { get; init; } = "";
        public string ErrorMessage { get; init; } = "";
        public string ErrorHint { get; init; } = "";
        public bool Retryable { get; init; }

        /// <summary>事件发生时刻（本地时间）。</summary>
        public DateTime Timestamp { get; init; } = DateTime.Now;
    }
}
