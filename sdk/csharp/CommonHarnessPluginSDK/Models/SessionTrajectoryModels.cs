﻿using System;
using System.Collections.Generic;

namespace CommonHarnessPluginSDK.Models
{
    /// <summary>
    /// 会话历史中的消息角色（与 proto MessageRole 对齐，但不暴露生成类型）。
    /// </summary>
    public enum HarnessMessageRole
    {
        Unspecified = 0,
        System = 1,
        Human = 2,
        Ai = 3,
        Tool = 4,
    }

    /// <summary>
    /// 工具调用记录
    /// </summary>
    public sealed class ToolCallRecord
    {
        public string CallId { get; init; } = "";
        public string ToolName { get; init; } = "";
        public string ArgumentsJson { get; init; } = "";
    }

    /// <summary>
    /// 一条历史消息的 CLR 投影（含 LangChain 风格的 tool_calls / tool_call_id）。
    /// </summary>
    public sealed class ChatMessageRecord
    {
        public string MessageId { get; init; } = "";
        public HarnessMessageRole Role { get; init; }
        public string Content { get; init; } = "";
        public IReadOnlyList<ToolCallRecord> ToolCalls { get; init; } = Array.Empty<ToolCallRecord>();
        public string ToolCallId { get; init; } = "";
        public string Name { get; init; } = "";
        public string AdditionalKwargsJson { get; init; } = "";
        public string CreatedAt { get; init; } = "";
    }

    /// <summary>
    /// 会话索引项：C# 不直连 PostgreSQL，全部来自 SessionService。
    /// </summary>
    public sealed class SessionSummary
    {
        public string ThreadId { get; init; } = "";
        public string Title { get; init; } = "";
        public string CreatedAt { get; init; } = "";
        public string UpdatedAt { get; init; } = "";
        public int MessageCount { get; init; }
        public bool Pinned { get; init; }
        public string LastMessagePreview { get; init; } = "";
        public string UsageJson { get; init; } = "";
        public IReadOnlyList<ChatMessageRecord> Messages { get; init; } = Array.Empty<ChatMessageRecord>();
    }

    public sealed class SessionHistory
    {
        public SessionSummary? Session { get; init; }
        public IReadOnlyList<ChatMessageRecord> Messages { get; init; } = Array.Empty<ChatMessageRecord>();
        public string InterruptJson { get; init; } = "";
        public string SessionUsageJson { get; init; } = "";
    }

    /// <summary>
    /// 一条轨迹事件（run / model / tool / skill / delegation / hitl）。
    /// </summary>
    public sealed class TrajectoryEventRecord
    {
        public string EventId { get; init; } = "";
        public string RunId { get; init; } = "";
        public string ThreadId { get; init; } = "";
        public string ParentId { get; init; } = "";
        public string Type { get; init; } = "";
        public string Name { get; init; } = "";
        public string Status { get; init; } = "";
        public string ArgsJson { get; init; } = "";
        public string ResultJson { get; init; } = "";
        public string StartedAt { get; init; } = "";
        public string EndedAt { get; init; } = "";
        public string MetadataJson { get; init; } = "";
    }

    /// <summary>
    /// 一次运行的轨迹聚合。
    /// </summary>
    public sealed class TrajectoryRunRecord
    {
        public string RunId { get; init; } = "";
        public string StartedAt { get; init; } = "";
        public string EndedAt { get; init; } = "";
        public string Status { get; init; } = "";
        public IReadOnlyList<TrajectoryEventRecord> Events { get; init; } = Array.Empty<TrajectoryEventRecord>();
        public string SummaryJson { get; init; } = "";
    }

    public sealed class TrajectorySnapshot
    {
        public string ThreadId { get; init; } = "";
        public IReadOnlyList<TrajectoryRunRecord> Runs { get; init; } = Array.Empty<TrajectoryRunRecord>();
        public string LatestRunId { get; init; } = "";
        public int EventCount { get; init; }
        public string GeneratedAt { get; init; } = "";
    }
}
