using CommonHarnessPluginSDK.Models;
using System;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace CommonHarnessPluginSDK.Utils
{
    /// <summary>
    /// 统一调用器：把同步/异步、void/值/字符串/ToolResult 的返回值归一化为 ToolResult。
    /// 任何异常都在这里转换为结构化错误，不穿透 gRPC 边界。
    /// </summary>
    public static class ToolInvoker
    {
        public static async Task<ToolResult> InvokeAsync(
            MethodInfo method,
            object? instance,
            object?[] args,
            CancellationToken cancellationToken)
        {
            if (!method.IsStatic && instance is null)
            {
                return ToolResult.Fail(
                    "TOOL_NOT_READY",
                    $"工具 {method.Name} 的宿主实例尚未就绪（通常表示主界面还未创建）。",
                    "等待主界面加载完成后重试。",
                    retryable: true);
            }

            object? raw;
            try
            {
                raw = method.Invoke(method.IsStatic ? null : instance, args);
            }
            catch (TargetInvocationException ex)
            {
                return MapException(ex.InnerException ?? ex);
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }

            try
            {
                raw = await NormalizeAsync(raw).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                return ToolResult.Fail("CANCELLED", "工具执行已取消。");
            }
            catch (TargetInvocationException ex)
            {
                return MapException(ex.InnerException ?? ex);
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }

            if (raw is ToolResult result)
            {
                return result;
            }

            if (raw is null)
            {
                return ToolResult.Ok("{}");
            }

            if (raw is string text)
            {
                if (string.IsNullOrWhiteSpace(text))
                {
                    return ToolResult.Ok("\"\"");
                }

                if (IsJson(text))
                {
                    return ToolResult.Ok(text);
                }

                return ToolResult.Ok(JsonSerializer.Serialize(text, ToolArgumentBinder.JsonOptions));
            }

            return ToolResult.Ok(JsonSerializer.Serialize(raw, raw.GetType(), ToolArgumentBinder.JsonOptions));
        }

        private static async Task<object?> NormalizeAsync(object? raw)
        {
            if (raw is null)
            {
                return null;
            }
            // 异步任务结果返回
            if (raw is Task task)
            {
                await task.ConfigureAwait(false);
                PropertyInfo? resultProperty = task.GetType().GetProperty("Result");
                return resultProperty?.GetValue(task);
            }
            // 后台线程结果返回
            if (raw is ValueTask valueTask)
            {
                await valueTask.ConfigureAwait(false);
                return null;
            }
            // 同步调用返回
            Type type = raw.GetType();
            if (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(ValueTask<>))
            {
                MethodInfo? asTask = type.GetMethod("AsTask");
                if (asTask?.Invoke(raw, null) is Task valueTaskTask)
                {
                    await valueTaskTask.ConfigureAwait(false);
                    return valueTaskTask.GetType().GetProperty("Result")?.GetValue(valueTaskTask);
                }
            }

            return raw;
        }

        private static ToolResult MapException(Exception ex)
        {
            if (ex is OperationCanceledException)
            {
                return ToolResult.Fail("CANCELLED", "工具执行已取消。");
            }

            if (ex is ToolBindingException binding)
            {
                return ToolResult.Fail(binding.Code, binding.Message, binding.Hint);
            }

            return ToolResult.Fail(
                "TOOL_EXCEPTION",
                ex.Message,
                "请检查工具实现或参数；同一工具连续失败两次后应停止重试。");
        }

        private static bool IsJson(string text)
        {
            try
            {
                using JsonDocument document = JsonDocument.Parse(text);
                return true;
            }
            catch (JsonException)
            {
                return false;
            }
        }
    }
}
