﻿using System;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace CommonHarnessPluginSDK.Services
{
    /// <summary>
    /// 【Q1 作用】C# ToolService 的进程内宿主：用 Kestrel（h2c / HTTP2）监听本机端口，
    ///           把 <see cref="ToolGrpcService"/> 暴露给 Python 的 ToolService 客户端。
    /// <para>WPF 是 WinExe，本宿主只负责 StartAsync / StopAsync，不接管应用生命周期。</para>
    /// 【Q2 调用方】WPF 的 HarnessService（App.OnStartup）或冒烟控制台。
    /// 【Q3 创建时机】应用启动时。
    /// 【Q4 释放】应用退出前必须 await <see cref="StopAsync"/> 或 <see cref="DisposeAsync"/>。
    /// 【Q5 线程约束】StartAsync / StopAsync 可被任意线程 await；服务内部并发由 gRPC 线程池处理。
    /// </summary>
    public sealed class HarnessToolServer : IAsyncDisposable
    {
        private readonly HarnessToolServerOptions _options;
        private WebApplication? _app;
        private bool _disposed;

        public HarnessToolServer(HarnessToolServerOptions? options = null)
        {
            _options = options ?? new HarnessToolServerOptions();
            Approvals = new ToolApprovalBroker();
        }

        /// <summary>审批策略中枢：宿主界面可订阅事件或替换 DecisionProvider。</summary>
        public ToolApprovalBroker Approvals { get; }

        /// <summary>是否已启动。</summary>
        public bool IsRunning { get; private set; }

        /// <summary>实际监听端口（启动后有效）。</summary>
        public int Port { get; private set; }

        /// <summary>
        /// 启动 gRPC 宿主。
        /// </summary>
        /// <exception cref="InvalidOperationException">重复启动时抛出。</exception>
        /// <exception cref="System.IO.IOException">端口被占用时由 Kestrel 抛出。</exception>
        public async Task StartAsync(CancellationToken cancellationToken = default)
        {
            if (IsRunning)
            {
                throw new InvalidOperationException("ToolService 已经启动。");
            }

            WebApplicationBuilder builder = WebApplication.CreateBuilder(new WebApplicationOptions
            {
                ContentRootPath = AppContext.BaseDirectory,
            });

            // 禁止加载外部 hosting startup 程序集，避免 WPF 进程被注入额外启动逻辑。
            builder.WebHost.UseSetting(WebHostDefaults.PreventHostingStartupKey, "true");
            builder.WebHost.ConfigureKestrel(kestrel =>
            {
                kestrel.ListenLocalhost(_options.Port, listen =>
                {
                    listen.Protocols = HttpProtocols.Http2;
                });
            });

            builder.Logging.SetMinimumLevel(_options.MinimumLogLevel);
            builder.Services.AddSingleton(Approvals);
            builder.Services.AddGrpc();

            _app = builder.Build();
            _app.MapGrpcService<ToolGrpcService>();

            try
            {
                await _app.StartAsync(cancellationToken).ConfigureAwait(false);
            }
            catch
            {
                await _app.DisposeAsync().ConfigureAwait(false);
                _app = null;
                throw;
            }

            ServerAddressesFeature? addresses = _app.Services
                .GetService<Microsoft.AspNetCore.Hosting.Server.IServer>()
                ?.Features.Get<ServerAddressesFeature>();
            Port = _options.Port;
            if (addresses is not null)
            {
                foreach (string address in addresses.Addresses)
                {
                    if (Uri.TryCreate(address, UriKind.Absolute, out Uri? uri))
                    {
                        Port = uri.Port;
                        break;
                    }
                }
            }

            IsRunning = true;
            _options.LogAction?.Invoke($"[ToolService] 已启动：127.0.0.1:{Port} (h2c)");
        }

        /// <summary>
        /// 停止 gRPC 宿主。
        /// </summary>
        public async Task StopAsync(CancellationToken cancellationToken = default)
        {
            if (_app is null)
            {
                IsRunning = false;
                return;
            }

            try
            {
                await _app.StopAsync(cancellationToken).ConfigureAwait(false);
            }
            finally
            {
                await _app.DisposeAsync().ConfigureAwait(false);
                _app = null;
                IsRunning = false;
                _options.LogAction?.Invoke("[ToolService] 已停止。");
            }
        }

        public async ValueTask DisposeAsync()
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            await StopAsync().ConfigureAwait(false);
        }
    }

    /// <summary>
    /// ToolService 宿主参数。
    /// </summary>
    public sealed class HarnessToolServerOptions
    {
        /// <summary>监听端口（本机回环）。默认 50052，与 Python HARNESS_TOOL_SERVICE_ADDRESS 一致。</summary>
        public int Port { get; set; } = 50052;

        /// <summary>宿主日志最小级别；默认 Warning，避免 WPF 输出刷屏。</summary>
        public LogLevel MinimumLogLevel { get; set; } = LogLevel.Warning;

        /// <summary>可选的日志回调（宿主状态变化时输出一行）。</summary>
        public Action<string>? LogAction { get; set; }
    }
}
