from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Capability(_message.Message):
    __slots__ = ("name", "enabled", "detail")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    name: str
    enabled: bool
    detail: str
    def __init__(self, name: _Optional[str] = ..., enabled: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class GetCapabilitiesRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    def __init__(self, user_id: _Optional[str] = ...) -> None: ...

class GetCapabilitiesResponse(_message.Message):
    __slots__ = ("contract_version", "model_name", "model_thinking_enabled", "sandbox_enabled", "python_tool_count", "skill_count", "subagent_count", "mcp_server_count", "session_count", "capabilities")
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODEL_NAME_FIELD_NUMBER: _ClassVar[int]
    MODEL_THINKING_ENABLED_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ENABLED_FIELD_NUMBER: _ClassVar[int]
    PYTHON_TOOL_COUNT_FIELD_NUMBER: _ClassVar[int]
    SKILL_COUNT_FIELD_NUMBER: _ClassVar[int]
    SUBAGENT_COUNT_FIELD_NUMBER: _ClassVar[int]
    MCP_SERVER_COUNT_FIELD_NUMBER: _ClassVar[int]
    SESSION_COUNT_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    contract_version: str
    model_name: str
    model_thinking_enabled: bool
    sandbox_enabled: bool
    python_tool_count: int
    skill_count: int
    subagent_count: int
    mcp_server_count: int
    session_count: int
    capabilities: _containers.RepeatedCompositeFieldContainer[Capability]
    def __init__(self, contract_version: _Optional[str] = ..., model_name: _Optional[str] = ..., model_thinking_enabled: _Optional[bool] = ..., sandbox_enabled: _Optional[bool] = ..., python_tool_count: _Optional[int] = ..., skill_count: _Optional[int] = ..., subagent_count: _Optional[int] = ..., mcp_server_count: _Optional[int] = ..., session_count: _Optional[int] = ..., capabilities: _Optional[_Iterable[_Union[Capability, _Mapping]]] = ...) -> None: ...

class ModelSettings(_message.Message):
    __slots__ = ("model_name", "base_url", "has_api_key", "thinking_enabled")
    MODEL_NAME_FIELD_NUMBER: _ClassVar[int]
    BASE_URL_FIELD_NUMBER: _ClassVar[int]
    HAS_API_KEY_FIELD_NUMBER: _ClassVar[int]
    THINKING_ENABLED_FIELD_NUMBER: _ClassVar[int]
    model_name: str
    base_url: str
    has_api_key: bool
    thinking_enabled: bool
    def __init__(self, model_name: _Optional[str] = ..., base_url: _Optional[str] = ..., has_api_key: _Optional[bool] = ..., thinking_enabled: _Optional[bool] = ...) -> None: ...

class GetModelSettingsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class UpdateModelSettingsRequest(_message.Message):
    __slots__ = ("model_name", "base_url", "api_key", "thinking_enabled")
    MODEL_NAME_FIELD_NUMBER: _ClassVar[int]
    BASE_URL_FIELD_NUMBER: _ClassVar[int]
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    THINKING_ENABLED_FIELD_NUMBER: _ClassVar[int]
    model_name: str
    base_url: str
    api_key: str
    thinking_enabled: bool
    def __init__(self, model_name: _Optional[str] = ..., base_url: _Optional[str] = ..., api_key: _Optional[str] = ..., thinking_enabled: _Optional[bool] = ...) -> None: ...

class SkillSummary(_message.Message):
    __slots__ = ("name", "display_name", "description", "path", "metadata_json")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    name: str
    display_name: str
    description: str
    path: str
    metadata_json: str
    def __init__(self, name: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., path: _Optional[str] = ..., metadata_json: _Optional[str] = ...) -> None: ...

class ListSkillsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListSkillsResponse(_message.Message):
    __slots__ = ("skills",)
    SKILLS_FIELD_NUMBER: _ClassVar[int]
    skills: _containers.RepeatedCompositeFieldContainer[SkillSummary]
    def __init__(self, skills: _Optional[_Iterable[_Union[SkillSummary, _Mapping]]] = ...) -> None: ...

class GetSkillRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class GetSkillResponse(_message.Message):
    __slots__ = ("skill", "content")
    SKILL_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    skill: SkillSummary
    content: str
    def __init__(self, skill: _Optional[_Union[SkillSummary, _Mapping]] = ..., content: _Optional[str] = ...) -> None: ...

class SubagentSummary(_message.Message):
    __slots__ = ("name", "description", "path", "config_json", "tools", "optional_tools", "mcp_servers", "skills", "model")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    CONFIG_JSON_FIELD_NUMBER: _ClassVar[int]
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    OPTIONAL_TOOLS_FIELD_NUMBER: _ClassVar[int]
    MCP_SERVERS_FIELD_NUMBER: _ClassVar[int]
    SKILLS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    path: str
    config_json: str
    tools: _containers.RepeatedScalarFieldContainer[str]
    optional_tools: _containers.RepeatedScalarFieldContainer[str]
    mcp_servers: _containers.RepeatedScalarFieldContainer[str]
    skills: _containers.RepeatedScalarFieldContainer[str]
    model: str
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., path: _Optional[str] = ..., config_json: _Optional[str] = ..., tools: _Optional[_Iterable[str]] = ..., optional_tools: _Optional[_Iterable[str]] = ..., mcp_servers: _Optional[_Iterable[str]] = ..., skills: _Optional[_Iterable[str]] = ..., model: _Optional[str] = ...) -> None: ...

class ListSubagentsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListSubagentsResponse(_message.Message):
    __slots__ = ("subagents",)
    SUBAGENTS_FIELD_NUMBER: _ClassVar[int]
    subagents: _containers.RepeatedCompositeFieldContainer[SubagentSummary]
    def __init__(self, subagents: _Optional[_Iterable[_Union[SubagentSummary, _Mapping]]] = ...) -> None: ...

class McpServerSummary(_message.Message):
    __slots__ = ("name", "transport", "description", "config_json", "optional", "enabled", "used_by_subagents")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TRANSPORT_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    CONFIG_JSON_FIELD_NUMBER: _ClassVar[int]
    OPTIONAL_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    USED_BY_SUBAGENTS_FIELD_NUMBER: _ClassVar[int]
    name: str
    transport: str
    description: str
    config_json: str
    optional: bool
    enabled: bool
    used_by_subagents: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, name: _Optional[str] = ..., transport: _Optional[str] = ..., description: _Optional[str] = ..., config_json: _Optional[str] = ..., optional: _Optional[bool] = ..., enabled: _Optional[bool] = ..., used_by_subagents: _Optional[_Iterable[str]] = ...) -> None: ...

class ListMcpServersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListMcpServersResponse(_message.Message):
    __slots__ = ("servers",)
    SERVERS_FIELD_NUMBER: _ClassVar[int]
    servers: _containers.RepeatedCompositeFieldContainer[McpServerSummary]
    def __init__(self, servers: _Optional[_Iterable[_Union[McpServerSummary, _Mapping]]] = ...) -> None: ...

class ContextFormat(_message.Message):
    __slots__ = ("extension", "kind", "mime_type")
    EXTENSION_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    extension: str
    kind: str
    mime_type: str
    def __init__(self, extension: _Optional[str] = ..., kind: _Optional[str] = ..., mime_type: _Optional[str] = ...) -> None: ...

class ListContextFormatsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListContextFormatsResponse(_message.Message):
    __slots__ = ("formats", "max_file_mb", "max_files")
    FORMATS_FIELD_NUMBER: _ClassVar[int]
    MAX_FILE_MB_FIELD_NUMBER: _ClassVar[int]
    MAX_FILES_FIELD_NUMBER: _ClassVar[int]
    formats: _containers.RepeatedCompositeFieldContainer[ContextFormat]
    max_file_mb: int
    max_files: int
    def __init__(self, formats: _Optional[_Iterable[_Union[ContextFormat, _Mapping]]] = ..., max_file_mb: _Optional[int] = ..., max_files: _Optional[int] = ...) -> None: ...
