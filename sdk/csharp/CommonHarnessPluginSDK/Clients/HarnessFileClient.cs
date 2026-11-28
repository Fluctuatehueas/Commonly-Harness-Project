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
    /// 【Q1 作用】FileService 客户端：会话工作区文件列出 / 读取 / 写入。
    /// <para>无沙箱模式下文件落在 LangGraph 线程状态的 files 通道。</para>
    /// 【Q2 调用方】WPF 聊天区的"会话文件"面板。
    /// 【Q3 创建时机】应用启动时。
    /// 【Q4 释放】自行创建的 channel 由 Dispose 释放。
    /// 【Q5 线程约束】可被任意线程 await。
    /// </summary>
    public sealed class HarnessFileClient : IDisposable
    {
        private readonly Proto.FileService.FileServiceClient _client;
        private readonly GrpcChannel? _ownedChannel;

        public HarnessFileClient(GrpcChannel channel)
        {
            _client = new Proto.FileService.FileServiceClient(channel);
        }

        public HarnessFileClient(string address)
        {
            _ownedChannel = GrpcChannel.ForAddress(address);
            _client = new Proto.FileService.FileServiceClient(_ownedChannel);
        }

        /// <summary>列出会话工作区文件；Source 为 state(本地) 或 sandbox。</summary>
        public async Task<(string Source, IReadOnlyList<ThreadFileEntry> Files)> ListThreadFilesAsync(
            string userId,
            string threadId,
            CancellationToken cancellationToken = default)
        {
            Proto.ListThreadFilesResponse response = await _client.ListThreadFilesAsync(
                new Proto.ListThreadFilesRequest
                {
                    UserId = userId ?? "",
                    ThreadId = threadId ?? "",
                },
                cancellationToken: cancellationToken).ConfigureAwait(false);

            return (
                response.Source,
                response.Files
                    .Select(file => new ThreadFileEntry
                    {
                        Path = file.Path,
                        Size = file.Size,
                        ModifiedAt = file.ModifiedAt,
                    })
                    .ToList());
        }

        /// <summary>
        /// 读取文件内容
        /// </summary>
        /// <param name="userId"></param>
        /// <param name="threadId"></param>
        /// <param name="path"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<ThreadFileContent> ReadThreadFileAsync(
            string userId,
            string threadId,
            string path,
            CancellationToken cancellationToken = default)
        {
            Proto.ReadThreadFileResponse response = await _client.ReadThreadFileAsync(
                new Proto.ReadThreadFileRequest
                {
                    UserId = userId ?? "",
                    ThreadId = threadId ?? "",
                    Path = path ?? "",
                },
                cancellationToken: cancellationToken).ConfigureAwait(false);

            return new ThreadFileContent
            {
                Path = response.Path,
                Content = response.Content,
                Encoding = response.Encoding,
            };
        }

        /// <summary>
        /// 写文件内容
        /// </summary>
        /// <param name="userId"></param>
        /// <param name="threadId"></param>
        /// <param name="path"></param>
        /// <param name="content"></param>
        /// <param name="cancellationToken"></param>
        /// <returns></returns>
        public async Task<bool> WriteThreadFileAsync(
            string userId,
            string threadId,
            string path,
            string content,
            CancellationToken cancellationToken = default)
        {
            Proto.WriteThreadFileResponse response = await _client.WriteThreadFileAsync(
                new Proto.WriteThreadFileRequest
                {
                    UserId = userId ?? "",
                    ThreadId = threadId ?? "",
                    Path = path ?? "",
                    Content = content ?? "",
                },
                cancellationToken: cancellationToken).ConfigureAwait(false);
            return response.Success;
        }

        public void Dispose()
        {
            _ownedChannel?.Dispose();
        }
    }
}
