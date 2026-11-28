namespace CommonHarnessPluginSDK.Models
{
    /// <summary>
    /// C# 侧轨迹事件模型，与 Python TrajectoryMiddleware 的事件字段保持同名同义。
    /// 由工具执行层产生，随 ToolCallResult.trajectory 上报，Python 统一落库。
    /// </summary>
    public sealed class TrajectoryEvent
    {
        public string EventId { get; init; } = Guid.NewGuid().ToString("N");
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
    /// 工具错误：跨 gRPC 边界只传结构化数据，不抛裸异常。
    /// </summary>
    public sealed class ToolError
    {
        public string Code { get; init; } = "";
        public string Message { get; init; } = "";
        public string Hint { get; init; } = "";
        public bool Retryable { get; init; }
    }

    /// <summary>
    /// 工具执行结果。Success 恒等于 Status == OK，由上层服务统一推导。
    /// </summary>
    public sealed class ToolResult
    {
        public bool Success { get; init; }
        public string ResultJson { get; init; } = "{}";
        public ToolError? Error { get; init; }
        public System.Collections.Generic.List<TrajectoryEvent> Trajectory { get; init; } = new();

        /// <summary>成功结果（resultJson 必须是合法 JSON 文本，字符串请传已序列化的引号串）。</summary>
        public static ToolResult Ok(string resultJson = "{}")
        {
            return new ToolResult { Success = true, ResultJson = resultJson };
        }

        /// <summary>失败结果：code 供模型与日志定位，hint 给模型下一步建议。</summary>
        public static ToolResult Fail(string code, string message, string hint = "", bool retryable = false)
        {
            return new ToolResult
            {
                Success = false,
                Error = new ToolError
                {
                    Code = code,
                    Message = message,
                    Hint = hint,
                    Retryable = retryable,
                },
            };
        }
    }
}
