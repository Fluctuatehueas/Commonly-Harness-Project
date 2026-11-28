﻿using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Reflection;
using System.Text.Json.Nodes;
using System.Text.Json.Schema;
using System.Threading;
using System.Threading.Tasks;

namespace CommonHarnessPluginSDK.Utils
{
    /// <summary>
    /// 由方法签名生成 args_schema_json / result_schema_json，作为 ListTools 的唯一权威来源。
    /// 基础类型使用显式映射，复杂 DTO 交给 JsonSchemaExporter。
    /// </summary>
    public static class ToolSchemaBuilder
    {
        public static string BuildArgsSchema(MethodInfo method)
        {
            JsonObject properties = new JsonObject();
            JsonArray required = new JsonArray();

            foreach (ParameterInfo parameter in method.GetParameters())
            {
                if (parameter.ParameterType == typeof(CancellationToken))
                {
                    continue;
                }

                string name = parameter.Name ?? $"arg{properties.Count}";
                JsonNode schema = BuildTypeSchema(parameter.ParameterType);
                string? description = parameter.GetCustomAttribute<DescriptionAttribute>()?.Description;
                if (!string.IsNullOrWhiteSpace(description) && schema is JsonObject schemaObject)
                {
                    schemaObject["description"] = description;
                }

                properties[name] = schema;

                // 有默认值或 params 的参数视为可选；其余都必须由调用方提供。
                if (!parameter.HasDefaultValue
                    && parameter.GetCustomAttribute<ParamArrayAttribute>() is null)
                {
                    required.Add(name);
                }
            }

            JsonObject root = new JsonObject
            {
                ["type"] = "object",
                ["properties"] = properties,
                ["required"] = required,
            };
            return root.ToJsonString();
        }

        public static string BuildResultSchema(MethodInfo method)
        {
            Type returnType = UnwrapAsyncReturnType(method.ReturnType);
            if (returnType == typeof(void) || returnType == typeof(Models.ToolResult))
            {
                return "";
            }

            return BuildTypeSchema(returnType).ToJsonString();
        }

        public static JsonNode BuildTypeSchema(Type type)
        {
            Type? nullable = Nullable.GetUnderlyingType(type);
            if (nullable is not null)
            {
                type = nullable;
            }

            if (type == typeof(string)
                || type == typeof(char)
                || type == typeof(Guid)
                || type == typeof(DateTime)
                || type == typeof(DateTimeOffset)
                || type == typeof(DateOnly)
                || type == typeof(TimeOnly)
                || type == typeof(TimeSpan)
                || type == typeof(Uri))
            {
                return new JsonObject { ["type"] = "string" };
            }

            if (type == typeof(bool))
            {
                return new JsonObject { ["type"] = "boolean" };
            }

            if (type == typeof(int)
                || type == typeof(long)
                || type == typeof(short)
                || type == typeof(byte)
                || type == typeof(uint)
                || type == typeof(ulong)
                || type == typeof(ushort)
                || type == typeof(sbyte))
            {
                return new JsonObject { ["type"] = "integer" };
            }

            if (type == typeof(float)
                || type == typeof(double)
                || type == typeof(decimal))
            {
                return new JsonObject { ["type"] = "number" };
            }

            if (type == typeof(object) || type == typeof(JsonNode) || type == typeof(System.Text.Json.JsonElement))
            {
                return new JsonObject { ["type"] = "object" };
            }

            if (type.IsEnum)
            {
                JsonArray values = new JsonArray();
                foreach (string name in Enum.GetNames(type))
                {
                    values.Add(name);
                }

                return new JsonObject
                {
                    ["type"] = "string",
                    ["enum"] = values,
                };
            }

            if (type.IsArray)
            {
                return new JsonObject
                {
                    ["type"] = "array",
                    ["items"] = BuildTypeSchema(type.GetElementType() ?? typeof(object)),
                };
            }

            if (type.IsGenericType)
            {
                Type definition = type.GetGenericTypeDefinition();
                Type[] arguments = type.GetGenericArguments();

                if (definition == typeof(List<>) || definition == typeof(IList<>)
                    || definition == typeof(IReadOnlyList<>))
                {
                    return new JsonObject
                    {
                        ["type"] = "array",
                        ["items"] = BuildTypeSchema(arguments[0]),
                    };
                }

                if (definition == typeof(Dictionary<,>)
                    || definition == typeof(IDictionary<,>)
                    || definition == typeof(IReadOnlyDictionary<,>))
                {
                    return new JsonObject
                    {
                        ["type"] = "object",
                        ["additionalProperties"] = BuildTypeSchema(arguments[1]),
                    };
                }
            }

            try
            {
                return JsonSchemaExporter.GetJsonSchemaAsNode(
                    ToolArgumentBinder.JsonOptions,
                    type,
                    JsonSchemaExporterOptions.Default);
            }
            catch
            {
                return new JsonObject { ["type"] = "object" };
            }
        }

        private static Type UnwrapAsyncReturnType(Type type)
        {
            if (type == typeof(Task) || type == typeof(ValueTask))
            {
                return typeof(void);
            }

            if (type.IsGenericType)
            {
                Type definition = type.GetGenericTypeDefinition();
                if (definition == typeof(Task<>) || definition == typeof(ValueTask<>))
                {
                    return type.GetGenericArguments()[0];
                }
            }

            return type;
        }
    }
}
