﻿using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;

namespace CommonHarnessPluginSDK.Utils
{
    /// <summary>
    /// 参数绑定错误；Code 与 ToolError.Code 对齐。
    /// </summary>
    public sealed class ToolBindingException : Exception
    {
        public string Code { get; }
        public string Hint { get; }

        public ToolBindingException(string code, string message, string hint = "")
            : base(message)
        {
            Code = code;
            Hint = hint;
        }
    }

    /// <summary>
    /// 把 arguments_json 绑定到方法参数。
    /// 支持 JSON 对象（按参数名，忽略大小写）、JSON 数组（按位置）、可选参数默认值、
    /// CancellationToken 注入、枚举字符串、Guid/DateTime、数组/List 与嵌套 DTO。
    /// </summary>
    public static class ToolArgumentBinder
    {
        public static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
            NumberHandling = JsonNumberHandling.AllowReadingFromString,
            Converters =
            {
                new JsonStringEnumConverter(),
                new TimeSpanJsonConverter(),
            },
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        };

        public static object?[] Bind(MethodInfo method, string? argumentsJson, CancellationToken cancellationToken)
        {
            ParameterInfo[] parameters = method.GetParameters();
            string json = string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson;

            using JsonDocument document = JsonDocument.Parse(json);
            JsonElement root = document.RootElement;

            if (root.ValueKind == JsonValueKind.Array)
            {
                return BindPositional(parameters, root, cancellationToken);
            }

            if (root.ValueKind != JsonValueKind.Object)
            {
                throw new ToolBindingException(
                    "INVALID_ARGUMENT",
                    "arguments_json 必须是 JSON 对象或数组。",
                    "对象形态按键名绑定，数组形态按参数顺序绑定。");
            }

            Dictionary<string, JsonElement> properties = new Dictionary<string, JsonElement>(
                StringComparer.OrdinalIgnoreCase);
            foreach (JsonProperty property in root.EnumerateObject())
            {
                properties[property.Name] = property.Value;
            }

            object?[] args = new object?[parameters.Length];
            for (int i = 0; i < parameters.Length; i++)
            {
                ParameterInfo parameter = parameters[i];
                if (parameter.ParameterType == typeof(CancellationToken))
                {
                    args[i] = cancellationToken;
                    continue;
                }

                if (IsParamArray(parameter))
                {
                    if (properties.TryGetValue(parameter.Name ?? "", out JsonElement arrayElement)
                        && arrayElement.ValueKind == JsonValueKind.Array)
                    {
                        args[i] = ConvertElement(arrayElement, parameter.ParameterType, parameter.Name);
                        continue;
                    }

                    args[i] = EmptyArray(parameter.ParameterType);
                    continue;
                }

                if (!properties.TryGetValue(parameter.Name ?? "", out JsonElement element))
                {
                    args[i] = MissingValue(parameter);
                    continue;
                }

                args[i] = ConvertElement(element, parameter.ParameterType, parameter.Name);
            }

            return args;
        }

        private static object?[] BindPositional(
            ParameterInfo[] parameters,
            JsonElement root,
            CancellationToken cancellationToken)
        {
            List<JsonElement> elements = root.EnumerateArray().ToList();
            object?[] args = new object?[parameters.Length];
            int sourceIndex = 0;

            for (int i = 0; i < parameters.Length; i++)
            {
                ParameterInfo parameter = parameters[i];
                if (parameter.ParameterType == typeof(CancellationToken))
                {
                    args[i] = cancellationToken;
                    continue;
                }

                if (IsParamArray(parameter))
                {
                    Array array = Array.CreateInstance(
                        parameter.ParameterType.GetElementType() ?? typeof(object),
                        Math.Max(0, elements.Count - sourceIndex));
                    for (int j = 0; j < array.Length; j++)
                    {
                        array.SetValue(
                            ConvertElement(elements[sourceIndex + j], array.GetType().GetElementType()!, parameter.Name),
                            j);
                    }
                    args[i] = array;
                    sourceIndex = elements.Count;
                    continue;
                }

                if (sourceIndex < elements.Count)
                {
                    args[i] = ConvertElement(elements[sourceIndex], parameter.ParameterType, parameter.Name);
                    sourceIndex++;
                    continue;
                }

                args[i] = MissingValue(parameter);
            }

            if (sourceIndex < elements.Count)
            {
                throw new ToolBindingException(
                    "INVALID_ARGUMENT",
                    $"参数数量不匹配：方法只有 {parameters.Length} 个参数，但传入了 {elements.Count} 个。");
            }

            return args;
        }

        private static object? ConvertElement(JsonElement element, Type parameterType, string? parameterName)
        {
            if (parameterType == typeof(JsonElement))
            {
                return element.Clone();
            }

            if (element.ValueKind == JsonValueKind.Null)
            {
                if (parameterType.IsValueType && Nullable.GetUnderlyingType(parameterType) is null)
                {
                    throw new ToolBindingException(
                        "INVALID_ARGUMENT",
                        $"参数 {parameterName} 不能为 null（期望 {FriendlyType(parameterType)}）。");
                }

                return null;
            }

            try
            {
                object? value = element.Deserialize(parameterType, JsonOptions);
                if (value is null && parameterType.IsValueType && Nullable.GetUnderlyingType(parameterType) is null)
                {
                    throw new ToolBindingException(
                        "INVALID_ARGUMENT",
                        $"参数 {parameterName} 类型转换结果为空（期望 {FriendlyType(parameterType)}）。");
                }

                return value;
            }
            catch (JsonException ex)
            {
                throw new ToolBindingException(
                    "INVALID_ARGUMENT",
                    $"参数 {parameterName} 类型转换失败：{ex.Message}",
                    $"期望类型 {FriendlyType(parameterType)}。");
            }
        }

        private static object? MissingValue(ParameterInfo parameter)
        {
            if (parameter.HasDefaultValue)
            {
                return parameter.DefaultValue;
            }

            Type type = parameter.ParameterType;
            if (!type.IsValueType || Nullable.GetUnderlyingType(type) is not null)
            {
                return null;
            }

            throw new ToolBindingException(
                "INVALID_ARGUMENT",
                $"缺少必填参数 {parameter.Name}（{FriendlyType(type)}）。",
                $"请在本轮 arguments_json 中传入属性 \"{parameter.Name}\"。");
        }

        private static bool IsParamArray(ParameterInfo parameter)
        {
            return parameter.GetCustomAttribute<ParamArrayAttribute>() is not null
                && parameter.ParameterType.IsArray;
        }

        private static Array EmptyArray(Type arrayType)
        {
            Type elementType = arrayType.GetElementType() ?? typeof(object);
            return Array.CreateInstance(elementType, 0);
        }

        private static string FriendlyType(Type type)
        {
            Type? underlying = Nullable.GetUnderlyingType(type);
            if (underlying is not null)
            {
                return $"{FriendlyType(underlying)}?";
            }

            if (type.IsArray)
            {
                return $"{FriendlyType(type.GetElementType() ?? typeof(object))}[]";
            }

            if (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(List<>))
            {
                return $"List<{FriendlyType(type.GetGenericArguments()[0])}>";
            }

            return type.Name;
        }
    }

    /// <summary>
    /// TimeSpan 采用 "c" 常量格式（如 01:02:03）与 JSON 字符串互转。
    /// </summary>
    public sealed class TimeSpanJsonConverter : JsonConverter<TimeSpan>
    {
        public override TimeSpan Read(
            ref Utf8JsonReader reader,
            Type typeToConvert,
            JsonSerializerOptions options)
        {
            string? text = reader.GetString();
            if (string.IsNullOrWhiteSpace(text))
            {
                throw new JsonException("TimeSpan 不能为空。");
            }

            return TimeSpan.Parse(text, CultureInfo.InvariantCulture);
        }

        public override void Write(
            Utf8JsonWriter writer,
            TimeSpan value,
            JsonSerializerOptions options)
        {
            writer.WriteStringValue(value.ToString("c", CultureInfo.InvariantCulture));
        }
    }
}
