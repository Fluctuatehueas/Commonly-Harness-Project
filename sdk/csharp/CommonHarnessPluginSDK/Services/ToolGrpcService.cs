using Agent.Proto;
using CommonHarnessPluginSDK.Models;
using CommonHarnessPluginSDK.Utils;
using Grpc.Core;
using Microsoft.Extensions.Logging;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Proto = Agent.Proto;
using SkillDefinition = CommonHarnessPluginSDK.Models.SkillDefinition;
using TrajectoryEvent = CommonHarnessPluginSDK.Models.TrajectoryEvent;
// Attributes.SandboxLevel 与 Agent.Proto.SandboxLevel 同名，这里统一用别名区分
using SdkSandboxLevel = CommonHarnessPluginSDK.Attributes.SandboxLevel;

namespace CommonHarnessPluginSDK.Services
{
    /// <summary>
    /// 【Q1 作用】Python → C# 的 ToolService 服务端：ListTools / CallTool / ResolveApproval。
    /// <para>所有工具执行都经 <see cref="ToolRegistry"/>；需要审批的工具先返回 NEEDS_APPROVAL，
    /// 再由 ResolveApproval 二次调用执行或拒绝。</para>
    /// <para>契约版本取 <see cref="ContractVersion"/>，与 Python 端 chat_server.py 的 CONTRACT_VERSION 对应。</para>
    /// 【Q2 调用方】Python 的 CSharpToolService（tool_client.py）。
    /// 【Q3 创建时机】HarnessToolServer 启动时由 DI 创建。
    /// 【Q4 释放】随 gRPC 宿主停止。
    /// 【Q5 线程约束】gRPC 线程池并发调用；工具实现内部自行封回 UI 线程。
    /// </summary>
    public sealed class ToolGrpcService : Proto.ToolService.ToolServiceBase
    {
        public const string ContractVersion = ConstantVariable.ContraceVersion;
        private const int ApprovalTtlSeconds = 300;
        private const int IdempotencyTtlSeconds = 600;

        private static readonly ConcurrentDictionary<string, PendingApproval> PendingApprovals =
            new ConcurrentDictionary<string, PendingApproval>(StringComparer.Ordinal);

        private static readonly ConcurrentDictionary<string, CachedToolCall> CachedCalls =
            new ConcurrentDictionary<string, CachedToolCall>(StringComparer.Ordinal);

        private readonly ILogger<ToolGrpcService>? _logger;
        private readonly ToolApprovalBroker _approvals;

        public ToolGrpcService(ToolApprovalBroker approvals, ILogger<ToolGrpcService>? logger = null)
        {
            _approvals = approvals ?? throw new ArgumentNullException(nameof(approvals));
            _logger = logger;
        }

        public override Task<Proto.ListToolsResponse> ListTools(
            Proto.ListToolsRequest request,
            ServerCallContext context)
        {
            if (!IsCompatible(request.ContractVersion))
            {
                throw new RpcException(new Status(
                    StatusCode.FailedPrecondition,
                    $"契约版本不兼容：C# {ContractVersion}，客户端 {request.ContractVersion}"));
            }

            Proto.ListToolsResponse response = new Proto.ListToolsResponse
            {
                ContractVersion = ContractVersion,
            };

            foreach (SkillDefinition skill in ToolRegistry.GetSkills())
            {
                Proto.SkillDefinition protoSkill = new Proto.SkillDefinition
                {
                    Name = skill.Name,
                    DisplayName = skill.DisplayName,
                    Description = skill.Description,
                    PromptFragment = skill.PromptFragment,
                    MetadataJson = skill.MetadataJson,
                };

                foreach (ToolMetadata tool in skill.Tools)
                {
                    protoSkill.Tools.Add(ToProto(tool));
                }

                response.Skills.Add(protoSkill);
            }

            return Task.FromResult(response);
        }

        public override async Task<Proto.ToolCallResult> CallTool(
            Proto.ToolCallRequest request,
            ServerCallContext context)
        {
            if (!IsCompatible(request.ContractVersion))
            {
                return ErrorResult(
                    request,
                    "CONTRACT_VERSION_MISMATCH",
                    $"契约版本不兼容：C# {ContractVersion}，客户端 {request.ContractVersion}",
                    "请使用同一份 Agent.proto 重新生成两端桩代码。");
            }

            if (!ToolRegistry.TryGet(request.ToolName, out ToolRegistration registration))
            {
                return ErrorResult(
                    request,
                    "TOOL_NOT_FOUND",
                    $"未找到工具 {request.ToolName}。",
                    "先调用 ListTools 获取当前可用工具，不要猜测工具名。");
            }

            if (TryGetCached(request, out Proto.ToolCallResult? cached, out bool conflict))
            {
                return conflict
                    ? ErrorResult(
                        request,
                        "CALL_ID_CONFLICT",
                        $"call_id {request.CallId} 已用于不同参数。",
                        "同一 call_id 只能对应一次调用；请生成新的 call_id。")
                    : cached!;
            }

            if (registration.Metadata.SandboxLevel != SdkSandboxLevel.Auto)
            {
                string approvalId = Guid.NewGuid().ToString("N");
                DateTime expiresAt = DateTime.UtcNow.AddSeconds(ApprovalTtlSeconds);
                PendingApprovals[approvalId] = new PendingApproval
                {
                    ApprovalId = approvalId,
                    CallId = request.CallId,
                    ToolName = request.ToolName,
                    ArgumentsJson = request.ArgumentsJson,
                    ThreadId = request.ThreadId,
                    RunId = request.RunId,
                    ExpiresAtUtc = expiresAt,
                    SupportsEditArgs = registration.Metadata.SupportsEditArgs,
                };

                // 通知宿主界面（非阻塞）；默认策略下最终仍会自动批准。
                _approvals.NotifyRequested(new ToolApprovalRequest
                {
                    ApprovalId = approvalId,
                    CallId = request.CallId,
                    ToolName = request.ToolName,
                    DisplayName = registration.Metadata.DisplayName,
                    ArgumentsJson = request.ArgumentsJson,
                    ThreadId = request.ThreadId,
                    RunId = request.RunId,
                    SupportsEditArgs = registration.Metadata.SupportsEditArgs,
                    ExpiresAtUtc = expiresAt,
                });

                Proto.ToolCallResult pending = new Proto.ToolCallResult
                {
                    Success = false,
                    Status = Proto.ToolStatus.NeedsApproval,
                    CallId = request.CallId,
                    ApprovalId = approvalId,
                    ResultJson = "",
                    Error = "该工具需要人工审批，尚未执行。",
                    Detail = new Proto.ToolError
                    {
                        Code = "APPROVAL_REQUIRED",
                        Message = "该工具需要人工审批，尚未执行。",
                        Hint = "请调用 ResolveApproval(approval_id, decision)。",
                        Retryable = false,
                    },
                };

                pending.Trajectory.Add(ToProto(new TrajectoryEvent
                {
                    RunId = request.RunId,
                    ThreadId = request.ThreadId,
                    Type = "hitl",
                    Name = request.ToolName,
                    Status = "requested",
                    ArgsJson = request.ArgumentsJson,
                    StartedAt = IsoNow(),
                    MetadataJson = "{\"source\":\"csharp\",\"approval_id\":\"" + approvalId + "\"}",
                }));

                return pending;
            }

            return await ExecuteAsync(request, registration, request.ArgumentsJson, context.CancellationToken)
                .ConfigureAwait(false);
        }

        public override async Task<Proto.ToolCallResult> ResolveApproval(
            Proto.ResolveApprovalRequest request,
            ServerCallContext context)
        {
            if (!IsCompatible(request.ContractVersion))
            {
                return ErrorResult(
                    request.CallId,
                    "CONTRACT_VERSION_MISMATCH",
                    $"契约版本不兼容：C# {ContractVersion}，客户端 {request.ContractVersion}",
                    "请使用同一份 Agent.proto 重新生成两端桩代码。");
            }

            if (!PendingApprovals.TryRemove(request.ApprovalId, out PendingApproval? pending))
            {
                return ErrorResult(
                    request.CallId,
                    "APPROVAL_NOT_FOUND",
                    $"未找到待审批记录 {request.ApprovalId}。",
                    "审批记录单次使用；请重新发起工具调用。");
            }

            if (pending.ExpiresAtUtc < DateTime.UtcNow)
            {
                return ErrorResult(
                    request.CallId,
                    "APPROVAL_EXPIRED",
                    "审批记录已过期。",
                    "请重新发起工具调用并尽快审批。");
            }

            if (!ToolRegistry.TryGet(pending.ToolName, out ToolRegistration registration))
            {
                return ErrorResult(
                    request.CallId,
                    "TOOL_NOT_FOUND",
                    $"未找到工具 {pending.ToolName}。",
                    "工具可能在审批期间被移除。");
            }

            // 决策来源：客户端明确给出 > 宿主 DecisionProvider > 默认自动批准。
            Proto.ApprovalDecision effective = request.Decision;
            if (effective == Proto.ApprovalDecision.Unspecified)
            {
                effective = _approvals.Resolve(new ToolApprovalRequest
                {
                    ApprovalId = pending.ApprovalId,
                    CallId = pending.CallId,
                    ToolName = pending.ToolName,
                    DisplayName = registration.Metadata.DisplayName,
                    ArgumentsJson = pending.ArgumentsJson,
                    ThreadId = pending.ThreadId,
                    RunId = pending.RunId,
                    SupportsEditArgs = pending.SupportsEditArgs,
                    ExpiresAtUtc = pending.ExpiresAtUtc,
                });
            }

            if (effective == Proto.ApprovalDecision.Reject)
            {
                Proto.ToolCallResult denied = new Proto.ToolCallResult
                {
                    Success = false,
                    Status = Proto.ToolStatus.Denied,
                    CallId = pending.CallId,
                    ResultJson = "",
                    Error = "用户拒绝了该工具执行。",
                    Detail = new Proto.ToolError
                    {
                        Code = "APPROVAL_DENIED",
                        Message = "用户拒绝了该工具执行。",
                        Hint = "不要自动重试，也不要用其他工具绕过审批；把拒绝结果如实告知用户。",
                        Retryable = false,
                    },
                };

                denied.Trajectory.Add(ToProto(new TrajectoryEvent
                {
                    RunId = pending.RunId,
                    ThreadId = pending.ThreadId,
                    Type = "hitl",
                    Name = pending.ToolName,
                    Status = "denied",
                    ArgsJson = pending.ArgumentsJson,
                    StartedAt = IsoNow(),
                    EndedAt = IsoNow(),
                    MetadataJson = "{\"source\":\"csharp\"}",
                }));

                CacheResult(pending.CallId, pending.ArgumentsJson, denied);
                return denied;
            }

            string argumentsJson = pending.ArgumentsJson;
            if (effective == Proto.ApprovalDecision.Edit)
            {
                if (!pending.SupportsEditArgs)
                {
                    return ErrorResult(
                        pending.CallId,
                        "EDIT_NOT_SUPPORTED",
                        $"工具 {pending.ToolName} 不允许审批时修改参数。",
                        "请改用 APPROVE 或 REJECT。");
                }

                if (string.IsNullOrWhiteSpace(request.EditedArgumentsJson))
                {
                    return ErrorResult(
                        pending.CallId,
                        "INVALID_ARGUMENT",
                        "EDIT 决策必须提供 edited_arguments_json。",
                        "传入完整的新参数 JSON。");
                }

                argumentsJson = request.EditedArgumentsJson;
            }

            Proto.ToolCallRequest synthetic = new Proto.ToolCallRequest
            {
                ToolName = pending.ToolName,
                ArgumentsJson = argumentsJson,
                ThreadId = pending.ThreadId,
                CallId = pending.CallId,
                RunId = pending.RunId,
                ContractVersion = ContractVersion,
            };

            Proto.ToolCallResult result = await ExecuteAsync(
                synthetic,
                registration,
                argumentsJson,
                context.CancellationToken).ConfigureAwait(false);

            result.Trajectory.Add(ToProto(new TrajectoryEvent
            {
                RunId = pending.RunId,
                ThreadId = pending.ThreadId,
                Type = "hitl",
                Name = pending.ToolName,
                Status = result.Status == Proto.ToolStatus.Ok ? "approved" : "approved_failed",
                ArgsJson = argumentsJson,
                ResultJson = result.ResultJson,
                StartedAt = IsoNow(),
                EndedAt = IsoNow(),
                MetadataJson = "{\"source\":\"csharp\"}",
            }));

            return result;
        }

        private async Task<Proto.ToolCallResult> ExecuteAsync(
            Proto.ToolCallRequest request,
            ToolRegistration registration,
            string argumentsJson,
            CancellationToken cancellationToken)
        {
            Stopwatch stopwatch = Stopwatch.StartNew();
            string startedAt = IsoNow();
            List<TrajectoryEvent> trajectory = new List<TrajectoryEvent>
            {
                new TrajectoryEvent
                {
                    RunId = request.RunId,
                    ThreadId = request.ThreadId,
                    Type = "tool",
                    Name = request.ToolName,
                    Status = "started",
                    ArgsJson = argumentsJson,
                    StartedAt = startedAt,
                    MetadataJson = "{\"source\":\"csharp\"}",
                },
            };

            ToolResult result;
            try
            {
                using CancellationTokenSource timeout =
                    CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                timeout.CancelAfter(TimeSpan.FromMilliseconds(registration.Metadata.TimeoutMs));

                object?[] args = ToolArgumentBinder.Bind(
                    registration.Method,
                    argumentsJson,
                    timeout.Token);

                result = await registration.Invoker(args, timeout.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                result = ToolResult.Fail(
                    cancellationToken.IsCancellationRequested ? "CANCELLED" : "TIMEOUT",
                    cancellationToken.IsCancellationRequested
                        ? "工具执行已取消。"
                        : $"工具执行超过 {registration.Metadata.TimeoutMs} ms。",
                    "确认工具是否响应 CancellationToken，或拆分长任务。");
            }
            catch (ToolBindingException ex)
            {
                result = ToolResult.Fail(ex.Code, ex.Message, ex.Hint);
            }
            catch (JsonException ex)
            {
                result = ToolResult.Fail(
                    "INVALID_ARGUMENT",
                    $"arguments_json 不是合法 JSON：{ex.Message}",
                    "传入 UTF-8 JSON 对象，键名与工具参数一致。");
            }
            catch (Exception ex)
            {
                result = ToolResult.Fail(
                    "TOOL_EXCEPTION",
                    ex.Message,
                    "请检查工具实现；同一工具连续失败两次后应停止重试。");
            }

            stopwatch.Stop();
            trajectory.Add(new TrajectoryEvent
            {
                RunId = request.RunId,
                ThreadId = request.ThreadId,
                Type = "tool",
                Name = request.ToolName,
                Status = result.Success ? "ok" : "error",
                ArgsJson = argumentsJson,
                ResultJson = result.ResultJson,
                StartedAt = startedAt,
                EndedAt = IsoNow(),
                MetadataJson = "{\"source\":\"csharp\"}",
            });
            trajectory.AddRange(result.Trajectory);

            Proto.ToolCallResult response = new Proto.ToolCallResult
            {
                Success = result.Success,
                Status = result.Success ? Proto.ToolStatus.Ok : Proto.ToolStatus.Error,
                ResultJson = result.ResultJson,
                CallId = request.CallId,
                DurationMs = stopwatch.ElapsedMilliseconds,
            };

            if (!result.Success && result.Error is not null)
            {
                response.Error = result.Error.Message;
                response.Detail = new Proto.ToolError
                {
                    Code = result.Error.Code,
                    Message = result.Error.Message,
                    Hint = result.Error.Hint,
                    Retryable = result.Error.Retryable,
                };
            }

            foreach (TrajectoryEvent evt in trajectory)
            {
                response.Trajectory.Add(ToProto(evt));
            }

            CacheResult(request.CallId, argumentsJson, response);
            _logger?.LogDebug(
                "工具 {Tool} 执行完成：success={Success}, {Elapsed}ms",
                request.ToolName,
                result.Success,
                stopwatch.ElapsedMilliseconds);
            return response;
        }

        private bool TryGetCached(
            Proto.ToolCallRequest request,
            out Proto.ToolCallResult? cached,
            out bool conflict)
        {
            cached = null;
            conflict = false;

            if (string.IsNullOrWhiteSpace(request.CallId))
            {
                return false;
            }

            if (!CachedCalls.TryGetValue(request.CallId, out CachedToolCall? entry))
            {
                return false;
            }

            if (entry.ExpiresAtUtc < DateTime.UtcNow)
            {
                CachedCalls.TryRemove(request.CallId, out _);
                return false;
            }

            string hash = HashArguments(request.ArgumentsJson);
            if (entry.ArgumentsHash == hash)
            {
                cached = entry.Result.Clone();
                return true;
            }

            conflict = true;
            return true;
        }

        private static void CacheResult(string callId, string argumentsJson, Proto.ToolCallResult result)
        {
            if (string.IsNullOrWhiteSpace(callId))
            {
                return;
            }

            CachedCalls[callId] = new CachedToolCall
            {
                ArgumentsHash = HashArguments(argumentsJson),
                Result = result.Clone(),
                ExpiresAtUtc = DateTime.UtcNow.AddSeconds(IdempotencyTtlSeconds),
            };
        }

        private static string HashArguments(string argumentsJson)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(argumentsJson ?? "");
            byte[] hash = SHA256.HashData(bytes);
            return Convert.ToHexString(hash);
        }

        private static Proto.ToolCallResult ErrorResult(
            Proto.ToolCallRequest request,
            string code,
            string message,
            string hint)
        {
            return ErrorResult(request.CallId, code, message, hint);
        }

        private static Proto.ToolCallResult ErrorResult(
            string callId,
            string code,
            string message,
            string hint)
        {
            return new Proto.ToolCallResult
            {
                Success = false,
                Status = Proto.ToolStatus.Error,
                CallId = callId,
                ResultJson = "",
                Error = message,
                Detail = new Proto.ToolError
                {
                    Code = code,
                    Message = message,
                    Hint = hint,
                    Retryable = false,
                },
            };
        }

        private static Proto.ToolDescriptor ToProto(ToolMetadata tool)
        {
            return new Proto.ToolDescriptor
            {
                Name = tool.Name,
                DisplayName = tool.DisplayName,
                Description = tool.Description,
                Domain = tool.Domain,
                Skill = tool.Skill,
                ArgsSchemaJson = tool.ArgsSchemaJson,
                ResultSchemaJson = tool.ResultSchemaJson,
                SandboxLevel = ToProtoFromSandLevel(tool.SandboxLevel),
                IsWrite = tool.IsWrite,
                IsDestructive = tool.IsDestructive,
                SupportsEditArgs = tool.SupportsEditArgs,
                TimeoutMs = tool.TimeoutMs,
                Version = tool.Version,
            };
        }

        private static Proto.SandboxLevel ToProtoFromSandLevel(SdkSandboxLevel level)
        {
            return level switch
            {
                SdkSandboxLevel.Auto => Proto.SandboxLevel.Auto,
                SdkSandboxLevel.Confirm => Proto.SandboxLevel.Confirm,
                SdkSandboxLevel.AlwaysConfirm => Proto.SandboxLevel.AlwaysConfirm,
                _ => Proto.SandboxLevel.Unspecified,
            };
        }

        private static Proto.TrajectoryEvent ToProto(TrajectoryEvent evt)
        {
            return new Proto.TrajectoryEvent
            {
                EventId = evt.EventId,
                RunId = evt.RunId,
                ThreadId = evt.ThreadId,
                ParentId = evt.ParentId,
                Type = evt.Type,
                Name = evt.Name,
                Status = evt.Status,
                ArgsJson = evt.ArgsJson,
                ResultJson = evt.ResultJson,
                StartedAt = evt.StartedAt,
                EndedAt = evt.EndedAt,
                MetadataJson = evt.MetadataJson,
            };
        }

        private static bool IsCompatible(string remoteVersion)
        {
            if (string.IsNullOrWhiteSpace(remoteVersion))
            {
                return true;
            }

            string localMajor = ContractVersion.Split('.')[0];
            string remoteMajor = remoteVersion.Split('.')[0];
            return string.Equals(localMajor, remoteMajor, StringComparison.Ordinal);
        }

        private static string IsoNow()
        {
            return DateTime.UtcNow.ToString("O");
        }

        private sealed class PendingApproval
        {
            public string ApprovalId { get; init; } = "";
            public string CallId { get; init; } = "";
            public string ToolName { get; init; } = "";
            public string ArgumentsJson { get; init; } = "";
            public string ThreadId { get; init; } = "";
            public string RunId { get; init; } = "";
            public DateTime ExpiresAtUtc { get; init; }
            public bool SupportsEditArgs { get; init; }
        }

        private sealed class CachedToolCall
        {
            public string ArgumentsHash { get; init; } = "";
            public Proto.ToolCallResult Result { get; init; } = new Proto.ToolCallResult();
            public DateTime ExpiresAtUtc { get; init; }
        }
    }
}
