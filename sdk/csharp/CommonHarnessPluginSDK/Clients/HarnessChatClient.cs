using CommonHarnessPluginSDK.Models;
using CommonHarnessPluginSDK.Utils;
using Grpc.Core;
using Grpc.Net.Client;
using System.Net.Sockets;
using System.Runtime.CompilerServices;
using System.Text;
using Proto = Agent.Proto;

namespace CommonHarnessPluginSDK.Clients
{
    public sealed class HarnessChatClient : IDisposable
    {
        private readonly GrpcChannel _channel; // Grpc通道
        private readonly bool _ownsChannel; // 通道是否获取到
        private readonly Proto.ChatService.ChatServiceClient _client;

        /// <summary>客户端参数。</summary>
        public HarnessChatClientOptions Options { get; set; }

        /// <summary>当前会话的 thread_id（Python 侧用它隔离上下文与记忆）。</summary>
        public string ThreadId { get; set; }

        /// <summary>后端当前是否可达。由 ProbeAsync / 流式调用结果维护。</summary>
        public bool IsAvailable { get; private set; }




        private bool _disposed;
        public HarnessChatClient(HarnessChatClientOptions? options = null, GrpcChannel? channel = null)
        {
            Options = options ?? new HarnessChatClientOptions();
            _channel = channel ?? GrpcChannel.ForAddress(Options.Address);
            _ownsChannel = channel is null;
            _client = new Proto.ChatService.ChatServiceClient(_channel);

            if (string.IsNullOrWhiteSpace(Options.ThreadId))
            {
                ThreadId = Guid.NewGuid().ToString("N");
            }
            else
            {
                ThreadId = Options.ThreadId;
            }
        }
        private void SetAvailable(bool available)
        {
            if (IsAvailable == available)
            {
                return;
            }

            IsAvailable = available;
            AvailabilityChanged?.Invoke(this, available);
        }
        /// <summary>
        /// 探测 Python 后端是否可达（TCP 连接，不发起真实对话，不消耗 token）。
        /// </summary>
        public async Task<bool> HealthProbeAsync(TimeSpan timeout, CancellationToken cancelToken = default)
        {
            bool ready = false;
            using CancellationTokenSource linked = CancellationTokenSource.CreateLinkedTokenSource(cancelToken); // 创建链接的取消令牌，外部token取消了，这个也跟着取消
            linked.CancelAfter(timeout); // timeout后自动取消

            try
            {
                using TcpClient client = new TcpClient();
                await client.ConnectAsync(Options.Host, Options.Port, linked.Token).ConfigureAwait(false); // task结束后，后续代码仍在线程池跑，少一次线程调度
                ready = true;
            }
            catch (OperationCanceledException)
            {
                ready = false;
            }
            catch (SocketException)
            {
                ready = false;
            }
            catch (Exception)
            {
                ready = false;
            }
            SetAvailable(ready);
            return ready;
        }


        /// <summary>
        /// 发送一条消息并以流的方式返回事件序列。
        /// <para>gRPC 层错误不抛出，而是作为 <see cref="HarnessChatEventKind.Error"/> 事件返回，便于界面渲染；
        /// 调用方主动取消时仍抛 <see cref="OperationCanceledException"/>。</para>
        /// </summary>
        public IAsyncEnumerable<HarnessChatEvent> StreamAsync(
            string msg,
            string? thread_id = null,
            HarnessChatMode mode = HarnessChatMode.Chat,
            CancellationToken cancellationToken = default)
        {
            string effectiveThread = string.IsNullOrWhiteSpace(thread_id) ? ThreadId : thread_id;
            Proto.ChatRequest request = new Proto.ChatRequest
            {
                Humanmsg = msg ?? "",
                ThreadId = effectiveThread,
                Mode = mode == HarnessChatMode.Task ? Proto.ChatMode.Task : Proto.ChatMode.Chat,
                ContractVersion = ConstantVariable.ContraceVersion, // 版本
                UserId = Options.UserId,
                Username = Options.Username,
            };
            return StreamCore(
                () => _client.Chat(
                    request,
                    deadline: DateTime.UtcNow.AddMinutes(Options.DeadlineMinutes),
                    cancellationToken: cancellationToken),
                cancellationToken);
        }

        /// <summary>
        /// HITL 恢复：把用户在中断卡片上提交的 resume_json 发给 ResumeChat，继续流式输出。
        /// </summary>
        public IAsyncEnumerable<HarnessChatEvent> ResumeStreamAsync(
            string threadId,
            string resumeJson,
            HarnessChatMode mode,
            CancellationToken cancellationToken = default)
        {
            Proto.ResumeChatRequest request = new Proto.ResumeChatRequest
            {
                ThreadId = string.IsNullOrWhiteSpace(threadId) ? ThreadId : threadId,
                ResumeJson = resumeJson ?? "",
                UserId = Options.UserId,
                Username = Options.Username,
                ContractVersion = ConstantVariable.ContraceVersion,
                Mode = mode == HarnessChatMode.Task ? Proto.ChatMode.Task : Proto.ChatMode.Chat,
            };

            return StreamCore(
                () => _client.ResumeChat(
                    request,
                    deadline: DateTime.UtcNow.AddMinutes(Options.DeadlineMinutes),
                    cancellationToken: cancellationToken),
                cancellationToken);
        }


        /// <summary>
        /// 非流式便捷入口：收集完整回复文本（适合冒烟测试与脚本化调用）。
        /// </summary>
        public async Task<string> ChatAsync(
            string message,
            HarnessChatMode mode,
            string? threadId = null,
            CancellationToken cancellationToken = default)
        {
            StringBuilder builder = new StringBuilder();
            await foreach (HarnessChatEvent evt in
                StreamAsync(message, threadId, mode, cancellationToken).ConfigureAwait(false))
            {
                if (evt.Kind == HarnessChatEventKind.TextDelta)
                {
                    builder.Append(evt.Text);
                }
            }

            return builder.ToString();
        }
        /// <summary>重置会话 ID（新建会话时调用）。</summary>
        public void ResetThread(string? threadId = null)
        {
            ThreadId = string.IsNullOrWhiteSpace(threadId) ? Guid.NewGuid().ToString("N") : threadId;
        }








        // =======================================

        private async IAsyncEnumerable<HarnessChatEvent> StreamCore(Func<AsyncServerStreamingCall<Proto.ChatChunk>> startCall, [EnumeratorCancellation] CancellationToken cancellationToken)
        {
            AsyncServerStreamingCall<Proto.ChatChunk>? call = null;
            HarnessChatEvent? startError = null;

            try
            {
                call = startCall();
                SetAvailable(true);
            }
            catch (RpcException ex)
            {
                SetAvailable(false);
                startError = ToErrorEvent(ex);
            }

            if (call is null)
            {
                yield return startError ?? new HarnessChatEvent
                {
                    Kind = HarnessChatEventKind.Error,
                    ErrorCode = "CHAT_START_FAILED",
                    ErrorMessage = "无法发起 Chat 流式调用。",
                };
                yield break;
            }
            using (call)
            {
                while (true)
                {
                    Proto.ChatChunk? chunk = null;
                    HarnessChatEvent? streamError = null;
                    try
                    {
                        bool moved = await call.ResponseStream.MoveNext(cancellationToken).ConfigureAwait(false);
                        if (!moved)
                        {
                            break;
                        }

                        chunk = call.ResponseStream.Current;
                    }
                    catch (OperationCanceledException)
                    {
                        throw;
                    }
                    catch (RpcException ex)
                    {
                        if (ex.StatusCode == StatusCode.Cancelled)
                        {
                            throw new OperationCanceledException(ex.Message, ex, cancellationToken);
                        }

                        SetAvailable(false);
                        streamError = ToErrorEvent(ex);
                    }

                    if (streamError is not null)
                    {
                        yield return streamError;
                        yield break;
                    }

                    HarnessChatEvent? evt = chunk is null ? null : Convert(chunk);
                    if (evt is not null)
                    {
                        yield return evt;
                    }
                }
            }
        }

        /// <summary>
        /// 依照Proto，将返回的chunk根据类型进行C#强类型转换
        /// </summary>
        /// <param name="chunk"></param>
        /// <returns></returns>
        private static HarnessChatEvent? Convert(Proto.ChatChunk chunk)
        {
            switch (chunk.PayloadCase)
            {
                case Proto.ChatChunk.PayloadOneofCase.Chunk:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.TextDelta,
                        Text = chunk.Chunk,
                    };

                case Proto.ChatChunk.PayloadOneofCase.Message:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.Message,
                        MessageId = chunk.Message.MessageId,
                        Role = chunk.Message.Role.ToString(),
                        Content = chunk.Message.Content,
                        Name = chunk.Message.Name,
                        CallId = chunk.Message.ToolCallId,
                        CreatedAt = chunk.Message.CreatedAt,
                    };

                case Proto.ChatChunk.PayloadOneofCase.ToolCall:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.ToolCall,
                        CallId = chunk.ToolCall.CallId,
                        RunId = chunk.ToolCall.RunId,
                        ToolName = chunk.ToolCall.ToolName,
                        ArgumentsJson = chunk.ToolCall.ArgumentsJson,
                        ThreadId = chunk.ToolCall.ThreadId,
                        IsPartial = chunk.ToolCall.IsPartial,
                    };

                case Proto.ChatChunk.PayloadOneofCase.ToolResult:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.ToolResult,
                        CallId = chunk.ToolResult.CallId,
                        ToolName = chunk.ToolResult.ToolName,
                        ResultJson = chunk.ToolResult.ResultJson,
                        ErrorJson = chunk.ToolResult.ErrorJson,
                        ToolStatus = (HarnessToolStatus)(int)chunk.ToolResult.Status,
                        ThreadId = chunk.ToolResult.ThreadId,
                    };

                case Proto.ChatChunk.PayloadOneofCase.Trajectory:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.Trajectory,
                        EventId = chunk.Trajectory.EventId,
                        RunId = chunk.Trajectory.RunId,
                        ThreadId = chunk.Trajectory.ThreadId,
                        ParentId = chunk.Trajectory.ParentId,
                        TrajectoryType = chunk.Trajectory.Type,
                        ToolName = chunk.Trajectory.Name,
                        TrajectoryStatus = chunk.Trajectory.Status,
                        TrajectoryArgsJson = chunk.Trajectory.ArgsJson,
                        TrajectoryResultJson = chunk.Trajectory.ResultJson,
                        StartedAt = chunk.Trajectory.StartedAt,
                        EndedAt = chunk.Trajectory.EndedAt,
                        MetadataJson = chunk.Trajectory.MetadataJson,
                    };

                case Proto.ChatChunk.PayloadOneofCase.Done:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.Done,
                        DoneMessage = chunk.Done.Msg,
                        ThreadId = chunk.Done.ThreadId,
                        FinishReason = chunk.Done.FinishReason,
                        UsageJson = chunk.Done.UsageJson,
                        ChatStatus = (HarnessChatStatus)(int)chunk.Done.Status,
                    };

                case Proto.ChatChunk.PayloadOneofCase.Error:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.Error,
                        ErrorCode = chunk.Error.Code,
                        ErrorMessage = chunk.Error.Message,
                        ErrorHint = chunk.Error.Hint,
                        Retryable = chunk.Error.Retryable,
                    };

                case Proto.ChatChunk.PayloadOneofCase.Reasoning:
                    return new HarnessChatEvent
                    {
                        Kind = HarnessChatEventKind.Reasoning,
                        ReasoningDelta = chunk.Reasoning.Delta,
                        ReasoningStepId = chunk.Reasoning.StepId,
                        RunId = chunk.Reasoning.RunId,
                        ThreadId = chunk.Reasoning.ThreadId,
                    };

                case Proto.ChatChunk.PayloadOneofCase.Hitl:
                    return ConvertHitl(chunk.Hitl);

                case Proto.ChatChunk.PayloadOneofCase.Plan:
                    return ConvertPlan(chunk.Plan);

                default:
                    return null;
            }
        }

        /// <summary>
        /// 【Q1 作用】把 proto 的 <c>HitlEvent</c> 投影为强类型事件：fields/options 直接变成列表，
        ///           UI 不需要再解析 JSON 字符串。
        /// 【Q2 调用方】Convert 的 Hitl 分支。
        /// 【Q3 创建时机】收到人工介入请求时。
        /// 【Q4 释放】随事件对象回收。
        /// 【Q5 线程约束】gRPC 消费线程。
        /// </summary>
        private static HarnessChatEvent ConvertHitl(Proto.HitlEvent hitl)
        {
            List<HarnessHitlField> fields = new List<HarnessHitlField>(hitl.Fields.Count);
            foreach (Proto.HitlField raw in hitl.Fields)
            {
                List<HarnessHitlOption> fieldOptions = new List<HarnessHitlOption>(raw.Options.Count);
                foreach (Proto.HitlOption option in raw.Options)
                {
                    fieldOptions.Add(ToHitlOption(option));
                }

                fields.Add(new HarnessHitlField
                {
                    Name = raw.Name,
                    Label = raw.Label,
                    Type = raw.Type,
                    Required = raw.Required,
                    DefaultJson = raw.DefaultJson,
                    Placeholder = raw.Placeholder,
                    Description = raw.Description,
                    Options = fieldOptions,
                });
            }

            List<HarnessHitlOption> options = new List<HarnessHitlOption>(hitl.Options.Count);
            foreach (Proto.HitlOption option in hitl.Options)
            {
                options.Add(ToHitlOption(option));
            }

            return new HarnessChatEvent
            {
                Kind = HarnessChatEventKind.Hitl,
                HitlId = hitl.HitlId,
                HitlKind = hitl.Kind,
                HitlTitle = hitl.Title,
                HitlDescription = hitl.Description,
                HitlFields = fields,
                HitlOptions = options,
                HitlAllowMultiple = hitl.AllowMultiple,
                HitlAllowFreeText = hitl.AllowFreeText,
                HitlSubmitLabel = hitl.SubmitLabel,
                HitlCancelLabel = hitl.CancelLabel,
                HitlResumeSchemaJson = hitl.ResumeSchemaJson,
                HitlMetadataJson = hitl.MetadataJson,
                ThreadId = hitl.ThreadId,
            };
        }

        /// <summary>
        ///  把 proto 的计划全量快照投影为强类型事件（steps 直接变成 DTO 列表）。
        /// 【线程约束】gRPC 消费线程。
        /// </summary>
        private static HarnessChatEvent ConvertPlan(Proto.PlanEvent plan)
        {
            List<PlanStepDto> steps = new List<PlanStepDto>(plan.Steps.Count);
            foreach (Proto.PlanStepInfo step in plan.Steps)
            {
                steps.Add(new PlanStepDto
                {
                    StepId = step.StepId,
                    Domain = step.Domain,
                    Goal = step.Goal,
                    DependsOn = step.DependsOn,
                    ExpectedTools = step.ExpectedTools,
                    Acceptance = step.Acceptance,
                    IsVisualCheck = step.IsVisualCheck,
                    Status = step.Status,
                    Error = step.Error,
                });
            }

            return new HarnessChatEvent
            {
                Kind = HarnessChatEventKind.Plan,
                PlanId = plan.PlanId,
                PlanVersion = plan.Version,
                PlanStatus = plan.Status,
                ReplanReason = plan.ReplanReason,
                PlanCreatedAt = plan.CreatedAt,
                PlanSteps = steps,
                RunId = plan.RunId,
                ThreadId = plan.ThreadId,
            };
        }

        private static HarnessHitlOption ToHitlOption(Proto.HitlOption option)
        {
            return new HarnessHitlOption
            {
                Value = option.Value,
                Label = string.IsNullOrWhiteSpace(option.Label) ? option.Value : option.Label,
                Description = option.Description,
                Recommended = option.Recommended,
            };
        }

        private static HarnessChatEvent ToErrorEvent(RpcException ex)
        {
            return new HarnessChatEvent
            {
                Kind = HarnessChatEventKind.Error,
                ErrorCode = ex.StatusCode.ToString(),
                ErrorMessage = ex.Status.Detail,
                ErrorHint = "请确认 Python 后端已启动（python main.py --enable-grpc），端口 50051 可达。",
                Retryable = ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded,
            };
        }








        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            if (_ownsChannel)
            {
                _channel.Dispose();
            }
        }
        #region Events

        /// <summary>可用性变化事件（UI 用它刷新状态灯）。</summary>
        public event EventHandler<bool>? AvailabilityChanged;
        #endregion
    }
}
