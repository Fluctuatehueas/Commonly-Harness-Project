﻿using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommonHarnessPluginSDK.Models;
using Grpc.Net.Client;
using Proto = Agent.Proto;

namespace CommonHarnessPluginSDK.Clients
{
    /// <summary>
    /// 【Q1 作用】SessionService 客户端：会话索引、历史、新建 / 重命名 / 置顶 / 删除全部走 gRPC，
    ///           C# 不直连 PostgreSQL。
    /// 【Q2 调用方】WPF 的 HarnessService 与 AIChatViewModel。
    /// 【Q3 创建时机】应用启动时（可共享 HarnessChatClient 的 GrpcChannel）。
    /// 【Q4 释放】若自行创建 channel 则 Dispose 释放；共享 channel 时由宿主释放。
    /// 【Q5 线程约束】方法可被任意线程 await。
    /// </summary>
    public sealed class HarnessSessionClient : IDisposable
    {
        private readonly Proto.SessionService.SessionServiceClient _client;
        private readonly GrpcChannel? _ownedChannel;

        public HarnessSessionClient(GrpcChannel channel)
        {
            _client = new Proto.SessionService.SessionServiceClient(channel);
        }

        public HarnessSessionClient(string address)
        {
            _ownedChannel = GrpcChannel.ForAddress(address);
            _client = new Proto.SessionService.SessionServiceClient(_ownedChannel);
        }

        /// <summary>列出会话索引；includeMessages 为 true 时一次性带回历史消息。</summary>
        public async Task<IReadOnlyList<SessionSummary>> ListSessionsAsync(
            string userId,
            bool includeMessages = false,
            int messagesLimitPerSession = 0,
            bool includeUsage = true,
            CancellationToken cancellationToken = default)
        {
            Proto.ListSessionsResponse response = await _client.ListSessionsAsync(
                new Proto.ListSessionsRequest
                {
                    UserId = userId ?? "",
                    IncludeMessages = includeMessages,
                    MessagesLimitPerSession = messagesLimitPerSession,
                    IncludeUsage = includeUsage,
                },
                cancellationToken: cancellationToken).ConfigureAwait(false);

            return response.Sessions.Select(ToSummary).ToList();
        }

        /// <summary>按 thread_id 拉取历史（支持 limit 与 before_message_id 游标）。</summary>
        public async Task<SessionHistory> GetHistoryAsync(
            string userId,
            string threadId,
            int limit = 0,
            string beforeMessageId = "",
            CancellationToken cancellationToken = default)
        {
            Proto.GetSessionHistoryResponse response = await _client.GetSessionHistoryAsync(
                new Proto.GetSessionHistoryRequest
                {
                    UserId = userId ?? "",
                    ThreadId = threadId ?? "",
                    Limit = limit,
                    BeforeMessageId = beforeMessageId ?? "",
                },
                cancellationToken: cancellationToken).ConfigureAwait(false);

            return new SessionHistory
            {
                Session = response.Session is null ? null : ToSummary(response.Session),
                Messages = response.Messages.Select(ToMessage).ToList(),
                InterruptJson = response.InterruptJson,
                SessionUsageJson = response.SessionUsageJson,
            };
        }

        /// <summary>
        /// 创建新会话
        /// </summary>
        /// <param name="userId"></param>
        /// <param name="title"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<SessionSummary> CreateSessionAsync(
            string userId,
            string title,
            CancellationToken cancellationToken = default)
        {
            Proto.SessionInfo response = await _client.CreateSessionAsync(
                new Proto.CreateSessionRequest { UserId = userId ?? "", Title = title ?? "" },
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return ToSummary(response);
        }

        /// <summary>
        /// 重命名新会话
        /// </summary>
        /// <param name="userId"></param>
        /// <param name="threadId"></param>
        /// <param name="title"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<SessionSummary> RenameSessionAsync(
            string userId,
            string threadId,
            string title,
            CancellationToken cancellationToken = default)
        {
            Proto.SessionInfo response = await _client.RenameSessionAsync(
                new Proto.RenameSessionRequest
                {
                    UserId = userId ?? "",
                    ThreadId = threadId ?? "",
                    Title = title ?? "",
                },
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return ToSummary(response);
        }


        /// <summary>
        /// 置顶/不置顶会话
        /// </summary>
        /// <param name="userId"></param>
        /// <param name="threadId"></param>
        /// <param name="pinned"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<SessionSummary> SetPinnedAsync(
            string userId,
            string threadId,
            bool pinned,
            CancellationToken cancellationToken = default)
        {
            Proto.SessionInfo response = await _client.SetSessionPinnedAsync(
                new Proto.SetSessionPinnedRequest
                {
                    UserId = userId ?? "",
                    ThreadId = threadId ?? "",
                    Pinned = pinned,
                },
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return ToSummary(response);
        }
        /// <summary>
        /// 删除会话
        /// </summary>
        /// <param name="userId"></param>
        /// <param name="threadId"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<bool> DeleteSessionAsync(
            string userId,
            string threadId,
            CancellationToken cancellationToken = default)
        {
            Proto.DeleteSessionResponse response = await _client.DeleteSessionAsync(
                new Proto.DeleteSessionRequest { UserId = userId ?? "", ThreadId = threadId ?? "" },
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return response.Success;
        }

        /// <summary>
        /// 将契约内容强类型转换
        /// </summary>
        /// <param name="info"></param>
        /// <returns></returns>
        internal static SessionSummary ToSummary(Proto.SessionInfo info)
        {
            return new SessionSummary
            {
                ThreadId = info.ThreadId,
                Title = info.Title,
                CreatedAt = info.CreatedAt,
                UpdatedAt = info.UpdatedAt,
                MessageCount = info.MessageCount,
                Pinned = info.Pinned,
                LastMessagePreview = info.LastMessagePreview,
                UsageJson = info.UsageJson,
                Messages = info.Messages.Select(ToMessage).ToList(),
            };
        }


        internal static ChatMessageRecord ToMessage(Proto.ChatMessage message)
        {
            return new ChatMessageRecord
            {
                MessageId = message.MessageId,
                Role = (HarnessMessageRole)(int)message.Role,
                Content = message.Content,
                ToolCalls = message.ToolCalls
                    .Select(call => new ToolCallRecord
                    {
                        CallId = call.CallId,
                        ToolName = call.ToolName,
                        ArgumentsJson = call.ArgumentsJson,
                    })
                    .ToList(),
                ToolCallId = message.ToolCallId,
                Name = message.Name,
                AdditionalKwargsJson = message.AdditionalKwargsJson,
                CreatedAt = message.CreatedAt,
            };
        }

        public void Dispose()
        {
            _ownedChannel?.Dispose();
        }
    }
}
