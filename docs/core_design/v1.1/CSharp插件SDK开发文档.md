# C# 插件 SDK 开发文档

Time: 2026/11/28（二次修订）  
Target: .NET 10（`net10.0`）  
Scope: 逐模块 C# 代码与实现讲解，覆盖形态 A（插件 stdio）、形态 B（gRPC 契约关联）、形态 C（HTTP 远程插件）。  
配套文档：[CSharp插件SDK设计文档.md](CSharp插件SDK设计文档.md)

阅读顺序：§1 工程 → §2 元数据（两种形态共用）→ §3~§5 形态 A → §6~§8 形态 B →
§8 形态 C → §9 Python 对端附录 → §10 测试 → §11 发布与契约同步 → §12 坑位 → §13 实施顺序。

---

## 1. 工程骨架

### 1.1 `Directory.Build.props`

```xml
<Project>
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <LangVersion>latest</LangVersion>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <InvariantGlobalization>false</InvariantGlobalization>
    <GenerateDocumentationFile>true</GenerateDocumentationFile>
    <NoWarn>$(NoWarn);CS1591</NoWarn>
    <Version>1.0.0</Version>
    <Authors>Local</Authors>
  </PropertyGroup>
</Project>
```

### 1.2 核心包（元数据 + 形态 A/C）

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <AssemblyName>Harness.Plugin.Sdk</AssemblyName>
    <RootNamespace>Harness.Plugin.Sdk</RootNamespace>
    <PackageId>Harness.Plugin.Sdk</PackageId>
    <Description>Harness C# SDK：工具元数据 + stdio 插件宿主</Description>
  </PropertyGroup>
</Project>
```

核心包零第三方依赖，只用 BCL。`[HarnessTool]`、Schema、参数绑定、`ToolResult`
必须放在这里，两种形态共用一份，禁止在 gRPC 包内复制。

### 1.3 gRPC 包（形态 B）

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <AssemblyName>Harness.Plugin.Sdk.Grpc</AssemblyName>
    <RootNamespace>Harness.Plugin.Sdk.Grpc</RootNamespace>
    <PackageId>Harness.Plugin.Sdk.Grpc</PackageId>
    <Description>Harness C# SDK：gRPC 契约关联（ToolService + 客户端）</Description>
  </PropertyGroup>

  <ItemGroup>
    <FrameworkReference Include="Microsoft.AspNetCore.App" />
    <ProjectReference Include="..\Harness.Plugin.Sdk\Harness.Plugin.Sdk.csproj" />
    <PackageReference Include="Grpc.AspNetCore" Version="2.62.0" />
    <PackageReference Include="Grpc.Net.Client" Version="2.62.0" />
    <PackageReference Include="Google.Protobuf" Version="3.27.0" />
    <PackageReference Include="Grpc.Tools" Version="2.62.0" PrivateAssets="All" />
  </ItemGroup>

  <!-- proto 唯一源在仓库根 proto/，由包携带，不复制进业务工程 -->
  <ItemGroup>
    <Protobuf Include="..\..\proto\*.proto" GrpcServices="Both" ProtoRoot="..\..\proto" />
  </ItemGroup>

  <ItemGroup>
    <None Include="..\..\proto\*.proto" Pack="true" PackagePath="proto\" />
    <None Include="..\..\proto\contract.version" Pack="true" PackagePath="proto\" />
    <None Include="buildTransitive\Harness.Plugin.Sdk.Grpc.props" Pack="true"
          PackagePath="buildTransitive\" />
  </ItemGroup>
</Project>
```

`buildTransitive/Harness.Plugin.Sdk.Grpc.props`（消费者零配置）：

```xml
<Project>
  <ItemGroup>
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Agent.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Session.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Trajectory.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\Harness.proto" GrpcServices="Both" />
    <Protobuf Include="$(MSBuildThisFileDirectory)..\proto\File.proto" GrpcServices="Both" />
  </ItemGroup>
</Project>
```

消费者工程只需要：

```xml
<PackageReference Include="Harness.Plugin.Sdk.Grpc" Version="1.0.0" />
<PackageReference Include="Grpc.Tools" Version="2.62.0" PrivateAssets="All" />
```

> `Grpc.Tools` 是构建期工具，必须 `PrivateAssets="All"`；`Harness.Plugin.Sdk.Grpc`
> 已依赖 `Grpc.AspNetCore` / `Grpc.Net.Client` / `Google.Protobuf`，消费者不用重复声明。

### 1.4 契约版本单一来源

`proto/contract.version`：

```text
1.3.0
```

C# 常量（`Harness.Plugin.Sdk.Grpc/ContractVersion.cs`）：

```csharp
namespace Harness.Plugin.Sdk.Grpc;

public static class ContractVersion
{
    // 与 proto/contract.version 保持一致，CI 有单测比对
    public const string Value = "1.3.0";

    public static bool IsCompatible(string remote)
    {
        if (string.IsNullOrWhiteSpace(remote)) return true; // 首次联调放行
        return Major(remote) == Major(Value);
    }

    private static string Major(string version) => version.Split('.', 2)[0];
}
```

Python 侧从同一个文件读取，禁止各自写死。

---

## 2. 元数据层（两种形态共用）

### 2.1 `HarnessToolAttribute`（统一特性）

属性取并集：插件模式用 `Name/Description/Timeout`，gRPC 模式额外用
`DisplayName/Domain/Skill/SandboxLevel/IsWrite/IsDestructive/SupportsEditArgs/Version/Highlight`。

```csharp
using System;

namespace Harness.Plugin.Sdk.Metadata;

[AttributeUsage(AttributeTargets.Method, AllowMultiple = false, Inherited = false)]
public sealed class HarnessToolAttribute : Attribute
{
    public HarnessToolAttribute(string? name = null) => Name = name;

    // 未填时由方法名转 snake_case
    public string? Name { get; }

    public string? Description { get; set; }
    public string? DisplayName { get; set; }
    public string? Domain { get; set; }

    // gRPC 分组；插件模式忽略
    public string? Skill { get; set; }

    // gRPC 审批等级；插件模式忽略
    public SandboxLevel SandboxLevel { get; set; } = SandboxLevel.Auto;

    public bool IsWrite { get; set; }
    public bool IsDestructive { get; set; }
    public bool SupportsEditArgs { get; set; }

    // 两种模式都生效
    public int TimeoutMs { get; set; } = 30_000;

    public string Version { get; set; } = "1.0";

    // 仅形态 B：执行前高亮目标
    public HighlightTarget Highlight { get; set; } = HighlightTarget.None;
}

public enum SandboxLevel
{
    Auto = 0,          // 可自动执行（只读或可撤销）
    Confirm = 1,       // 越界，需确认
    AlwaysConfirm = 2  // 破坏性，始终确认
}

public enum HighlightTarget
{
    None = 0,
    WpfControl = 1,
    Scene3D = 2
}
```

参数特性只在「逐参数绑定」时使用；单 DTO 模式改用 `[Description]`。

```csharp
[AttributeUsage(AttributeTargets.Parameter, AllowMultiple = false)]
public sealed class HarnessParamAttribute(string description) : Attribute
{
    public string Description { get; } = description;
    public bool Required { get; set; } = true;
    public string[]? Enum { get; set; }
    public string? Format { get; set; }
}
```

### 2.2 `ToolResult` / `ToolError` / `TrajectoryEvent`

一份结果模型同时服务：
- 形态 A：`ResultJson` 经 `ToContent()` 变成 `{content}`；
- 形态 B：`ResultJson` + `Error` + `Trajectory` 直接映射 `ToolCallResult`。

```csharp
using System.Text.Json;

namespace Harness.Plugin.Sdk.Metadata;

public sealed record ToolError(string Code, string Message, string Hint = "", bool Retryable = false);

public sealed record TrajectoryEvent
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

public sealed record ToolResult
{
    public bool Success { get; init; }
    public string ResultJson { get; init; } = "{}";
    public ToolError? Error { get; init; }
    public IReadOnlyList<TrajectoryEvent> Trajectory { get; init; } = [];

    public static ToolResult Ok(string resultJson = "{}")
        => new() { Success = true, ResultJson = resultJson };

    // 纯文本工具：序列化成 JSON 字符串，形态 B 也拿到合法 JSON
    public static ToolResult Text(string text)
        => Ok(JsonSerializer.Serialize(text ?? ""));

    public static ToolResult Fail(string code, string message, string hint = "", bool retryable = false)
        => new() { Success = false, Error = new ToolError(code, message, hint, retryable) };

    // 形态 A：还原成模型可读文本
    public string ToContent()
    {
        if (string.IsNullOrEmpty(ResultJson)) return "";
        try
        {
            using var document = JsonDocument.Parse(ResultJson);
            if (document.RootElement.ValueKind == JsonValueKind.String)
                return document.RootElement.GetString() ?? "";
            return ResultJson;
        }
        catch (JsonException)
        {
            return ResultJson;
        }
    }

    public ToolResult WithTrajectory(IEnumerable<TrajectoryEvent> events)
        => this with { Trajectory = [.. Trajectory, .. events] };
}
```

### 2.3 `JsonSchemaBuilder`

宿主插件侧只认 `string/integer/number/boolean/array/object` 与 `enum`；
gRPC 侧 `args_schema_json` 也必须落在同一子集。

双模式：
- **单 DTO**（gRPC 惯例）：方法只有一个非注入参数且是类/结构体 → 整个 JSON 反序列化成 DTO；
  Schema 由 DTO 属性生成。
- **逐参数**（插件惯例）：每个参数一条属性，描述取 `[HarnessParam]` 或 `[Description]`。

```csharp
using System.ComponentModel;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Harness.Plugin.Sdk.Metadata;

public static class JsonSchemaBuilder
{
    public static JsonObject BuildArgsSchema(MethodInfo method)
    {
        var parameters = method.GetParameters();
        var payload = TryGetSinglePayload(parameters);
        if (payload is not null)
        {
            return ForType(payload.ParameterType, depth: 0);
        }

        var properties = new JsonObject();
        var required = new JsonArray();
        foreach (var parameter in parameters)
        {
            if (IsInjected(parameter.ParameterType)) continue;

            var name = parameter.Name ?? "arg";
            var schema = ForType(parameter.ParameterType, depth: 0);
            var description = parameter.GetCustomAttribute<HarnessParamAttribute>()?.Description
                              ?? parameter.GetCustomAttribute<DescriptionAttribute>()?.Description;
            if (!string.IsNullOrEmpty(description)) schema["description"] = description;

            var metadata = parameter.GetCustomAttribute<HarnessParamAttribute>();
            if (metadata?.Enum is { Length: > 0 } values)
            {
                var array = new JsonArray();
                foreach (var value in values) array.Add(value);
                schema["enum"] = array;
            }

            properties[name] = schema;
            var optional = parameter.HasDefaultValue
                           || Nullable.GetUnderlyingType(parameter.ParameterType) is not null;
            if ((metadata?.Required ?? true) && !optional) required.Add(name);
        }

        var root = new JsonObject { ["type"] = "object", ["properties"] = properties };
        if (required.Count > 0) root["required"] = required;
        return root;
    }

    public static string BuildResultSchema(MethodInfo method)
    {
        var returnType = UnwrapTask(method.ReturnType);
        if (returnType is null || returnType == typeof(void)) return "";
        if (returnType == typeof(ToolResult)) return "{\"type\":\"object\"}";
        return ForType(returnType, depth: 0).ToJsonString();
    }

    public static bool IsInjected(Type type)
        => type == typeof(CancellationToken) || type == typeof(ToolContext);

    public static bool IsPayload(Type type)
        => !type.IsPrimitive && type != typeof(string) && type != typeof(decimal)
           && !type.IsEnum && !typeof(JsonElement).IsAssignableFrom(type)
           && !typeof(JsonNode).IsAssignableFrom(type)
           && !typeof(System.Collections.IEnumerable).IsAssignableFrom(type)
           && type != typeof(CancellationToken) && type != typeof(ToolContext);

    public static Type? UnwrapTask(Type type)
    {
        if (type == typeof(Task) || type == typeof(ValueTask)) return typeof(void);
        if (type.IsGenericType)
        {
            var definition = type.GetGenericTypeDefinition();
            if (definition == typeof(Task<>) || definition == typeof(ValueTask<>))
                return type.GetGenericArguments()[0];
        }
        return type;
    }

    public static ParameterInfo? TryGetSinglePayload(ParameterInfo[] parameters)
    {
        var candidates = parameters.Where(item => !IsInjected(item.ParameterType)).ToArray();
        return candidates.Length == 1 && IsPayload(candidates[0].ParameterType) ? candidates[0] : null;
    }

    private static JsonObject ForType(Type type, int depth)
    {
        var underlying = Nullable.GetUnderlyingType(type);
        if (underlying is not null) type = underlying;

        if (type == typeof(string) || type == typeof(char) || type == typeof(Guid)
            || type == typeof(DateTime) || type == typeof(DateTimeOffset) || type == typeof(TimeSpan))
            return new JsonObject { ["type"] = "string" };

        if (type == typeof(bool)) return new JsonObject { ["type"] = "boolean" };

        if (type == typeof(byte) || type == typeof(sbyte) || type == typeof(short)
            || type == typeof(ushort) || type == typeof(int) || type == typeof(uint)
            || type == typeof(long) || type == typeof(ulong))
            return new JsonObject { ["type"] = "integer" };

        if (type == typeof(float) || type == typeof(double) || type == typeof(decimal))
            return new JsonObject { ["type"] = "number" };

        if (type.IsEnum)
        {
            var values = new JsonArray();
            foreach (var name in Enum.GetNames(type)) values.Add(name);
            return new JsonObject { ["type"] = "string", ["enum"] = values };
        }

        if (type == typeof(JsonElement) || typeof(JsonNode).IsAssignableFrom(type))
            return new JsonObject { ["type"] = "object" };

        if (type.IsArray && type.GetElementType() is { } elementType)
            return new JsonObject { ["type"] = "array", ["items"] = ForType(elementType, depth + 1) };

        if (typeof(System.Collections.IDictionary).IsAssignableFrom(type))
            return new JsonObject { ["type"] = "object" };

        if (typeof(System.Collections.IEnumerable).IsAssignableFrom(type) && type != typeof(string))
        {
            var itemType = type.IsGenericType
                ? type.GetGenericArguments()[0]
                : type.GetInterfaces()
                    .FirstOrDefault(item => item.IsGenericType
                                            && item.GetGenericTypeDefinition() == typeof(IEnumerable<>))
                    ?.GetGenericArguments()[0] ?? typeof(JsonElement);
            return new JsonObject { ["type"] = "array", ["items"] = ForType(itemType, depth + 1) };
        }

        if (depth >= 2) return new JsonObject { ["type"] = "object" };

        var properties = new JsonObject();
        var required = new JsonArray();
        foreach (var property in type.GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            if (!property.CanRead || property.GetIndexParameters().Length > 0) continue;

            var schema = ForType(property.PropertyType, depth + 1);
            var description = property.GetCustomAttribute<DescriptionAttribute>()?.Description;
            if (!string.IsNullOrEmpty(description)) schema["description"] = description;

            var name = ToCamel(property.Name);
            properties[name] = schema;
            if (Nullable.GetUnderlyingType(property.PropertyType) is null
                && !property.PropertyType.IsClass
                && property.PropertyType != typeof(string)
                && property.GetCustomAttribute<System.Text.Json.Serialization.JsonIgnoreAttribute>() is null)
            {
                required.Add(name);
            }
        }

        var result = new JsonObject { ["type"] = "object" };
        if (properties.Count > 0)
        {
            result["properties"] = properties;
            if (required.Count > 0) result["required"] = required;
        }
        return result;
    }

    private static string ToCamel(string name)
        => string.IsNullOrEmpty(name) ? name : char.ToLowerInvariant(name[0]) + name[1..];
}
```

### 2.4 `ToolArgumentBinder`

```csharp
using System.Reflection;
using System.Text.Json;
using Harness.Plugin.Sdk.Protocol;

namespace Harness.Plugin.Sdk.Metadata;

public sealed class ToolBindingException(string code, string message, string hint = "")
    : Exception(message)
{
    public string Code { get; } = code;
    public string Hint { get; } = hint;
}

public static class ToolArgumentBinder
{
    public static object?[] Bind(MethodInfo method, string argumentsJson, CancellationToken ct)
    {
        var parameters = method.GetParameters();
        using var document = string.IsNullOrWhiteSpace(argumentsJson)
            ? JsonDocument.Parse("{}")
            : JsonDocument.Parse(argumentsJson);

        var root = document.RootElement;
        var payload = JsonSchemaBuilder.TryGetSinglePayload(parameters);
        var values = new object?[parameters.Length];

        if (payload is not null)
        {
            for (var index = 0; index < parameters.Length; index++)
            {
                var parameter = parameters[index];
                values[index] = parameter.ParameterType == typeof(CancellationToken)
                    ? ct
                    : parameter == payload
                        ? Deserialize(root, parameter.ParameterType)
                        : parameter.ParameterType == typeof(ToolContext) ? null : Default(parameter);
            }
            return values;
        }

        for (var index = 0; index < parameters.Length; index++)
        {
            var parameter = parameters[index];
            var type = parameter.ParameterType;

            if (type == typeof(CancellationToken)) { values[index] = ct; continue; }
            if (type == typeof(ToolContext)) continue; // 由调用方注入

            if (root.ValueKind == JsonValueKind.Array)
            {
                values[index] = index < root.GetArrayLength()
                    ? Deserialize(root[index], type)
                    : DefaultOrThrow(parameter);
                continue;
            }

            var name = parameter.Name ?? $"arg{index}";
            if (root.TryGetProperty(name, out var element)
                || TryGetPropertyIgnoreCase(root, name, out element))
            {
                values[index] = Deserialize(element, type);
                continue;
            }

            values[index] = DefaultOrThrow(parameter);
        }

        return values;
    }

    private static bool TryGetPropertyIgnoreCase(JsonElement root, string name, out JsonElement value)
    {
        foreach (var property in root.EnumerateObject())
        {
            if (string.Equals(property.Name, name, StringComparison.OrdinalIgnoreCase))
            {
                value = property.Value;
                return true;
            }
        }
        value = default;
        return false;
    }

    private static object? Deserialize(JsonElement element, Type type)
    {
        try
        {
            return JsonSerializer.Deserialize(element.GetRawText(), type, JsonDefaults.Options);
        }
        catch (JsonException error)
        {
            throw new ToolBindingException("INVALID_ARGUMENT",
                $"参数反序列化失败（{type.Name}）：{error.Message}",
                "检查 arguments_json 的字段名与类型。");
        }
    }

    private static object? Default(ParameterInfo parameter)
        => parameter.HasDefaultValue
            ? parameter.DefaultValue
            : Nullable.GetUnderlyingType(parameter.ParameterType) is not null
                ? null
                : parameter.ParameterType.IsValueType
                    ? Activator.CreateInstance(parameter.ParameterType)
                    : null;

    private static object? DefaultOrThrow(ParameterInfo parameter)
    {
        if (parameter.HasDefaultValue) return parameter.DefaultValue;
        if (Nullable.GetUnderlyingType(parameter.ParameterType) is not null) return null;
        throw new ToolBindingException("INVALID_ARGUMENT",
            $"缺少参数: {parameter.Name}", "按 args_schema_json 补齐必填参数。");
    }
}
```

> `ToolContext` 是形态 A 的注入上下文（见 §4）；形态 B 的工具一般不需要它。
> 若同一个工具类要同时服务两种形态，构造函数里不要依赖 WPF；把 UI 操作封到实例方法内部。

### 2.5 `HarnessToolRegistry`

```csharp
using System.Collections.Concurrent;
using System.ComponentModel;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Text.Json.Nodes;

namespace Harness.Plugin.Sdk.Metadata;

public sealed class ToolRegistration
{
    public required string Name { get; init; }
    public required HarnessToolAttribute Attribute { get; init; }
    public required MethodInfo Method { get; init; }
    public required ToolMetadata Metadata { get; init; }
    public required Func<object?[], CancellationToken, Task<ToolResult>> Invoker { get; init; }
}

public sealed record ToolMetadata
{
    public required string Name { get; init; }
    public string DisplayName { get; init; } = "";
    public string Description { get; init; } = "";
    public string Domain { get; init; } = "";
    public string Skill { get; init; } = "default";
    public string ArgsSchemaJson { get; init; } = "{}";
    public string ResultSchemaJson { get; init; } = "";
    public SandboxLevel SandboxLevel { get; init; } = SandboxLevel.Auto;
    public HighlightTarget Highlight { get; init; } = HighlightTarget.None;
    public bool IsWrite { get; init; }
    public bool IsDestructive { get; init; }
    public bool SupportsEditArgs { get; init; }
    public int TimeoutMs { get; init; } = 30_000;
    public string Version { get; init; } = "1.0";
}

public sealed record SkillDefinition(
    string Name,
    string DisplayName,
    string Description,
    string PromptFragment,
    IReadOnlyList<ToolMetadata> Tools,
    string MetadataJson = "{}");

public sealed class HarnessToolRegistry
{
    private static readonly Regex NamePattern = new("^[a-z][a-z0-9_]*$", RegexOptions.Compiled);

    private readonly ConcurrentDictionary<string, ToolRegistration> _tools =
        new(StringComparer.Ordinal);

    public int Count => _tools.Count;

    public IReadOnlyDictionary<string, ToolRegistration> Registrations => _tools;

    public int Register(object instance) => RegisterCore(instance.GetType(), () => instance);

    public int Register(Type type, Func<object?>? factory = null)
        => RegisterCore(type, factory ?? (() => Activator.CreateInstance(type)));

    public int RegisterFromAssembly(Assembly assembly, Func<Type, object?>? factory = null,
        Action<string>? log = null)
    {
        var count = 0;
        foreach (var type in assembly.GetTypes())
        {
            try
            {
                count += factory is null ? Register(type) : RegisterCore(type, () => factory(type));
            }
            catch (Exception error)
            {
                log?.Invoke($"[HarnessToolRegistry] 跳过 {type.FullName}: {error.Message}");
            }
        }
        return count;
    }

    public bool TryGet(string name, out ToolRegistration registration)
        => _tools.TryGetValue(name, out registration!);

    public IReadOnlyList<SkillDefinition> GetSkills()
        => _tools.Values
            .GroupBy(item => item.Metadata.Skill, StringComparer.Ordinal)
            .Select(group => new SkillDefinition(
                group.Key,
                group.Key,
                $"C# 端工具分组：{group.Key}",
                "",
                group.Select(item => item.Metadata)
                    .OrderBy(item => item.Name, StringComparer.Ordinal)
                    .ToArray()))
            .OrderBy(item => item.Name, StringComparer.Ordinal)
            .ToArray();

    // 形态 A 的 describe.capabilities.tool
    public JsonArray DescribeTools()
    {
        var array = new JsonArray();
        foreach (var entry in _tools.Values.OrderBy(item => item.Name, StringComparer.Ordinal))
        {
            array.Add(new JsonObject
            {
                ["name"] = entry.Name,
                ["description"] = entry.Metadata.Description,
                ["inputSchema"] = JsonNode.Parse(entry.Metadata.ArgsSchemaJson) ?? new JsonObject(),
            });
        }
        return array;
    }

    private int RegisterCore(Type type, Func<object?> instanceFactory)
    {
        if (type.IsInterface || type.IsGenericTypeDefinition) return 0;
        if (type.IsAbstract && !type.IsSealed) return 0; // 静态类允许

        var registered = 0;
        foreach (var method in type.GetMethods(
                     BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
        {
            var attribute = method.GetCustomAttribute<HarnessToolAttribute>();
            if (attribute is null) continue;

            var name = string.IsNullOrWhiteSpace(attribute.Name)
                ? ToSnakeCase(method.Name)
                : attribute.Name!.Trim();

            if (!NamePattern.IsMatch(name))
                throw new InvalidOperationException(
                    $"工具名 {name} 不合法：必须 snake_case（小写字母开头，只含小写字母、数字、下划线）。");
            if (method.IsGenericMethodDefinition)
                throw new InvalidOperationException($"工具 {name} 不能是泛型方法。");

            var description = attribute.Description
                              ?? method.GetCustomAttribute<DescriptionAttribute>()?.Description
                              ?? name;

            var metadata = new ToolMetadata
            {
                Name = name,
                DisplayName = attribute.DisplayName ?? name,
                Description = description,
                Domain = attribute.Domain ?? "",
                Skill = string.IsNullOrWhiteSpace(attribute.Skill) ? "default" : attribute.Skill!,
                ArgsSchemaJson = JsonSchemaBuilder.BuildArgsSchema(method).ToJsonString(),
                ResultSchemaJson = JsonSchemaBuilder.BuildResultSchema(method),
                SandboxLevel = attribute.SandboxLevel,
                Highlight = attribute.Highlight,
                IsWrite = attribute.IsWrite,
                IsDestructive = attribute.IsDestructive,
                SupportsEditArgs = attribute.SupportsEditArgs,
                TimeoutMs = attribute.TimeoutMs > 0 ? attribute.TimeoutMs : 30_000,
                Version = attribute.Version,
            };

            var registration = new ToolRegistration
            {
                Name = name,
                Attribute = attribute,
                Method = method,
                Metadata = metadata,
                Invoker = async (args, ct) =>
                {
                    var target = method.IsStatic ? null : instanceFactory();
                    object? value;
                    try
                    {
                        value = method.Invoke(target, args);
                    }
                    catch (TargetInvocationException error) when (error.InnerException is not null)
                    {
                        throw error.InnerException;
                    }
                    return await NormalizeAsync(value, ct).ConfigureAwait(false);
                },
            };

            if (!_tools.TryAdd(name, registration))
                throw new InvalidOperationException(
                    $"工具名重复：{name}。同一进程内工具名必须唯一。");

            registered++;
        }
        return registered;
    }

    internal static async Task<ToolResult> NormalizeAsync(object? value, CancellationToken ct)
    {
        switch (value)
        {
            case null: return ToolResult.Ok();
            case ToolResult result: return result;
            case string text: return ToolResult.Text(text);
            case Task<ToolResult> task: return await task.WaitAsync(ct).ConfigureAwait(false);
            case Task<string> task: return ToolResult.Text(await task.WaitAsync(ct).ConfigureAwait(false));
            case Task task: await task.WaitAsync(ct).ConfigureAwait(false); return ToolResult.Ok();
            case ValueTask<ToolResult> task: return await task.AsTask().WaitAsync(ct).ConfigureAwait(false);
            case ValueTask<string> task:
                return ToolResult.Text(await task.AsTask().WaitAsync(ct).ConfigureAwait(false));
            case ValueTask task: await task.AsTask().WaitAsync(ct).ConfigureAwait(false); return ToolResult.Ok();
            default:
                return ToolResult.Ok(
                    System.Text.Json.JsonSerializer.Serialize(value, Protocol.JsonDefaults.Options));
        }
    }

    private static string ToSnakeCase(string name)
    {
        var builder = new System.Text.StringBuilder(name.Length + 8);
        for (var index = 0; index < name.Length; index++)
        {
            var current = name[index];
            if (char.IsUpper(current))
            {
                if (index > 0) builder.Append('_');
                builder.Append(char.ToLowerInvariant(current));
            }
            else
            {
                builder.Append(current);
            }
        }
        return builder.ToString();
    }
}
```

> 命名策略变化：旧 `ToolRegistry` 直接用方法名并要求调用方写 snake_case；SDK 改为
> 「显式 Name 必须 snake_case，未填则 PascalCase 自动转 snake_case」，减少迁移摩擦。

---

## 3. 形态 A：协议层

### 3.1 `JsonDefaults` / `RpcErrorCode` / `PluginRpcException`

```csharp
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Harness.Plugin.Sdk.Protocol;

public static class JsonDefaults
{
    public static JsonSerializerOptions Options { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        WriteIndented = false,
    };
}

public enum RpcErrorCode
{
    ParseError = -32700,
    InvalidRequest = -32600,
    MethodNotFound = -32601,
    InvalidParams = -32602,
    InternalError = -32603,
    NotFound = -32001,
    NotReady = -32002,
    PermissionDenied = -32003,
    Timeout = -32004,
    CapabilityMismatch = -32005,
    ProtocolVersionMismatch = -32006,
    Crashed = -32007,
    Unhealthy = -32008,
    PackageInvalid = -32009,
    ToolFailed = -32010,
}

public sealed class PluginRpcException(RpcErrorCode code, string message,
    System.Text.Json.Nodes.JsonNode? detail = null, Exception? inner = null)
    : Exception(message, inner)
{
    public RpcErrorCode Code { get; } = code;
    public System.Text.Json.Nodes.JsonNode? Detail { get; } = detail;
}
```

### 3.2 `RpcPeer`

读循环与请求处理必须解耦：`tool.call` 内部可能反向 `host.storage.get`，
若在读循环里同步等待业务，响应永远读不到。

```csharp
using System.Collections.Concurrent;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Harness.Plugin.Sdk.Protocol;

public sealed record RpcError(int Code, string Message, JsonNode? Data = null);

public sealed record RpcOutcome(JsonNode? Result, RpcError? Error)
{
    public static RpcOutcome Ok(JsonNode? result = null) => new(result, null);
    public static RpcOutcome Fail(RpcErrorCode code, string message, JsonNode? data = null)
        => new(null, new RpcError((int)code, message, data));
}

public delegate Task<RpcOutcome> RpcRequestHandler(
    string method, JsonElement parameters, CancellationToken ct);

public delegate Task RpcNotificationHandler(
    string method, JsonElement parameters, CancellationToken ct);

public sealed class RpcPeer : IAsyncDisposable
{
    private readonly TextReader _reader;
    private readonly TextWriter _writer;
    private readonly RpcRequestHandler _onRequest;
    private readonly RpcNotificationHandler? _onNotification;
    private readonly bool _ownsStreams;
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly CancellationTokenSource _lifetime = new();
    private long _sequence;

    public RpcPeer(Stream input, Stream output, RpcRequestHandler onRequest,
        RpcNotificationHandler? onNotification = null)
        : this(
            new StreamReader(input, new UTF8Encoding(false), false, 64 * 1024),
            new StreamWriter(output, new UTF8Encoding(false)) { AutoFlush = true },
            onRequest, onNotification, ownsStreams: true)
    {
    }

    public RpcPeer(TextReader reader, TextWriter writer, RpcRequestHandler onRequest,
        RpcNotificationHandler? onNotification = null)
        : this(reader, writer, onRequest, onNotification, ownsStreams: false)
    {
    }

    private RpcPeer(TextReader reader, TextWriter writer, RpcRequestHandler onRequest,
        RpcNotificationHandler? onNotification, bool ownsStreams)
    {
        _reader = reader;
        _writer = writer;
        _onRequest = onRequest;
        _onNotification = onNotification;
        _ownsStreams = ownsStreams;
    }

    public async Task RunAsync(CancellationToken ct)
    {
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct, _lifetime.Token);
        var token = linked.Token;
        try
        {
            while (!token.IsCancellationRequested)
            {
                string? line;
                try
                {
                    line = await _reader.ReadLineAsync(token).ConfigureAwait(false);
                }
                catch (Exception error) when (error is OperationCanceledException or IOException
                                              or ObjectDisposedException)
                {
                    break;
                }

                if (line is null) break;   // 宿主关闭 stdin
                if (line.Length == 0) continue;

                JsonElement message;
                try
                {
                    using var document = JsonDocument.Parse(line);
                    message = document.RootElement.Clone();
                }
                catch (JsonException)
                {
                    continue; // 脏行忽略，与宿主一致
                }

                Dispatch(message, token);
            }
        }
        finally
        {
            FailPending(new PluginRpcException(RpcErrorCode.Crashed, "插件连接已关闭"));
        }
    }

    private void Dispatch(JsonElement message, CancellationToken ct)
    {
        if (message.TryGetProperty("method", out var methodElement)
            && methodElement.ValueKind == JsonValueKind.String)
        {
            var method = methodElement.GetString()!;
            var parameters = message.TryGetProperty("params", out var raw) ? raw : default;

            if (!message.TryGetProperty("id", out var idElement))
            {
                if (_onNotification is not null) _ = SafeNotifyAsync(method, parameters, ct);
                return;
            }

            _ = SafeHandleAsync(method, parameters, idElement.Clone(), ct);
            return;
        }

        if (message.TryGetProperty("id", out var responseId)
            && responseId.ValueKind == JsonValueKind.String
            && _pending.TryRemove(responseId.GetString()!, out var completion))
        {
            if (message.TryGetProperty("error", out var errorElement))
            {
                var code = errorElement.TryGetProperty("code", out var codeElement)
                           && codeElement.TryGetInt32(out var codeValue)
                    ? codeValue : (int)RpcErrorCode.InternalError;
                var text = errorElement.TryGetProperty("message", out var messageElement)
                    ? messageElement.GetString() ?? "宿主调用失败" : "宿主调用失败";
                JsonNode? data = errorElement.TryGetProperty("data", out var dataElement)
                    ? JsonNode.Parse(dataElement.GetRawText()) : null;
                completion.TrySetException(new PluginRpcException((RpcErrorCode)code, text, data));
            }
            else
            {
                var result = message.TryGetProperty("result", out var resultElement)
                    ? resultElement.Clone() : default;
                completion.TrySetResult(result);
            }
        }
    }

    private async Task SafeHandleAsync(string method, JsonElement parameters, JsonElement id,
        CancellationToken ct)
    {
        RpcOutcome outcome;
        try
        {
            outcome = await _onRequest(method, parameters, ct).ConfigureAwait(false);
        }
        catch (PluginRpcException error)
        {
            outcome = new RpcOutcome(null, new RpcError((int)error.Code, error.Message, error.Detail));
        }
        catch (Exception error)
        {
            outcome = RpcOutcome.Fail(RpcErrorCode.InternalError, error.Message);
        }

        try
        {
            await WriteResponseAsync(id, outcome, ct).ConfigureAwait(false);
        }
        catch (Exception)
        {
            // 宿主已断开，正常退出路径
        }
    }

    private async Task SafeNotifyAsync(string method, JsonElement parameters, CancellationToken ct)
    {
        try
        {
            await _onNotification!(method, parameters, ct).ConfigureAwait(false);
        }
        catch (Exception)
        {
            // 通知失败不回复
        }
    }

    private async Task WriteResponseAsync(JsonElement id, RpcOutcome outcome, CancellationToken ct)
    {
        var envelope = new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = JsonNode.Parse(id.GetRawText()),
        };

        if (outcome.Error is { } error)
        {
            var payload = new JsonObject { ["code"] = error.Code, ["message"] = error.Message };
            if (error.Data is not null) payload["data"] = error.Data;
            envelope["error"] = payload;
        }
        else
        {
            envelope["result"] = outcome.Result ?? new JsonObject();
        }

        await WriteLineAsync(envelope.ToJsonString(JsonDefaults.Options), ct).ConfigureAwait(false);
    }

    public async Task<JsonElement> RequestAsync(string method, JsonNode? parameters,
        CancellationToken ct = default)
    {
        var id = Interlocked.Increment(ref _sequence).ToString(CultureInfo.InvariantCulture);
        var completion = new TaskCompletionSource<JsonElement>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = completion;

        var envelope = new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["method"] = method,
            ["params"] = parameters ?? new JsonObject(),
        };

        try
        {
            await WriteLineAsync(envelope.ToJsonString(JsonDefaults.Options), ct).ConfigureAwait(false);
            return await completion.Task.WaitAsync(ct).ConfigureAwait(false);
        }
        finally
        {
            _pending.TryRemove(id, out _);
        }
    }

    public Task NotifyAsync(string method, JsonNode? parameters, CancellationToken ct = default)
        => WriteLineAsync(new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["method"] = method,
            ["params"] = parameters ?? new JsonObject(),
        }.ToJsonString(JsonDefaults.Options), ct);

    private async Task WriteLineAsync(string text, CancellationToken ct)
    {
        await _writeLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            await _writer.WriteLineAsync(text.AsMemory(), ct).ConfigureAwait(false);
            await _writer.FlushAsync(ct).ConfigureAwait(false);
        }
        finally
        {
            _writeLock.Release();
        }
    }

    private void FailPending(Exception error)
    {
        foreach (var key in _pending.Keys)
        {
            if (_pending.TryRemove(key, out var completion)) completion.TrySetException(error);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _lifetime.CancelAsync().ConfigureAwait(false);
        if (_ownsStreams)
        {
            await _reader.DisposeAsync().ConfigureAwait(false);
            await _writer.DisposeAsync().ConfigureAwait(false);
        }
        _lifetime.Dispose();
        _writeLock.Dispose();
    }
}
```

---

## 4. 形态 A：运行时

### 4.1 `ToolContext` / `IHostClient` / 日志（位于 Metadata 层）

`ToolContext` 放在 `Harness.Plugin.Sdk.Metadata`，`Host` 用界面 `IHostClient`，
避免元数据层反向依赖 `Hosting`。

```csharp
using System.Text.Json;
using System.Text.Json.Nodes;
using Harness.Plugin.Sdk.Contracts;

namespace Harness.Plugin.Sdk.Metadata;

public interface IPluginLogger
{
    void Debug(string message);
    void Info(string message);
    void Warn(string message);
    void Error(string message, Exception? error = null);
}

public interface IHostClient
{
    bool IsAvailable { get; }
    Task<JsonElement?> StorageGetAsync(string key, CancellationToken ct = default);
    Task StorageSetAsync(string key, object? value, CancellationToken ct = default);
    Task<IReadOnlyList<string>> StorageListAsync(CancellationToken ct = default);
    Task StorageDeleteAsync(string key, CancellationToken ct = default);
    Task<string> SecretGetAsync(string name, CancellationToken ct = default);
    Task LogAsync(string level, string message, CancellationToken ct = default);
    Task ProgressAsync(JsonObject payload, CancellationToken ct = default);
}

public sealed class ToolContext
{
    public ToolContext(PluginInitRequest init, IHostClient host, IPluginLogger logger)
    {
        Init = init;
        Host = host;
        Log = logger;
    }

    public PluginInitRequest Init { get; }
    public string PluginId => Init.PluginId;
    public string UserId => Init.UserId;
    public IReadOnlyList<string> Permissions => Init.Permissions;
    public JsonObject Config => Init.Config ?? new JsonObject();
    public IHostClient Host { get; }
    public IPluginLogger Log { get; }
    public bool HasPermission(string permission)
        => Permissions.Contains(permission, StringComparer.Ordinal);
}

internal sealed class PluginLogger : IPluginLogger
{
    private IHostClient? _host;

    public void Attach(IHostClient host) => _host = host;

    public void Debug(string message) => Write("debug", message);
    public void Info(string message) => Write("info", message);
    public void Warn(string message) => Write("warn", message);
    public void Error(string message, Exception? error = null)
        => Write("error", error is null ? message : $"{message}: {error}");

    private void Write(string level, string message)
    {
        Console.Error.WriteLine($"[{level}] {message}");
        if (_host is { IsAvailable: true }) _ = ForwardAsync(level, message);
    }

    private async Task ForwardAsync(string level, string message)
    {
        try { await _host!.LogAsync(level, message).ConfigureAwait(false); }
        catch (Exception) { /* 宿主已断开 */ }
    }
}
```

`PluginInitRequest` 与 `ManifestInfo` 与初版一致（见 `Contracts/`）：前者映射
`initialize.params`，后者读取 `manifest.json` 并校验能力子集。

### 4.2 `HostClient`

```csharp
using System.Text.Json;
using System.Text.Json.Nodes;
using Harness.Plugin.Sdk.Metadata;
using Harness.Plugin.Sdk.Protocol;

namespace Harness.Plugin.Sdk.Hosting;

public sealed class HostClient : IHostClient
{
    private readonly RpcPeer? _peer;

    private HostClient(RpcPeer? peer) { _peer = peer; }

    internal static HostClient Create(RpcPeer? peer) => new(peer);

    public bool IsAvailable => _peer is not null;

    public async Task<JsonElement?> StorageGetAsync(string key, CancellationToken ct = default)
    {
        var result = await RequestIdempotentAsync("host.storage.get",
            new JsonObject { ["key"] = key }, ct).ConfigureAwait(false);
        return result.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined ? null : result;
    }

    public async Task StorageSetAsync(string key, object? value, CancellationToken ct = default)
    {
        var node = value is null ? null : JsonSerializer.SerializeToNode(value, JsonDefaults.Options);
        await RequestAsync("host.storage.set",
            new JsonObject { ["key"] = key, ["value"] = node }, ct).ConfigureAwait(false);
    }

    public async Task<IReadOnlyList<string>> StorageListAsync(CancellationToken ct = default)
    {
        var result = await RequestIdempotentAsync("host.storage.list", new JsonObject(), ct)
            .ConfigureAwait(false);
        return result.ValueKind == JsonValueKind.Array
            ? result.EnumerateArray().Select(item => item.GetString() ?? "").ToArray()
            : [];
    }

    public Task StorageDeleteAsync(string key, CancellationToken ct = default)
        => RequestAsync("host.storage.delete", new JsonObject { ["key"] = key }, ct);

    public async Task<string> SecretGetAsync(string name, CancellationToken ct = default)
    {
        var result = await RequestAsync("host.secret.get",
            new JsonObject { ["name"] = name }, ct).ConfigureAwait(false);
        return result.ValueKind == JsonValueKind.String
            ? result.GetString() ?? ""
            : throw new PluginRpcException(RpcErrorCode.InternalError,
                "host.secret.get 返回值不是字符串");
    }

    public Task LogAsync(string level, string message, CancellationToken ct = default)
    {
        if (_peer is null)
        {
            Console.Error.WriteLine($"[{level}] {message}");
            return Task.CompletedTask;
        }
        return _peer.NotifyAsync("host.log",
            new JsonObject { ["level"] = level, ["message"] = message }, ct);
    }

    public Task ProgressAsync(JsonObject payload, CancellationToken ct = default)
        => _peer is null ? Task.CompletedTask : _peer.NotifyAsync("host.progress", payload, ct);

    private async Task<JsonElement> RequestAsync(string method, JsonObject parameters,
        CancellationToken ct)
    {
        if (_peer is null) throw Denied(method);
        return await _peer.RequestAsync(method, parameters, ct).ConfigureAwait(false);
    }

    private async Task<JsonElement> RequestIdempotentAsync(string method, JsonObject parameters,
        CancellationToken ct)
    {
        if (_peer is null) throw Denied(method);
        try
        {
            return await _peer.RequestAsync(method, parameters, ct).ConfigureAwait(false);
        }
        catch (PluginRpcException error) when (error.Code == RpcErrorCode.Timeout)
        {
            return await _peer.RequestAsync(method, parameters, ct).ConfigureAwait(false);
        }
    }

    private static PluginRpcException Denied(string method)
        => new(RpcErrorCode.PermissionDenied, $"当前形态不支持反向调用: {method}");
}
```

### 4.3 `PluginDispatcher`

```csharp
using System.Text.Json;
using System.Text.Json.Nodes;
using Harness.Plugin.Sdk.Contracts;
using Harness.Plugin.Sdk.Metadata;
using Harness.Plugin.Sdk.Protocol;

namespace Harness.Plugin.Sdk.Hosting;

public sealed class PluginDispatcher
{
    private readonly HarnessToolRegistry _registry;
    private readonly PluginCallbacks _callbacks;
    private readonly PluginOptions _options;
    private readonly PluginLogger _logger = new();
    private RpcPeer? _peer;
    private bool _shutdownCalled;

    internal PluginDispatcher(HarnessToolRegistry registry, PluginCallbacks callbacks,
        PluginOptions options)
    {
        _registry = registry;
        _callbacks = callbacks;
        _options = options;
    }

    public ToolContext? Context { get; private set; }

    public void AttachPeer(RpcPeer peer) => _peer = peer;

    public async Task<RpcOutcome> DispatchAsync(string method, JsonElement parameters,
        CancellationToken ct)
    {
        return method switch
        {
            "plugin.initialize" => await InitializeAsync(parameters, ct).ConfigureAwait(false),
            "plugin.describe" => Describe(),
            "plugin.health" => await HealthAsync(ct).ConfigureAwait(false),
            "plugin.shutdown" => RpcOutcome.Ok(new JsonObject { ["ok"] = true }),
            "tool.call" => await CallToolAsync(parameters, ct).ConfigureAwait(false),
            "resource.list" => ListResources(),
            "resource.read" => await ReadResourceAsync(parameters, ct).ConfigureAwait(false),
            "prompt.get" => GetPrompt(parameters),
            "hook.invoke" => await InvokeHookAsync(parameters, ct).ConfigureAwait(false),
            "event.notify" => await NotifyEventAsync(parameters, ct).ConfigureAwait(false),
            _ => RpcOutcome.Fail(RpcErrorCode.MethodNotFound, $"未知方法: {method}"),
        };
    }

    public async Task ShutdownAsync()
    {
        if (_shutdownCalled || Context is null) return;
        _shutdownCalled = true;
        if (_callbacks.Shutdown is not null)
        {
            try { await _callbacks.Shutdown(Context).ConfigureAwait(false); }
            catch (Exception error) { _logger.Error("OnShutdown 执行失败", error); }
        }
    }

    private async Task<RpcOutcome> InitializeAsync(JsonElement parameters, CancellationToken ct)
    {
        var init = JsonSerializer.Deserialize<PluginInitRequest>(
            parameters.GetRawText(), JsonDefaults.Options) ?? new PluginInitRequest();

        if (init.ProtocolVersion.Split('.', 2)[0] != "1")
            return RpcOutcome.Fail(RpcErrorCode.ProtocolVersionMismatch,
                $"协议版本不兼容: {init.ProtocolVersion}");

        var manifest = ManifestInfo.TryLoadFromBaseDirectory();
        if (_options.StrictManifest && manifest.PluginId.Length > 0
            && !string.Equals(manifest.PluginId, init.PluginId, StringComparison.Ordinal))
            return RpcOutcome.Fail(RpcErrorCode.CapabilityMismatch,
                $"manifest 与 initialize 的 plugin_id 不一致: {manifest.PluginId} vs {init.PluginId}");

        var host = HostClient.Create(_peer);
        _logger.Attach(host);
        Context = new ToolContext(init, host, _logger);

        if (_callbacks.Initialize is not null)
            await _callbacks.Initialize(Context, ct).ConfigureAwait(false);

        return RpcOutcome.Ok(new JsonObject { ["ok"] = true });
    }

    private RpcOutcome Describe()
    {
        var capabilities = new JsonObject
        {
            ["tool"] = _registry.DescribeTools(),
            ["resource"] = new JsonArray(),
            ["prompt"] = new JsonArray(),
            ["hook"] = new JsonArray(),
            ["ui"] = new JsonArray(),
        };

        if (_options.StrictManifest)
        {
            var manifest = ManifestInfo.TryLoadFromBaseDirectory();
            var reported = capabilities
                .Where(pair => pair.Value is JsonArray { Count: > 0 })
                .Select(pair => pair.Key);
            manifest.EnsureCapabilitiesDeclared(reported);
        }

        return RpcOutcome.Ok(new JsonObject
        {
            ["plugin_id"] = _options.PluginId ?? "",
            ["version"] = _options.Version ?? "",
            ["protocolVersion"] = _options.ProtocolVersion,
            ["capabilities"] = capabilities,
        });
    }

    private async Task<RpcOutcome> HealthAsync(CancellationToken ct)
        => Context is null
            ? RpcOutcome.Fail(RpcErrorCode.NotReady, "插件尚未初始化")
            : RpcOutcome.Ok(_callbacks.Health is not null
                ? await _callbacks.Health(Context, ct).ConfigureAwait(false)
                : new JsonObject { ["status"] = "ok" });

    private async Task<RpcOutcome> CallToolAsync(JsonElement parameters, CancellationToken ct)
    {
        if (Context is null) return RpcOutcome.Fail(RpcErrorCode.NotReady, "插件尚未初始化");

        if (!parameters.TryGetProperty("name", out var nameElement)
            || nameElement.ValueKind != JsonValueKind.String)
            return RpcOutcome.Fail(RpcErrorCode.InvalidParams, "tool.call 缺少 name");

        var name = nameElement.GetString()!;
        if (!_registry.TryGet(name, out var entry))
            return RpcOutcome.Fail(RpcErrorCode.MethodNotFound, $"未知工具: {name}");

        var arguments = parameters.TryGetProperty("arguments", out var raw) ? raw : default;
        var argumentsJson = arguments.ValueKind == JsonValueKind.Undefined
            ? "{}" : arguments.GetRawText();

        try
        {
            var args = ToolArgumentBinder.Bind(entry.Method, argumentsJson, ct);
            for (var index = 0; index < args.Length; index++)
            {
                if (entry.Method.GetParameters()[index].ParameterType == typeof(ToolContext))
                    args[index] = Context;
            }

            var result = await entry.Invoker(args, ct).ConfigureAwait(false);
            return RpcOutcome.Ok(new JsonObject { ["content"] = result.ToContent() });
        }
        catch (ToolBindingException error)
        {
            throw new PluginRpcException(RpcErrorCode.InvalidParams, error.Message);
        }
        catch (PluginRpcException) { throw; }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (Exception error)
        {
            _logger.Error($"工具 {name} 异常", error);
            throw new PluginRpcException(RpcErrorCode.ToolFailed, error.Message, inner: error);
        }
    }

    private RpcOutcome ListResources() => RpcOutcome.Ok(new JsonArray());

    private Task<RpcOutcome> ReadResourceAsync(JsonElement parameters, CancellationToken ct)
        => Task.FromResult(RpcOutcome.Fail(RpcErrorCode.NotFound, "未注册任何资源"));

    private RpcOutcome GetPrompt(JsonElement parameters)
        => RpcOutcome.Fail(RpcErrorCode.NotFound, "未注册任何提示词");

    private async Task<RpcOutcome> InvokeHookAsync(JsonElement parameters, CancellationToken ct)
    {
        if (Context is null || _callbacks.Hook is null) return RpcOutcome.Ok(null);
        var name = parameters.TryGetProperty("hook", out var hook) ? hook.GetString() ?? "" : "";
        var payload = parameters.TryGetProperty("payload", out var raw) ? raw : default;
        return RpcOutcome.Ok(await _callbacks.Hook(name, payload, Context, ct).ConfigureAwait(false));
    }

    private async Task<RpcOutcome> NotifyEventAsync(JsonElement parameters, CancellationToken ct)
    {
        if (Context is not null && _callbacks.Event is not null)
            await _callbacks.Event(parameters, Context, ct).ConfigureAwait(false);
        return RpcOutcome.Ok(new JsonObject());
    }
}

public sealed class PluginCallbacks
{
    public Func<ToolContext, CancellationToken, Task>? Initialize { get; set; }
    public Func<ToolContext, CancellationToken, Task<JsonObject>>? Health { get; set; }
    public Func<ToolContext, Task>? Shutdown { get; set; }
    public Func<string, JsonElement, ToolContext, CancellationToken, Task<JsonNode?>>? Hook { get; set; }
    public Func<JsonElement, ToolContext, CancellationToken, Task>? Event { get; set; }
}
```

### 4.4 `PluginHost`

```csharp
using System.Text.Json;
using System.Text.Json.Nodes;
using Harness.Plugin.Sdk.Metadata;
using Harness.Plugin.Sdk.Protocol;

namespace Harness.Plugin.Sdk.Hosting;

public sealed class PluginHost : IAsyncDisposable
{
    private readonly PluginDispatcher _dispatcher;
    private readonly CancellationTokenSource _stop = new();
    private RpcPeer? _peer;

    internal PluginHost(PluginDispatcher dispatcher) => _dispatcher = dispatcher;

    public async Task<int> RunAsync(CancellationToken ct = default)
    {
        // 顺序关键：先占原始流，再重定向 Console.Out
        var stdout = Console.OpenStandardOutput();
        var stdin = Console.OpenStandardInput();
        Console.SetOut(Console.Error);

        _peer = new RpcPeer(stdin, stdout, HandleRequestAsync, HandleNotificationAsync);
        _dispatcher.AttachPeer(_peer);

        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct, _stop.Token);
        ConsoleCancelEventHandler cancelHandler = (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            _stop.Cancel();
        };
        EventHandler exitHandler = (_, _) => _stop.Cancel();

        Console.CancelKeyPress += cancelHandler;
        AppDomain.CurrentDomain.ProcessExit += exitHandler;

        try
        {
            await _peer.RunAsync(linked.Token).ConfigureAwait(false);
        }
        finally
        {
            Console.CancelKeyPress -= cancelHandler;
            AppDomain.CurrentDomain.ProcessExit -= exitHandler;
            await _dispatcher.ShutdownAsync().ConfigureAwait(false);

            var peer = _peer;
            _peer = null;
            if (peer is not null) await peer.DisposeAsync().ConfigureAwait(false);
        }

        return 0;
    }

    private async Task<RpcOutcome> HandleRequestAsync(string method, JsonElement parameters,
        CancellationToken ct)
    {
        var outcome = await _dispatcher.DispatchAsync(method, parameters, ct).ConfigureAwait(false);

        if (method == "plugin.shutdown")
        {
            _ = Task.Run(async () =>
            {
                await Task.Delay(200).ConfigureAwait(false);
                _stop.Cancel();
            });
        }
        return outcome;
    }

    private Task HandleNotificationAsync(string method, JsonElement parameters, CancellationToken ct)
        => _dispatcher.DispatchAsync(method, parameters, ct);

    public async ValueTask DisposeAsync()
    {
        await _stop.CancelAsync().ConfigureAwait(false);
        if (_peer is not null) await _peer.DisposeAsync().ConfigureAwait(false);
        _stop.Dispose();
    }
}

public sealed class PluginBuilder
{
    private readonly HarnessToolRegistry _registry = new();
    private readonly PluginCallbacks _callbacks = new();
    private readonly PluginOptions _options;

    private PluginBuilder(PluginOptions options) => _options = options;

    public static PluginBuilder Create(PluginOptions? options = null)
        => new(options ?? OptionsFromManifest());

    public static PluginOptions OptionsFromManifest()
    {
        var manifest = Contracts.ManifestInfo.TryLoadFromBaseDirectory();
        return new PluginOptions
        {
            PluginId = manifest.PluginId,
            Version = manifest.Version,
            ProtocolVersion = manifest.ProtocolVersion,
        };
    }

    public PluginBuilder Use<T>() where T : new() => AddTools(new T());

    public PluginBuilder AddTools(object instance)
    {
        _registry.Register(instance);
        return this;
    }

    public PluginBuilder AddTools(Func<object> factory, Type type)
    {
        _registry.Register(type, factory);
        return this;
    }

    public PluginBuilder OnInitialize(Func<ToolContext, CancellationToken, Task> callback)
    {
        _callbacks.Initialize = callback;
        return this;
    }

    public PluginBuilder OnHealth(Func<ToolContext, CancellationToken, Task<JsonObject>> callback)
    {
        _callbacks.Health = callback;
        return this;
    }

    public PluginBuilder OnShutdown(Func<ToolContext, Task> callback)
    {
        _callbacks.Shutdown = callback;
        return this;
    }

    public PluginBuilder OnHook(
        Func<string, JsonElement, ToolContext, CancellationToken, Task<JsonNode?>> handler)
    {
        _callbacks.Hook = handler;
        return this;
    }

    public PluginHost Build() => new(new PluginDispatcher(_registry, _callbacks, _options));
}
```

`PluginOptions`：

```csharp
public sealed class PluginOptions
{
    public string? PluginId { get; init; }
    public string? Version { get; init; }
    public string ProtocolVersion { get; init; } = "1.0";
    public bool StrictManifest { get; init; } = true;
}
```

---

## 5. 形态 A：入口、manifest 与打包

### 5.1 `Program.cs`

```csharp
using Harness.Plugin.Sdk.Hosting;

await using var host = PluginBuilder
    .Create()
    .Use<EchoTools>()
    .OnInitialize((context, _) =>
    {
        context.Log.Info($"C# 插件已初始化 user={context.UserId}");
        return Task.CompletedTask;
    })
    .Build();

return await host.RunAsync();
```

### 5.2 工具实现（两种形态通用）

```csharp
using System.ComponentModel;
using System.Globalization;
using System.Text.Json;
using Harness.Plugin.Sdk.Hosting;
using Harness.Plugin.Sdk.Metadata;

public sealed record AddRequest(
    [property: Description("第一个数")] double A,
    [property: Description("第二个数")] double B);

public sealed class EchoTools
{
    [HarnessTool("echo", Description = "原样回显文本", TimeoutMs = 10_000)]
    public string Echo([HarnessParam("要回显的文本")] string text) => text;

    // 单 DTO 模式：gRPC 推荐写法，插件模式同样可用
    [HarnessTool("add", Description = "计算两数之和")]
    public Task<string> AddAsync(AddRequest request)
        => Task.FromResult((request.A + request.B).ToString(CultureInfo.InvariantCulture));

    [HarnessTool("count", Description = "累计调用次数", TimeoutMs = 10_000)]
    public async Task<string> CountAsync(ToolContext context, CancellationToken ct)
    {
        var stored = await context.Host.StorageGetAsync("count", ct);
        var current = stored is { ValueKind: JsonValueKind.Number } ? stored.Value.GetInt32() : 0;
        current++;
        await context.Host.StorageSetAsync("count", current, ct);
        return $"这是第 {current} 次调用";
    }
}
```

### 5.3 `manifest.json`

```json
{
  "plugin_id": "csharp-echo",
  "name": "C# 回显示例",
  "version": "1.0.0",
  "protocolVersion": "1.0",
  "description": "演示 C# SDK 的最小插件。",
  "author": { "name": "Local" },
  "license": "MIT",
  "tag": ["示例", "C#"],
  "capabilities": ["tool"],
  "runtime": {
    "kind": "subprocess",
    "language": "dotnet",
    "command": "dotnet",
    "args": ["CSharpEchoPlugin.dll"]
  },
  "transport": { "kind": "stdio" },
  "permissions": ["storage"],
  "env": { "dotnet": ">=10.0" },
  "harnessVersion": { "min": "1.1.0", "max": "1.x" },
  "configSchema": { "type": "object", "properties": {} },
  "dist": {}
}
```

打包：

```bash
dotnet publish samples/EchoPlugin/CSharpEchoPlugin.csproj -c Release -o dist/csharp-echo \
  --no-self-contained /p:PublishSingleFile=false
python src/agent/skills/main/plugin-development/scripts/smoke_plugin.py dist/csharp-echo
```

### 5.4 `Bridge/ExternalProcessTool`：包装已有 exe

已有控制台程序不必重写：约定「stdin 收 JSON、stdout 回 JSON 或纯文本」即可包成工具。
需要 manifest 声明 `process` 权限。

```csharp
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Harness.Plugin.Sdk.Metadata;
using Harness.Plugin.Sdk.Protocol;

namespace Harness.Plugin.Sdk.Bridge;

public sealed record ExternalProcessOptions
{
    public required string FileName { get; init; }
    public IReadOnlyList<string> Arguments { get; init; } = [];
    public string? WorkingDirectory { get; init; }
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(30);
    public bool WriteJsonToStandardInput { get; init; } = true;
}

public static class ExternalProcessTool
{
    public static async Task<ToolResult> InvokeAsync(ExternalProcessOptions options,
        JsonNode? arguments, ToolContext context, CancellationToken ct)
    {
        if (!context.HasPermission("process"))
            throw new PluginRpcException(RpcErrorCode.PermissionDenied,
                "未声明 process 权限，不能启动外部进程");

        var startInfo = new ProcessStartInfo(options.FileName)
        {
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var argument in options.Arguments) startInfo.ArgumentList.Add(argument);
        if (!string.IsNullOrEmpty(options.WorkingDirectory))
            startInfo.WorkingDirectory = options.WorkingDirectory;

        using var process = Process.Start(startInfo)
            ?? throw new ToolBindingException("PROCESS_START_FAILED", "无法启动外部进程");

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(options.Timeout);

        var stdoutTask = process.StandardOutput.ReadToEndAsync(timeout.Token);
        var stderrTask = process.StandardError.ReadToEndAsync(timeout.Token);

        try
        {
            if (options.WriteJsonToStandardInput)
            {
                var payload = (arguments ?? new JsonObject()).ToJsonString(JsonDefaults.Options);
                await process.StandardInput.WriteAsync(payload.AsMemory(), timeout.Token);
                await process.StandardInput.FlushAsync(timeout.Token);
            }
            process.StandardInput.Close();

            await process.WaitForExitAsync(timeout.Token);
            var stdout = await stdoutTask;
            var stderr = await stderrTask;

            if (!string.IsNullOrWhiteSpace(stderr)) context.Log.Warn(stderr.Trim());
            if (process.ExitCode != 0)
                return ToolResult.Fail("PROCESS_FAILED",
                    $"外部进程退出码 {process.ExitCode}: {Trim(stderr)}",
                    "检查外部程序的日志与参数。");

            return ToolResult.Text(Normalize(stdout));
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            TryKill(process);
            return ToolResult.Fail("TIMEOUT",
                $"外部进程超时（{options.Timeout.TotalSeconds:0}s）", "缩小任务或提高 Timeout。");
        }
    }

    private static string Normalize(string stdout)
    {
        try
        {
            using var document = JsonDocument.Parse(stdout);
            if (document.RootElement.ValueKind == JsonValueKind.Object
                && document.RootElement.TryGetProperty("content", out var content)
                && content.ValueKind == JsonValueKind.String)
                return content.GetString() ?? "";
        }
        catch (JsonException)
        {
            // 非 JSON 按纯文本
        }
        return stdout.Trim();
    }

    private static string Trim(string text) => text.Length <= 400 ? text : text[..400];

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
        }
        catch (Exception)
        {
            // 已退出
        }
    }
}
```

用法：

```csharp
[HarnessTool("legacy_report", Description = "调用已有报表程序", TimeoutMs = 120_000)]
public Task<ToolResult> LegacyReportAsync(JsonObject payload, ToolContext context, CancellationToken ct)
    => ExternalProcessTool.InvokeAsync(new ExternalProcessOptions
    {
        FileName = Path.Combine(AppContext.BaseDirectory, "bin", "LegacyReport.exe"),
        Arguments = ["--json"],
        Timeout = TimeSpan.FromMinutes(2),
    }, payload, context, ct);
```

约束：禁止字符串拼接参数（统一 `ArgumentList`）；跨平台文件名用
`RuntimeInformation.IsOSPlatform` 判断；长任务用 `host.progress` 上报。

> 形态 B 的工具方法不要声明 `ToolContext` 参数（gRPC 没有插件上下文），
> 需要配置请在构造工具实例时注入。

---

## 6. 形态 B：ToolService 服务端

### 6.1 `ToolServiceOptions`

```csharp
using Microsoft.Extensions.Logging;

namespace Harness.Plugin.Sdk.Grpc.Server;

public sealed class ToolServiceOptions
{
    // 0 表示由 Kestrel 自动分配（测试/多实例推荐）
    public int Port { get; set; } = 50052;

    public string ExpectedContractVersion { get; set; } = ContractVersion.Value;

    // 审批记录单次使用，默认 300s 过期
    public TimeSpan ApprovalTtl { get; set; } = TimeSpan.FromSeconds(300);

    // call_id 幂等缓存，默认 600s
    public TimeSpan IdempotencyTtl { get; set; } = TimeSpan.FromSeconds(600);

    public LogLevel MinimumLogLevel { get; set; } = LogLevel.Warning;

    public Action<string>? Log { get; set; }
}
```

### 6.2 `ToolDescriptorFactory`

元数据层只有一份，这里只做 CLR → proto 的投影。

```csharp
using Harness.Plugin.Sdk.Metadata;
using Proto = Agent.Proto;

namespace Harness.Plugin.Sdk.Grpc.Server;

public static class ToolDescriptorFactory
{
    public static Proto.ToolDescriptor ToProto(ToolMetadata tool) => new()
    {
        Name = tool.Name,
        DisplayName = tool.DisplayName,
        Description = tool.Description,
        Domain = tool.Domain,
        Skill = tool.Skill,
        ArgsSchemaJson = tool.ArgsSchemaJson,
        ResultSchemaJson = tool.ResultSchemaJson,
        SandboxLevel = ToProto(tool.SandboxLevel),
        IsWrite = tool.IsWrite,
        IsDestructive = tool.IsDestructive,
        SupportsEditArgs = tool.SupportsEditArgs,
        TimeoutMs = tool.TimeoutMs,
        Version = tool.Version,
    };

    public static Proto.SkillDefinition ToProto(SkillDefinition skill)
    {
        var proto = new Proto.SkillDefinition
        {
            Name = skill.Name,
            DisplayName = skill.DisplayName,
            Description = skill.Description,
            PromptFragment = skill.PromptFragment,
            MetadataJson = skill.MetadataJson,
        };
        foreach (var tool in skill.Tools) proto.Tools.Add(ToProto(tool));
        return proto;
    }

    public static Proto.SandboxLevel ToProto(SandboxLevel level) => level switch
    {
        SandboxLevel.Confirm => Proto.SandboxLevel.Confirm,
        SandboxLevel.AlwaysConfirm => Proto.SandboxLevel.AlwaysConfirm,
        _ => Proto.SandboxLevel.Auto,
    };

    public static Proto.TrajectoryEvent ToProto(TrajectoryEvent evt) => new()
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
```

### 6.3 `ToolApprovalBroker`

语义与现有实现一致：事件只通知不阻塞；`DecisionProvider` 优先，其次 `DefaultDecision`；
决策方抛异常按拒绝处理。

```csharp
using Proto = Agent.Proto;

namespace Harness.Plugin.Sdk.Grpc.Server;

public sealed class ToolApprovalRequest
{
    public required string ApprovalId { get; init; }
    public required string CallId { get; init; }
    public required string ToolName { get; init; }
    public string DisplayName { get; init; } = "";
    public string ArgumentsJson { get; init; } = "";
    public string ThreadId { get; init; } = "";
    public string RunId { get; init; } = "";
    public bool SupportsEditArgs { get; init; }
    public DateTime ExpiresAtUtc { get; init; }
}

public sealed class ToolApprovalBroker
{
    public event EventHandler<ToolApprovalRequest>? ApprovalRequested;

    // UI 接入点：返回 Approve / Reject / Edit
    public Func<ToolApprovalRequest, Proto.ApprovalDecision>? DecisionProvider { get; set; }

    // 无 DecisionProvider 时的默认值；生产环境建议改为 Reject
    public Proto.ApprovalDecision DefaultDecision { get; set; } = Proto.ApprovalDecision.Approve;

    public void NotifyRequested(ToolApprovalRequest request)
    {
        try { ApprovalRequested?.Invoke(this, request); }
        catch (Exception) { /* 订阅方异常不影响主链路 */ }
    }

    public Proto.ApprovalDecision Resolve(ToolApprovalRequest request)
    {
        try { return DecisionProvider?.Invoke(request) ?? DefaultDecision; }
        catch (Exception) { return Proto.ApprovalDecision.Reject; }
    }
}
```

### 6.4 `ToolGrpcService`

```csharp
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Grpc.Core;
using Harness.Plugin.Sdk.Metadata;
using Microsoft.Extensions.Logging;
using Proto = Agent.Proto;
using SdkTrajectoryEvent = Harness.Plugin.Sdk.Metadata.TrajectoryEvent;

namespace Harness.Plugin.Sdk.Grpc.Server;

public sealed class ToolGrpcService : Proto.ToolService.ToolServiceBase
{
    private readonly HarnessToolRegistry _registry;
    private readonly ToolApprovalBroker _approvals;
    private readonly ToolServiceOptions _options;
    private readonly ILogger<ToolGrpcService>? _logger;

    private readonly ConcurrentDictionary<string, PendingApproval> _pending = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, CachedCall> _cache = new(StringComparer.Ordinal);

    public ToolGrpcService(HarnessToolRegistry registry, ToolApprovalBroker approvals,
        ToolServiceOptions options, ILogger<ToolGrpcService>? logger = null)
    {
        _registry = registry;
        _approvals = approvals;
        _options = options;
        _logger = logger;
    }

    public override Task<Proto.ListToolsResponse> ListTools(Proto.ListToolsRequest request,
        ServerCallContext context)
    {
        if (!IsCompatible(request.ContractVersion))
        {
            // 与现有实现一致：发现阶段直接失败，避免带着错误契约继续跑
            throw new RpcException(new Status(StatusCode.FailedPrecondition,
                $"契约版本不兼容：C# {_options.ExpectedContractVersion}，客户端 {request.ContractVersion}"));
        }

        var response = new Proto.ListToolsResponse
        {
            ContractVersion = _options.ExpectedContractVersion,
        };
        foreach (var skill in _registry.GetSkills())
        {
            response.Skills.Add(ToolDescriptorFactory.ToProto(skill));
        }
        return Task.FromResult(response);
    }

    public override async Task<Proto.ToolCallResult> CallTool(Proto.ToolCallRequest request,
        ServerCallContext context)
    {
        if (!IsCompatible(request.ContractVersion))
        {
            return Error(request.CallId, "CONTRACT_VERSION_MISMATCH",
                $"契约版本不兼容：C# {_options.ExpectedContractVersion}，客户端 {request.ContractVersion}",
                "请使用同一份 proto 重新生成两端桩代码。");
        }

        if (!_registry.TryGet(request.ToolName, out var registration))
        {
            return Error(request.CallId, "TOOL_NOT_FOUND", $"未找到工具 {request.ToolName}。",
                "先调用 ListTools 获取当前可用工具，不要猜工具名。");
        }

        if (TryGetCached(request, out var cached, out var conflict))
        {
            return conflict
                ? Error(request.CallId, "CALL_ID_CONFLICT",
                    $"call_id {request.CallId} 已用于不同参数。", "同一 call_id 只能对应一次调用。")
                : cached!;
        }

        if (registration.Metadata.SandboxLevel != SandboxLevel.Auto)
        {
            return RequestApproval(request, registration);
        }

        return await ExecuteAsync(request, registration, request.ArgumentsJson,
            context.CancellationToken).ConfigureAwait(false);
    }

    public override async Task<Proto.ToolCallResult> ResolveApproval(
        Proto.ResolveApprovalRequest request, ServerCallContext context)
    {
        if (!IsCompatible(request.ContractVersion))
        {
            return Error(request.CallId, "CONTRACT_VERSION_MISMATCH",
                $"契约版本不兼容：C# {_options.ExpectedContractVersion}，客户端 {request.ContractVersion}",
                "请使用同一份 proto 重新生成两端桩代码。");
        }

        if (!_pending.TryRemove(request.ApprovalId, out var pending))
        {
            return Error(request.CallId, "APPROVAL_NOT_FOUND",
                $"未找到待审批记录 {request.ApprovalId}。", "审批记录单次使用，请重新发起工具调用。");
        }

        if (pending.ExpiresAtUtc < DateTime.UtcNow)
        {
            return Error(request.CallId, "APPROVAL_EXPIRED", "审批记录已过期。",
                "请重新发起工具调用并尽快审批。");
        }

        if (!_registry.TryGet(pending.ToolName, out var registration))
        {
            return Error(request.CallId, "TOOL_NOT_FOUND", $"未找到工具 {pending.ToolName}。",
                "工具可能在审批期间被移除。");
        }

        var decision = request.Decision;
        if (decision == Proto.ApprovalDecision.Unspecified)
        {
            decision = _approvals.Resolve(new ToolApprovalRequest
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

        if (decision == Proto.ApprovalDecision.Reject)
        {
            var denied = new Proto.ToolCallResult
            {
                Success = false,
                Status = Proto.ToolStatus.Denied,
                CallId = pending.CallId,
                Error = "用户拒绝了该工具执行。",
                Detail = new Proto.ToolError
                {
                    Code = "APPROVAL_DENIED",
                    Message = "用户拒绝了该工具执行。",
                    Hint = "不要自动重试，也不要用其他工具绕过审批；把拒绝结果如实告知用户。",
                    Retryable = false,
                },
            };
            denied.Trajectory.Add(ToolDescriptorFactory.ToProto(new SdkTrajectoryEvent
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
            Cache(pending.CallId, pending.ArgumentsJson, denied);
            return denied;
        }

        var argumentsJson = pending.ArgumentsJson;
        if (decision == Proto.ApprovalDecision.Edit)
        {
            if (!pending.SupportsEditArgs)
            {
                return Error(pending.CallId, "EDIT_NOT_SUPPORTED",
                    $"工具 {pending.ToolName} 不允许审批时修改参数。", "请改用 APPROVE 或 REJECT。");
            }
            if (string.IsNullOrWhiteSpace(request.EditedArgumentsJson))
            {
                return Error(pending.CallId, "INVALID_ARGUMENT", "EDIT 决策必须提供 edited_arguments_json。",
                    "传入完整的新参数 JSON。");
            }
            argumentsJson = request.EditedArgumentsJson;
        }

        var synthetic = new Proto.ToolCallRequest
        {
            ToolName = pending.ToolName,
            ArgumentsJson = argumentsJson,
            ThreadId = pending.ThreadId,
            CallId = pending.CallId,
            RunId = pending.RunId,
            ContractVersion = _options.ExpectedContractVersion,
        };

        var result = await ExecuteAsync(synthetic, registration, argumentsJson,
            context.CancellationToken).ConfigureAwait(false);

        result.Trajectory.Add(ToolDescriptorFactory.ToProto(new SdkTrajectoryEvent
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

    private Proto.ToolCallResult RequestApproval(Proto.ToolCallRequest request,
        ToolRegistration registration)
    {
        var approvalId = Guid.NewGuid().ToString("N");
        var expiresAt = DateTime.UtcNow.Add(_options.ApprovalTtl);
        var pending = new PendingApproval
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
        _pending[approvalId] = pending;

        _approvals.NotifyRequested(new ToolApprovalRequest
        {
            ApprovalId = approvalId,
            CallId = request.CallId,
            ToolName = request.ToolName,
            DisplayName = registration.Metadata.DisplayName,
            ArgumentsJson = request.ArgumentsJson,
            ThreadId = request.ThreadId,
            RunId = request.RunId,
            SupportsEditArgs = pending.SupportsEditArgs,
            ExpiresAtUtc = expiresAt,
        });

        var result = new Proto.ToolCallResult
        {
            Success = false,
            Status = Proto.ToolStatus.NeedsApproval,
            CallId = request.CallId,
            ApprovalId = approvalId,
            Error = "该工具需要人工审批，尚未执行。",
            Detail = new Proto.ToolError
            {
                Code = "APPROVAL_REQUIRED",
                Message = "该工具需要人工审批，尚未执行。",
                Hint = "请调用 ResolveApproval(approval_id, decision)。",
                Retryable = false,
            },
        };
        result.Trajectory.Add(ToolDescriptorFactory.ToProto(new SdkTrajectoryEvent
        {
            RunId = request.RunId,
            ThreadId = request.ThreadId,
            Type = "hitl",
            Name = request.ToolName,
            Status = "requested",
            ArgsJson = request.ArgumentsJson,
            StartedAt = IsoNow(),
            MetadataJson = $"{{\"source\":\"csharp\",\"approval_id\":\"{approvalId}\"}}",
        }));
        return result;
    }

    private async Task<Proto.ToolCallResult> ExecuteAsync(Proto.ToolCallRequest request,
        ToolRegistration registration, string argumentsJson, CancellationToken cancellationToken)
    {
        var stopwatch = Stopwatch.StartNew();
        var startedAt = IsoNow();
        var trajectory = new List<SdkTrajectoryEvent>
        {
            new()
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
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromMilliseconds(registration.Metadata.TimeoutMs));

            var args = ToolArgumentBinder.Bind(registration.Method, argumentsJson, timeout.Token);
            result = await registration.Invoker(args, timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            result = cancellationToken.IsCancellationRequested
                ? ToolResult.Fail("CANCELLED", "工具执行已取消。", "确认调用方是否主动取消。")
                : ToolResult.Fail("TIMEOUT",
                    $"工具执行超过 {registration.Metadata.TimeoutMs} ms。",
                    "确认工具是否响应 CancellationToken，或拆分长任务。");
        }
        catch (ToolBindingException error)
        {
            result = ToolResult.Fail(error.Code, error.Message, error.Hint);
        }
        catch (JsonException error)
        {
            result = ToolResult.Fail("INVALID_ARGUMENT",
                $"arguments_json 不是合法 JSON：{error.Message}",
                "传入 UTF-8 JSON 对象，键名与 args_schema_json 一致。");
        }
        catch (Exception error)
        {
            result = ToolResult.Fail("TOOL_EXCEPTION", error.Message,
                "检查工具实现；同一工具连续失败两次后应停止重试。");
        }

        stopwatch.Stop();
        trajectory.Add(new SdkTrajectoryEvent
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

        var response = new Proto.ToolCallResult
        {
            Success = result.Success,
            Status = result.Success ? Proto.ToolStatus.Ok : Proto.ToolStatus.Error,
            ResultJson = result.ResultJson,
            CallId = request.CallId,
            DurationMs = stopwatch.ElapsedMilliseconds,
        };

        if (!result.Success && result.Error is { } error)
        {
            response.Error = error.Message;
            response.Detail = new Proto.ToolError
            {
                Code = error.Code,
                Message = error.Message,
                Hint = error.Hint,
                Retryable = error.Retryable,
            };
        }

        foreach (var evt in trajectory)
        {
            response.Trajectory.Add(ToolDescriptorFactory.ToProto(evt));
        }

        Cache(request.CallId, argumentsJson, response);
        _logger?.LogDebug("工具 {Tool} 执行完成：success={Success}, {Elapsed}ms",
            request.ToolName, result.Success, stopwatch.ElapsedMilliseconds);
        return response;
    }

    private bool TryGetCached(Proto.ToolCallRequest request, out Proto.ToolCallResult? cached,
        out bool conflict)
    {
        cached = null;
        conflict = false;
        if (string.IsNullOrWhiteSpace(request.CallId)) return false;
        if (!_cache.TryGetValue(request.CallId, out var entry)) return false;

        if (entry.ExpiresAtUtc < DateTime.UtcNow)
        {
            _cache.TryRemove(request.CallId, out _);
            return false;
        }

        if (entry.ArgumentsHash == Hash(request.ArgumentsJson))
        {
            cached = entry.Result.Clone();
            return true;
        }

        conflict = true;
        return true;
    }

    private void Cache(string callId, string argumentsJson, Proto.ToolCallResult result)
    {
        if (string.IsNullOrWhiteSpace(callId)) return;
        _cache[callId] = new CachedCall
        {
            ArgumentsHash = Hash(argumentsJson),
            Result = result.Clone(),
            ExpiresAtUtc = DateTime.UtcNow.Add(_options.IdempotencyTtl),
        };
    }

    private static string Hash(string text)
        => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text ?? "")));

    private static Proto.ToolCallResult Error(string callId, string code, string message, string hint)
        => new()
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

    private bool IsCompatible(string remote) => ContractVersion.IsCompatible(remote);

    private static string IsoNow() => DateTime.UtcNow.ToString("O");

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

    private sealed class CachedCall
    {
        public string ArgumentsHash { get; init; } = "";
        public Proto.ToolCallResult Result { get; init; } = new();
        public DateTime ExpiresAtUtc { get; init; }
    }
}
```

> `ContractVersion` 在命名空间 `Harness.Plugin.Sdk.Grpc`，`ToolGrpcService` 在
> `.Grpc.Server`，父命名空间自动可见。若你把类放到别的命名空间，显式 `using`。

### 6.5 `HarnessToolServer`

```csharp
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Harness.Plugin.Sdk.Metadata;

namespace Harness.Plugin.Sdk.Grpc.Server;

public sealed class HarnessToolServer : IAsyncDisposable
{
    private readonly HarnessToolRegistry _registry;
    private readonly ToolApprovalBroker _approvals;
    private readonly ToolServiceOptions _options;
    private WebApplication? _app;
    private bool _disposed;

    public HarnessToolServer(HarnessToolRegistry registry, ToolApprovalBroker approvals,
        ToolServiceOptions? options = null)
    {
        _registry = registry;
        _approvals = approvals;
        _options = options ?? new ToolServiceOptions();
    }

    public ToolApprovalBroker Approvals => _approvals;

    public bool IsRunning { get; private set; }

    // 启动后有效；Port=0 时是实际分配端口
    public int Port { get; private set; }

    public async Task StartAsync(CancellationToken ct = default)
    {
        if (IsRunning) throw new InvalidOperationException("ToolService 已经启动。");

        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            ContentRootPath = AppContext.BaseDirectory,
        });

        // 避免 WPF 进程被外部 hosting startup 注入
        builder.WebHost.UseSetting(WebHostDefaults.PreventHostingStartupKey, "true");
        builder.WebHost.ConfigureKestrel(kestrel =>
        {
            kestrel.ListenLocalhost(_options.Port, listen => listen.Protocols = HttpProtocols.Http2);
        });

        builder.Logging.SetMinimumLevel(_options.MinimumLogLevel);
        builder.Services.AddSingleton(_registry);
        builder.Services.AddSingleton(_approvals);
        builder.Services.AddSingleton(_options);
        builder.Services.AddGrpc();

        _app = builder.Build();
        _app.MapGrpcService<ToolGrpcService>();

        try
        {
            await _app.StartAsync(ct).ConfigureAwait(false);
        }
        catch
        {
            await _app.DisposeAsync().ConfigureAwait(false);
            _app = null;
            throw;
        }

        Port = _options.Port;
        var addresses = _app.Services.GetService<IServer>()?.Features.Get<ServerAddressesFeature>();
        if (addresses is not null)
        {
            foreach (var address in addresses.Addresses)
            {
                if (Uri.TryCreate(address, UriKind.Absolute, out var uri))
                {
                    Port = uri.Port;
                    break;
                }
            }
        }

        IsRunning = true;
        _options.Log?.Invoke($"[ToolService] 已启动：127.0.0.1:{Port} (h2c)");
    }

    public async Task StopAsync(CancellationToken ct = default)
    {
        if (_app is null) { IsRunning = false; return; }
        try { await _app.StopAsync(ct).ConfigureAwait(false); }
        finally
        {
            await _app.DisposeAsync().ConfigureAwait(false);
            _app = null;
            IsRunning = false;
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_disposed) return;
        _disposed = true;
        await StopAsync().ConfigureAwait(false);
    }
}
```

### 6.6 组合入口（控制台 / WPF 通用）

```csharp
using Harness.Plugin.Sdk.Grpc.Server;
using Harness.Plugin.Sdk.Metadata;

var registry = new HarnessToolRegistry();
registry.Register(new SceneTools());          // 实例：可注入业务服务
registry.Register(typeof(UtilityTools));      // 静态/无参类型

var approvals = new ToolApprovalBroker
{
    // 生产建议：接 UI 或改为 Reject
    DecisionProvider = request =>
    {
        Console.Error.WriteLine($"请求审批：{request.ToolName} {request.ArgumentsJson}");
        return Agent.Proto.ApprovalDecision.Approve;
    },
};

await using var server = new HarnessToolServer(registry, approvals, new ToolServiceOptions { Port = 50052 });
await server.StartAsync();
Console.Error.WriteLine($"ToolService 端口：{server.Port}");

await Task.Delay(Timeout.Infinite);
```

WPF 中把 `StartAsync/StopAsync` 接到 `App.OnStartup/OnExit`；工具实现若操作可视树，
在方法内封 `Application.Current.Dispatcher.InvokeAsync(...)`，不要依赖 gRPC 线程。

---

## 7. 形态 B：客户端

### 7.1 事件模型

用独立的记录类型表达 `ChatChunk` 的 `oneof`，UI 直接 `switch`。

```csharp
namespace Harness.Plugin.Sdk.Grpc.Client;

public abstract record ChatEvent;

public sealed record TextDeltaEvent(string Text) : ChatEvent;

public sealed record MessageEvent(string MessageId, string Role, string Content,
    string ToolCallId, string Name, string CreatedAt) : ChatEvent;

public sealed record ChatToolCallEvent(string CallId, string RunId, string ToolName,
    string ArgumentsJson, string ThreadId, bool IsPartial) : ChatEvent;

public sealed record ChatToolResultEvent(string CallId, string ToolName, string ResultJson,
    string ErrorJson, int Status, string ThreadId) : ChatEvent;

public sealed record TrajectoryChatEvent(string EventId, string RunId, string ThreadId,
    string ParentId, string Type, string Name, string Status, string ArgsJson,
    string ResultJson, string StartedAt, string EndedAt, string MetadataJson) : ChatEvent;

public sealed record DoneEvent(string Message, string ThreadId, string FinishReason,
    string UsageJson, int Status) : ChatEvent;

public sealed record ErrorEvent(string Code, string Message, string Hint, bool Retryable) : ChatEvent;

public sealed record ReasoningEvent(string Delta, string RunId, string StepId) : ChatEvent;

public sealed record HitlEvent(string HitlId, string ThreadId, string Kind, string Title,
    string Description, IReadOnlyList<HitlField> Fields, IReadOnlyList<HitlOption> Options,
    bool AllowMultiple, bool AllowFreeText, string SubmitLabel, string CancelLabel,
    string ResumeSchemaJson, string MetadataJson) : ChatEvent;

public sealed record HitlField(string Name, string Label, string Type, bool Required,
    string DefaultJson, string Placeholder, string Description,
    IReadOnlyList<HitlOption> Options);

public sealed record HitlOption(string Value, string Label, string Description, bool Recommended);

public sealed record PlanEvent(string PlanId, string RunId, string ThreadId, int Version,
    IReadOnlyList<PlanStep> Steps, string ReplanReason, string Status, string CreatedAt) : ChatEvent;

public sealed record PlanStep(string StepId, string Domain, string Goal,
    IReadOnlyList<string> DependsOn, IReadOnlyList<string> ExpectedTools, string Acceptance,
    bool IsVisualCheck, string Status, string Error);
```

### 7.2 `HarnessChatClient`

```csharp
using System.Net.Sockets;
using System.Runtime.CompilerServices;
using Grpc.Core;
using Grpc.Net.Client;
using Proto = Agent.Proto;

namespace Harness.Plugin.Sdk.Grpc.Client;

public sealed class HarnessChatClientOptions
{
    public string Address { get; set; } = "http://127.0.0.1:50051";
    public string ThreadId { get; set; } = "";
    public string UserId { get; set; } = "csharp_host";
    public string Username { get; set; } = "C# Host";
    public double DeadlineMinutes { get; set; } = 10;

    public string Host => Uri.TryCreate(Address, UriKind.Absolute, out var uri) ? uri.Host : "127.0.0.1";
    public int Port => Uri.TryCreate(Address, UriKind.Absolute, out var uri) ? uri.Port : 50051;
}

public sealed class HarnessChatClient : IDisposable
{
    private readonly GrpcChannel _channel;
    private readonly bool _ownsChannel;
    private readonly Proto.ChatService.ChatServiceClient _client;
    private bool _disposed;

    public HarnessChatClient(HarnessChatClientOptions? options = null, GrpcChannel? channel = null)
    {
        Options = options ?? new HarnessChatClientOptions();
        _channel = channel ?? GrpcChannel.ForAddress(Options.Address);
        _ownsChannel = channel is null;
        _client = new Proto.ChatService.ChatServiceClient(_channel);
        ThreadId = string.IsNullOrWhiteSpace(Options.ThreadId)
            ? Guid.NewGuid().ToString("N")
            : Options.ThreadId;
    }

    public HarnessChatClientOptions Options { get; }

    public string ThreadId { get; set; }

    public bool IsAvailable { get; private set; }

    public event EventHandler<bool>? AvailabilityChanged;

    // TCP 探测，不消耗 token
    public async Task<bool> ProbeAsync(TimeSpan timeout, CancellationToken ct = default)
    {
        var ready = false;
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
        linked.CancelAfter(timeout);
        try
        {
            using var client = new TcpClient();
            await client.ConnectAsync(Options.Host, Options.Port, linked.Token).ConfigureAwait(false);
            ready = true;
        }
        catch (Exception)
        {
            ready = false;
        }
        SetAvailable(ready);
        return ready;
    }

    public IAsyncEnumerable<ChatEvent> StreamAsync(string message, ChatMode mode,
        string? threadId = null, CancellationToken ct = default)
    {
        var request = new Proto.ChatRequest
        {
            Humanmsg = message ?? "",
            ThreadId = string.IsNullOrWhiteSpace(threadId) ? ThreadId : threadId,
            Mode = mode == ChatMode.Task ? Proto.ChatMode.Task : Proto.ChatMode.Chat,
            ContractVersion = ContractVersion.Value,
            UserId = Options.UserId,
            Username = Options.Username,
        };

        return StreamCore(
            () => _client.Chat(request,
                deadline: DateTime.UtcNow.AddMinutes(Options.DeadlineMinutes),
                cancellationToken: ct),
            ct);
    }

    public IAsyncEnumerable<ChatEvent> ResumeStreamAsync(string threadId, string resumeJson,
        ChatMode mode, CancellationToken ct = default)
    {
        var request = new Proto.ResumeChatRequest
        {
            ThreadId = string.IsNullOrWhiteSpace(threadId) ? ThreadId : threadId,
            ResumeJson = resumeJson ?? "",
            UserId = Options.UserId,
            Username = Options.Username,
            ContractVersion = ContractVersion.Value,
            Mode = mode == ChatMode.Task ? Proto.ChatMode.Task : Proto.ChatMode.Chat,
        };

        return StreamCore(
            () => _client.ResumeChat(request,
                deadline: DateTime.UtcNow.AddMinutes(Options.DeadlineMinutes),
                cancellationToken: ct),
            ct);
    }

    public void ResetThread(string? threadId = null)
        => ThreadId = string.IsNullOrWhiteSpace(threadId) ? Guid.NewGuid().ToString("N") : threadId;

    private async IAsyncEnumerable<ChatEvent> StreamCore(
        Func<AsyncServerStreamingCall<Proto.ChatChunk>> start,
        [EnumeratorCancellation] CancellationToken ct)
    {
        AsyncServerStreamingCall<Proto.ChatChunk>? call = null;
        ChatEvent? startError = null;
        try
        {
            call = start();
            SetAvailable(true);
        }
        catch (RpcException error)
        {
            SetAvailable(false);
            startError = ToError(error);
        }

        if (call is null)
        {
            yield return startError ?? new ErrorEvent("CHAT_START_FAILED", "无法发起 Chat 流式调用。", "", false);
            yield break;
        }

        using (call)
        {
            while (true)
            {
                ChatEvent? item = null;
                ChatEvent? streamError = null;
                try
                {
                    if (!await call.ResponseStream.MoveNext(ct).ConfigureAwait(false)) break;
                    item = Convert(call.ResponseStream.Current);
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (RpcException error)
                {
                    if (error.StatusCode == StatusCode.Cancelled)
                        throw new OperationCanceledException(error.Message, error, ct);
                    SetAvailable(false);
                    streamError = ToError(error);
                }

                if (streamError is not null)
                {
                    yield return streamError;
                    yield break;
                }
                if (item is not null) yield return item;
            }
        }
    }

    private static ChatEvent? Convert(Proto.ChatChunk chunk) => chunk.PayloadCase switch
    {
        Proto.ChatChunk.PayloadOneofCase.Chunk => new TextDeltaEvent(chunk.Chunk),
        Proto.ChatChunk.PayloadOneofCase.Message => new MessageEvent(
            chunk.Message.MessageId, chunk.Message.Role.ToString(), chunk.Message.Content,
            chunk.Message.ToolCallId, chunk.Message.Name, chunk.Message.CreatedAt),
        Proto.ChatChunk.PayloadOneofCase.ToolCall => new ChatToolCallEvent(
            chunk.ToolCall.CallId, chunk.ToolCall.RunId, chunk.ToolCall.ToolName,
            chunk.ToolCall.ArgumentsJson, chunk.ToolCall.ThreadId, chunk.ToolCall.IsPartial),
        Proto.ChatChunk.PayloadOneofCase.ToolResult => new ChatToolResultEvent(
            chunk.ToolResult.CallId, chunk.ToolResult.ToolName, chunk.ToolResult.ResultJson,
            chunk.ToolResult.ErrorJson, (int)chunk.ToolResult.Status, chunk.ToolResult.ThreadId),
        Proto.ChatChunk.PayloadOneofCase.Trajectory => new TrajectoryChatEvent(
            chunk.Trajectory.EventId, chunk.Trajectory.RunId, chunk.Trajectory.ThreadId,
            chunk.Trajectory.ParentId, chunk.Trajectory.Type, chunk.Trajectory.Name,
            chunk.Trajectory.Status, chunk.Trajectory.ArgsJson, chunk.Trajectory.ResultJson,
            chunk.Trajectory.StartedAt, chunk.Trajectory.EndedAt, chunk.Trajectory.MetadataJson),
        Proto.ChatChunk.PayloadOneofCase.Done => new DoneEvent(
            chunk.Done.Msg, chunk.Done.ThreadId, chunk.Done.FinishReason,
            chunk.Done.UsageJson, (int)chunk.Done.Status),
        Proto.ChatChunk.PayloadOneofCase.Error => new ErrorEvent(
            chunk.Error.Code, chunk.Error.Message, chunk.Error.Hint, chunk.Error.Retryable),
        Proto.ChatChunk.PayloadOneofCase.Reasoning => new ReasoningEvent(
            chunk.Reasoning.Delta, chunk.Reasoning.RunId, chunk.Reasoning.StepId),
        Proto.ChatChunk.PayloadOneofCase.Hitl => new HitlEvent(
            chunk.Hitl.HitlId, chunk.Hitl.ThreadId, chunk.Hitl.Kind, chunk.Hitl.Title,
            chunk.Hitl.Description,
            chunk.Hitl.Fields.Select(item => new HitlField(item.Name, item.Label, item.Type,
                item.Required, item.DefaultJson, item.Placeholder, item.Description,
                item.Options.Select(option => new HitlOption(option.Value,
                    string.IsNullOrEmpty(option.Label) ? option.Value : option.Label,
                    option.Description, option.Recommended)).ToArray())).ToArray(),
            chunk.Hitl.Options.Select(option => new HitlOption(option.Value,
                string.IsNullOrEmpty(option.Label) ? option.Value : option.Label,
                option.Description, option.Recommended)).ToArray(),
            chunk.Hitl.AllowMultiple, chunk.Hitl.AllowFreeText, chunk.Hitl.SubmitLabel,
            chunk.Hitl.CancelLabel, chunk.Hitl.ResumeSchemaJson, chunk.Hitl.MetadataJson),
        Proto.ChatChunk.PayloadOneofCase.Plan => new PlanEvent(
            chunk.Plan.PlanId, chunk.Plan.RunId, chunk.Plan.ThreadId, chunk.Plan.Version,
            chunk.Plan.Steps.Select(step => new PlanStep(step.StepId, step.Domain, step.Goal,
                step.DependsOn.ToArray(), step.ExpectedTools.ToArray(), step.Acceptance,
                step.IsVisualCheck, step.Status, step.Error)).ToArray(),
            chunk.Plan.ReplanReason, chunk.Plan.Status, chunk.Plan.CreatedAt),
        _ => null,
    };

    private static ErrorEvent ToError(RpcException error) => new(
        error.StatusCode.ToString(), error.Status.Detail,
        "确认 Python 后端已启动（main.py --enable-grpc），端口 50051 可达。",
        error.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded);

    private void SetAvailable(bool value)
    {
        if (IsAvailable == value) return;
        IsAvailable = value;
        AvailabilityChanged?.Invoke(this, value);
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        if (_ownsChannel) _channel.Dispose();
    }
}

public enum ChatMode { Chat, Task }
```

> `ToolCallEvent.arguments_json` 在 `is_partial=true` 时是增量片段，必须按 `call_id`
> 累积后再反序列化。SDK 提供 `ToolCallAccumulator`（`Append/Release/Clear`）即可，
> 实现与旧 `HarnessBridge` 相同。

### 7.3 其余客户端

| 客户端 | 关键方法 | 说明 |
| :-- | :-- | :-- |
| `HarnessSessionClient` | `ListSessionsAsync` / `GetHistoryAsync` / `CreateAsync` / `RenameAsync` / `SetPinnedAsync` / `DeleteAsync` | 会话索引与历史 |
| `HarnessTrajectoryClient` | `GetAsync(userId, threadId)` | 按 run 聚合 |
| `HarnessAdminClient` | `GetCapabilitiesAsync` / `GetModelSettingsAsync` / `UpdateModelSettingsAsync` / `ListSkillsAsync` / `ListSubagentsAsync` / `ListMcpServersAsync` / `ListContextFormatsAsync` | 能力发现与配置 |
| `HarnessFileClient` | `ListAsync` / `ReadAsync` / `WriteAsync` | 会话工作区文件 |

统一构造方式：

```csharp
using Proto = Agent.Proto;

public sealed class HarnessClients : IDisposable
{
    private readonly GrpcChannel _channel;

    public HarnessClients(string address = "http://127.0.0.1:50051")
    {
        _channel = GrpcChannel.ForAddress(address);
        Sessions = new Proto.SessionService.SessionServiceClient(_channel);
        Trajectory = new Proto.TrajectoryService.TrajectoryServiceClient(_channel);
        Harness = new Proto.HarnessService.HarnessServiceClient(_channel);
        Files = new Proto.FileService.FileServiceClient(_channel);
    }

    public Proto.SessionService.SessionServiceClient Sessions { get; }
    public Proto.TrajectoryService.TrajectoryServiceClient Trajectory { get; }
    public Proto.HarnessService.HarnessServiceClient Harness { get; }
    public Proto.FileService.FileServiceClient Files { get; }

    public void Dispose() => _channel.Dispose();
}
```

> 建议进程内共享一个 `GrpcChannel`；`HarnessChatClient` 允许传入外部 channel
> （`channel` 参数），避免每个客户端各开一条 HTTP/2 连接。

---

## 8. 形态 C：HTTP 远程插件（可选）

宿主 `HttpTransport` 每次请求一次 `POST`，无 session、无 `host.*`、非 2xx 视为失败。
SDK 复用 `PluginDispatcher`，只需一个 Kestrel 端点：

```csharp
using System.Text.Json;
using System.Text.Json.Nodes;
using Harness.Plugin.Sdk.Hosting;
using Harness.Plugin.Sdk.Protocol;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;

public static class HttpPluginHost
{
    public static WebApplication Build(PluginDispatcher dispatcher, string path = "/rpc",
        string[]? args = null)
    {
        var builder = WebApplication.CreateSlimBuilder(args ?? []);
        var app = builder.Build();

        app.MapPost(path, async (HttpContext context) =>
        {
            JsonElement root;
            try
            {
                using var document = await JsonDocument.ParseAsync(
                    context.Request.Body, cancellationToken: context.RequestAborted);
                root = document.RootElement.Clone();
            }
            catch (JsonException)
            {
                return Results.StatusCode(StatusCodes.Status400BadRequest);
            }

            if (!root.TryGetProperty("method", out var methodElement)
                || !root.TryGetProperty("id", out var idElement))
                return Results.StatusCode(StatusCodes.Status400BadRequest);

            var parameters = root.TryGetProperty("params", out var raw) ? raw : default;
            var outcome = await dispatcher.DispatchAsync(
                methodElement.GetString()!, parameters, context.RequestAborted);

            var envelope = new JsonObject
            {
                ["jsonrpc"] = "2.0",
                ["id"] = JsonNode.Parse(idElement.GetRawText()),
            };
            if (outcome.Error is { } error)
            {
                var payload = new JsonObject { ["code"] = error.Code, ["message"] = error.Message };
                if (error.Data is not null) payload["data"] = error.Data;
                envelope["error"] = payload;
            }
            else
            {
                envelope["result"] = outcome.Result ?? new JsonObject();
            }

            return Results.Text(envelope.ToJsonString(JsonDefaults.Options), "application/json");
        });

        return app;
    }
}
```

manifest：

```json
{
  "runtime": { "kind": "remote", "language": "dotnet" },
  "transport": { "kind": "http", "url": "http://127.0.0.1:5731/rpc" }
}
```

该形态没有 `host.storage/secret/log/progress`，`HostClient` 会本地抛 `-32003`。

---

## 9. 附录：Python 对端（通用层设计）

### 9.1 目录

```text
src/
  grpc_contract/            # 协议壳，通用
    proto/                  # 与 SDK 的 proto 同步（proto-sync）
    generated/              # *_pb2 / *_pb2_grpc
    server.py               # 注册 5 个 servicer + h2c 监听
  grpc_runtime/             # 通用适配接口
    adapters.py             # ChatAdapter/SessionAdapter/... 协议
    tool_bridge.py          # ToolService 客户端 → LangChain StructuredTool
  grpc_common_harness/      # CommonProject 特化实现
    chat.py                 # 复用 api/chat.py 的事件抽取
    session.py              # 复用会话存储
    trajectory.py           # 复用 agent/middlewares/trajectory.py
    capabilities.py         # 模型/技能/子 Agent/MCP
    files.py                # thread state files
```

### 9.2 适配接口（保持通用）

```python
from typing import AsyncIterator, Protocol

import Agent_pb2 as pb


class ChatAdapter(Protocol):
    def stream(self, request: pb.ChatRequest) -> AsyncIterator[pb.ChatChunk]: ...
    def resume(self, request: pb.ResumeChatRequest) -> AsyncIterator[pb.ChatChunk]: ...


class SessionAdapter(Protocol):
    async def list_sessions(self, request: pb.ListSessionsRequest) -> pb.ListSessionsResponse: ...
    async def get_history(self, request: pb.GetSessionHistoryRequest) -> pb.GetSessionHistoryResponse: ...
    # create / rename / pin / delete 同理由宿主实现


class TrajectoryAdapter(Protocol):
    async def get(self, request: pb.GetTrajectoryRequest) -> pb.GetTrajectoryResponse: ...


class CapabilityAdapter(Protocol):
    async def capabilities(self, request: pb.GetCapabilitiesRequest) -> pb.GetCapabilitiesResponse: ...
    # 其余 List*/Get*/Update* 按需实现


class FileAdapter(Protocol):
    async def list_files(self, request: pb.ListThreadFilesRequest) -> pb.ListThreadFilesResponse: ...
    async def read_file(self, request: pb.ReadThreadFileRequest) -> pb.ReadThreadFileResponse: ...
    async def write_file(self, request: pb.WriteThreadFileRequest) -> pb.WriteThreadFileResponse: ...
```

`tool_bridge.py` 与现有 `tool_client.py` 等价：`ListTools` → `StructuredTool.from_function`，
`args_schema` 直接用 `args_schema_json`；`NEEDS_APPROVAL` 自动转 `ResolveApproval`。

### 9.3 CommonProject 映射

| 契约能力 | 现有实现 | 改造点 |
| :-- | :-- | :-- |
| 文本/工具/推理/中断事件流 | `api/chat.py` 的 `_get_stream_message` / `_get_tool_chunks` / `_get_reasoning_text` / `_get_stream_interrupts` | 转成 `ChatChunk` 分支 |
| 轨迹 | `agent/middlewares/trajectory.py`（已有 `run_id/parent_id`） | 映射 `TrajectoryEvent`，或先返回空 |
| 会话 | 现有会话存储 | 实现 `SessionAdapter` |
| 能力发现 | 模型配置 + 技能/子 Agent/MCP 加载器 | 聚合为 `HarnessService` |
| C# 工具 | 无（gRPC 新增） | `tool_bridge.py` 注册进 Agent 工具列表 |
| 计划/Plan | 取决于 `PLANNER_ENABLED` | 未开启时不下发 `plan` 分支 |

结论：协议壳与适配接口通用；特化只发生在 `grpc_common_harness/`。

---

## 10. 测试

### 10.1 形态 A：假总线

```csharp
using System.Text;
using System.Threading.Channels;
using Harness.Plugin.Sdk.Hosting;
using Harness.Plugin.Sdk.Protocol;

internal sealed class ChannelTextReader(ChannelReader<string> reader) : TextReader
{
    public override async ValueTask<string?> ReadLineAsync(CancellationToken ct)
    {
        try { return await reader.ReadAsync(ct); }
        catch (ChannelClosedException) { return null; }
    }
}

internal sealed class ChannelTextWriter(ChannelWriter<string> writer) : TextWriter
{
    public override Encoding Encoding => Encoding.UTF8;
    public override Task WriteLineAsync(ReadOnlyMemory<char> buffer, CancellationToken ct = default)
        => writer.WriteAsync(new string(buffer.Span), ct).AsTask();
    public override Task WriteLineAsync(string? value)
        => writer.WriteAsync(value ?? "", CancellationToken.None).AsTask();
}

internal sealed class FakeBus : IAsyncDisposable
{
    private readonly Channel<string> _toPlugin = Channel.CreateUnbounded<string>();
    private readonly Channel<string> _fromPlugin = Channel.CreateUnbounded<string>();
    private readonly RpcPeer _peer;

    public FakeBus(PluginDispatcher dispatcher)
    {
        _peer = new RpcPeer(
            new ChannelTextReader(_toPlugin.Reader),
            new ChannelTextWriter(_fromPlugin.Writer),
            (method, parameters, ct) => dispatcher.DispatchAsync(method, parameters, ct));
        dispatcher.AttachPeer(_peer);
        _ = _peer.RunAsync(CancellationToken.None);
    }

    public Task SendAsync(string line, CancellationToken ct = default)
        => _toPlugin.Writer.WriteAsync(line, ct).AsTask();

    public Task<string> ReadAsync(CancellationToken ct = default)
        => _fromPlugin.Reader.ReadAsync(ct).AsTask();

    public async ValueTask DisposeAsync()
    {
        _toPlugin.Writer.TryComplete();
        await _peer.DisposeAsync();
    }
}
```

用例：`initialize → describe → tool.call` 三段往返；未知工具 `-32601`（未初始化则 `-32002`）；
业务异常 `-32010`；通知不产生响应行；stdin EOF 正常返回。

### 10.2 形态 B：真实 gRPC 往返

用 `Port=0` 启动真实 Kestrel，避免固定端口冲突。

```csharp
using Grpc.Net.Client;
using Harness.Plugin.Sdk.Grpc.Server;
using Harness.Plugin.Sdk.Metadata;
using Proto = Agent.Proto;
using Xunit;

public sealed class ToolServiceTests
{
    [Fact]
    public async Task ListTools_and_CallTool_roundtrip()
    {
        var registry = new HarnessToolRegistry();
        registry.Register(new EchoTools());

        await using var server = new HarnessToolServer(registry, new ToolApprovalBroker(),
            new ToolServiceOptions { Port = 0 });
        await server.StartAsync();

        using var channel = GrpcChannel.ForAddress($"http://127.0.0.1:{server.Port}");
        var client = new Proto.ToolService.ToolServiceClient(channel);

        var list = await client.ListToolsAsync(new Proto.ListToolsRequest
        {
            ContractVersion = ContractVersion.Value,
        });
        Assert.Contains(list.Skills.SelectMany(item => item.Tools),
            item => item.Name == "echo");

        var result = await client.CallToolAsync(new Proto.ToolCallRequest
        {
            ToolName = "echo",
            ArgumentsJson = """{"text":"你好"}""",
            CallId = Guid.NewGuid().ToString("N"),
            ContractVersion = ContractVersion.Value,
        });
        Assert.Equal(Proto.ToolStatus.Ok, result.Status);
        Assert.True(result.Success);
        Assert.Contains("你好", result.ResultJson);
    }

    [Fact]
    public async Task Dangerous_tool_requires_approval()
    {
        var registry = new HarnessToolRegistry();
        registry.Register(new SceneTools());
        var broker = new ToolApprovalBroker { DefaultDecision = Proto.ApprovalDecision.Reject };

        await using var server = new HarnessToolServer(registry, broker,
            new ToolServiceOptions { Port = 0 });
        await server.StartAsync();
        using var channel = GrpcChannel.ForAddress($"http://127.0.0.1:{server.Port}");
        var client = new Proto.ToolService.ToolServiceClient(channel);

        var pending = await client.CallToolAsync(new Proto.ToolCallRequest
        {
            ToolName = "scene_clear",
            ArgumentsJson = "{}",
            CallId = Guid.NewGuid().ToString("N"),
            ContractVersion = ContractVersion.Value,
        });
        Assert.Equal(Proto.ToolStatus.NeedsApproval, pending.Status);

        var denied = await client.ResolveApprovalAsync(new Proto.ResolveApprovalRequest
        {
            ApprovalId = pending.ApprovalId,
            Decision = Proto.ApprovalDecision.Unspecified, // 交给 broker
            CallId = pending.CallId,
            ContractVersion = ContractVersion.Value,
        });
        Assert.Equal(Proto.ToolStatus.Denied, denied.Status);
    }
}
```

危险工具样例：

```csharp
public sealed class SceneTools
{
    [HarnessTool("scene_clear", Description = "清空场景", SandboxLevel = SandboxLevel.AlwaysConfirm,
        IsWrite = true, IsDestructive = true, SupportsEditArgs = false)]
    public string Clear() => "cleared";
}
```

### 10.3 契约版本一致性

```csharp
[Fact]
public void Contract_version_matches_proto_file()
{
    var path = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "proto", "contract.version");
    var text = File.ReadAllText(path).Trim();
    Assert.Equal(ContractVersion.Value, text);
}
```

CI 再跑一次 `Sdk.Tools validate --proto-dir proto` 比对 `contract.sha256`。

---

## 11. 发布与契约同步

### 11.1 打包

```bash
dotnet pack src/Harness.Plugin.Sdk/Harness.Plugin.Sdk.csproj -c Release -o dist/nuget
dotnet pack src/Harness.Plugin.Sdk.Grpc/Harness.Plugin.Sdk.Grpc.csproj -c Release -o dist/nuget
dotnet nuget add source ./dist/nuget --name harness-local
```

外部开发者：

```bash
dotnet add package Harness.Plugin.Sdk.Grpc --source ./dist/nuget
dotnet add package Grpc.Tools --version 2.62.0
```

### 11.2 proto 同步

```bash
# C# → Python：把 SDK 的 proto 同步到宿主仓库
dotnet run --project src/Harness.Plugin.Sdk.Tools -- proto-sync \
  --from proto --to ../CommonProject/src/grpc_contract/proto

# 生成 Python 桩
cd ../CommonProject/src/grpc_contract/proto
python -m grpc_tools.protoc -I. \
  --python_out=../generated --pyi_out=../generated --grpc_python_out=../generated \
  Agent.proto Session.proto Trajectory.proto Harness.proto File.proto

# 校验
dotnet run --project src/Harness.Plugin.Sdk.Tools -- validate --proto-dir proto
```

规则：

- 字段号永不复用；删除字段写 `reserved`；新增流式分支只追加 `oneof` 编号。
- `contract.version` 与 `ContractVersion.Value` 必须一致（单测 + CI）。
- 生成产物提交进仓库，保证无 `protoc` 环境也能构建。

### 11.3 形态 A 插件打包

```bash
dotnet publish samples/EchoPlugin/CSharpEchoPlugin.csproj -c Release -o dist/csharp-echo \
  --no-self-contained /p:PublishSingleFile=false
Compress-Archive -Path dist/csharp-echo/* -DestinationPath dist/csharp-echo-1.0.0.zip
```

`manifest.json` 必须在 ZIP 第一层，文件名与 `runtime.args` 一致。

---

## 12. 坑位清单

| 坑 | 现象 | 处理 |
| :-- | :-- | :-- |
| 先 `Console.SetOut` 再取 stdout | 形态 A 握手超时 | 按 `PluginHost.RunAsync` 顺序 |
| 读循环里 `await` 业务 | `host.*` 必超时 | 入站请求投递独立任务 |
| `Console.WriteLine` 调试残留 | 宿主日志「非法 JSON」 | 统一 `context.Log.*` |
| 把 DTO 参数写成 `JsonElement` | Schema 退化成 object | 用 DTO + `[Description]` |
| 工具名非 snake_case | 注册抛异常 | 显式 Name 校验；未填自动转换 |
| 同 `call_id` 重放不同参数 | `CALL_ID_CONFLICT` | 每次调用生成新 id |
| 把 `NEEDS_APPROVAL` 当失败 | 模型重复调用 | 它是流程中间态，必须走 `ResolveApproval` |
| 审批后复用 `call_id` | 命中幂等缓存 | 审批用 `approval_id`，不重发 `CallTool` |
| 工具不响应 `CancellationToken` | `TIMEOUT` 后线程仍占用 | 长任务拆分 + 检查取消 |
| WPF 工具直接改控件 | `InvalidOperationException` | 封回 `Application.Current.Dispatcher` |
| Kestrel 配了 HTTPS 重定向 | 客户端 h2c 连不上 | 只监听 `Http2`，不配 TLS |
| 客户端用 `https://` 连 h2c | `Unavailable` | 统一 `http://` |
| 端口被占用 | 启动抛 `IOException` | `Port=0` 自动分配并回报 |
| 目标机缺 ASP.NET Core Runtime | Kestrel 起不来 | README 写明运行时要求 |
| proto 两份各改一半 | 字段错位/默认值 | proto 单源 + `validate` 门禁 |
| `Grpc.Tools` 没加 `PrivateAssets` | 消费者传递构建依赖 | 固定写法 |
| 插件包含自包含产物 | 超过 32 MB 被拒 | 框架依赖发布 |
| 只在 Windows 发布形态 A | Linux 沙箱验证失败 | `dotnet Plugin.dll` 跨平台 |
| `dotnet` 首启提示写 stdout | 非 JSON 行告警 | 宿主容忍；部署前预热一次 |

---

## 13. 实施顺序

1. `Harness.Plugin.Sdk`：特性、`ToolResult`、Schema、绑定、注册表（两种形态共用）。
2. 形态 A：`RpcPeer` → `PluginHost` → `EchoPlugin` → 宿主冒烟脚本。
3. `Harness.Plugin.Sdk.Grpc`：proto 入包 + props 注入 + `ToolDescriptorFactory`。
4. `ToolGrpcService`（ListTools / CallTool / 审批 / 幂等 / 轨迹）+ 单测。
5. `HarnessToolServer`（Kestrel h2c，端口 0）+ 真实往返测试。
6. `HarnessChatClient` 与其余客户端；WPF 样例接入。
7. `Sdk.Tools`：`pack` / `validate` / `proto-sync`。
8. 迁移现有 `HarnessBridge`，保留 `[Tool]` 别名一个版本周期。
9. Python 对端：先 `ToolService` 桥（最小闭环），再补 Chat/Session/Trajectory。

