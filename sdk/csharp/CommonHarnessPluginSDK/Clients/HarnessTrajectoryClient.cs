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
    /// 【Q1 作用】TrajectoryService 客户端：按会话聚合 run / 事件 / Token / 耗时。
    /// <para>数据由 Python TrajectoryMiddleware 写入 PostgreSQL；C# 只读查询。</para>
    /// 【Q2 调用方】WPF 的 AIChatViewModel（每轮结束与切换会话时刷新轨迹）。
    /// 【Q3 创建时机】应用启动时。
    /// 【Q4 释放】自行创建的 channel 由 Dispose 释放。
    /// 【Q5 线程约束】可被任意线程 await。
    /// </summary>
    public sealed class HarnessTrajectoryClient : IDisposable
    {
        private readonly Proto.TrajectoryService.TrajectoryServiceClient _client;
        private readonly GrpcChannel? _ownedChannel;

        public HarnessTrajectoryClient(GrpcChannel channel)
        {
            _client = new Proto.TrajectoryService.TrajectoryServiceClient(channel);
        }

        public HarnessTrajectoryClient(string address)
        {
            _ownedChannel = GrpcChannel.ForAddress(address);
            _client = new Proto.TrajectoryService.TrajectoryServiceClient(_ownedChannel);
        }

        /// <summary>
        /// 异步获取会话轨迹信息
        /// </summary>
        /// <param name="userId">用户ID</param>
        /// <param name="threadId">会话ID</param>
        /// <param name="cancellationToken">取消任务令牌</param>
        /// <returns></returns>
        public async Task<TrajectorySnapshot> GetTrajectoryAsync(
            string userId,
            string threadId,
            CancellationToken cancellationToken = default)
        {
            Proto.GetTrajectoryResponse response = await _client.GetTrajectoryAsync(
                new Proto.GetTrajectoryRequest { UserId = userId ?? "", ThreadId = threadId ?? "" },
                cancellationToken: cancellationToken).ConfigureAwait(false);

            return new TrajectorySnapshot
            {
                ThreadId = response.ThreadId,
                LatestRunId = response.LatestRunId,
                EventCount = response.EventCount,
                GeneratedAt = response.GeneratedAt,
                Runs = response.Runs.Select(ToRun).ToList(),
            };
        }

        private static TrajectoryRunRecord ToRun(Proto.TrajectoryRun run)
        {
            return new TrajectoryRunRecord
            {
                RunId = run.RunId,
                StartedAt = run.StartedAt,
                EndedAt = run.EndedAt,
                Status = run.Status,
                SummaryJson = run.SummaryJson,
                Events = run.Events.Select(ToEvent).ToList(),
            };
        }

        private static TrajectoryEventRecord ToEvent(Proto.TrajectoryEvent evt)
        {
            return new TrajectoryEventRecord
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

        public void Dispose()
        {
            _ownedChannel?.Dispose();
        }
    }
}
