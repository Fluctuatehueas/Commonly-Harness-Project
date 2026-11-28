from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ChatMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHAT_MODE_UNSPECIFIED: _ClassVar[ChatMode]
    CHAT_MODE_CHAT: _ClassVar[ChatMode]
    CHAT_MODE_TASK: _ClassVar[ChatMode]

class MessageRole(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MESSAGE_ROLE_UNSPECIFIED: _ClassVar[MessageRole]
    MESSAGE_ROLE_SYSTEM: _ClassVar[MessageRole]
    MESSAGE_ROLE_HUMAN: _ClassVar[MessageRole]
    MESSAGE_ROLE_AI: _ClassVar[MessageRole]
    MESSAGE_ROLE_TOOL: _ClassVar[MessageRole]

class ChatStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHAT_STATUS_UNSPECIFIED: _ClassVar[ChatStatus]
    CHAT_STATUS_OK: _ClassVar[ChatStatus]
    CHAT_STATUS_INTERRUPTED: _ClassVar[ChatStatus]
    CHAT_STATUS_ERROR: _ClassVar[ChatStatus]
    CHAT_STATUS_CANCELLED: _ClassVar[ChatStatus]

class ToolStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TOOL_STATUS_UNSPECIFIED: _ClassVar[ToolStatus]
    TOOL_STATUS_OK: _ClassVar[ToolStatus]
    TOOL_STATUS_ERROR: _ClassVar[ToolStatus]
    TOOL_STATUS_DENIED: _ClassVar[ToolStatus]
    TOOL_STATUS_NEEDS_APPROVAL: _ClassVar[ToolStatus]
    TOOL_STATUS_CANCELLED: _ClassVar[ToolStatus]

class SandboxLevel(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SANDBOX_LEVEL_UNSPECIFIED: _ClassVar[SandboxLevel]
    SANDBOX_LEVEL_AUTO: _ClassVar[SandboxLevel]
    SANDBOX_LEVEL_CONFIRM: _ClassVar[SandboxLevel]
    SANDBOX_LEVEL_ALWAYS_CONFIRM: _ClassVar[SandboxLevel]

class ApprovalDecision(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    APPROVAL_DECISION_UNSPECIFIED: _ClassVar[ApprovalDecision]
    APPROVAL_DECISION_APPROVE: _ClassVar[ApprovalDecision]
    APPROVAL_DECISION_REJECT: _ClassVar[ApprovalDecision]
    APPROVAL_DECISION_EDIT: _ClassVar[ApprovalDecision]
CHAT_MODE_UNSPECIFIED: ChatMode
CHAT_MODE_CHAT: ChatMode
CHAT_MODE_TASK: ChatMode
MESSAGE_ROLE_UNSPECIFIED: MessageRole
MESSAGE_ROLE_SYSTEM: MessageRole
MESSAGE_ROLE_HUMAN: MessageRole
MESSAGE_ROLE_AI: MessageRole
MESSAGE_ROLE_TOOL: MessageRole
CHAT_STATUS_UNSPECIFIED: ChatStatus
CHAT_STATUS_OK: ChatStatus
CHAT_STATUS_INTERRUPTED: ChatStatus
CHAT_STATUS_ERROR: ChatStatus
CHAT_STATUS_CANCELLED: ChatStatus
TOOL_STATUS_UNSPECIFIED: ToolStatus
TOOL_STATUS_OK: ToolStatus
TOOL_STATUS_ERROR: ToolStatus
TOOL_STATUS_DENIED: ToolStatus
TOOL_STATUS_NEEDS_APPROVAL: ToolStatus
TOOL_STATUS_CANCELLED: ToolStatus
SANDBOX_LEVEL_UNSPECIFIED: SandboxLevel
SANDBOX_LEVEL_AUTO: SandboxLevel
SANDBOX_LEVEL_CONFIRM: SandboxLevel
SANDBOX_LEVEL_ALWAYS_CONFIRM: SandboxLevel
APPROVAL_DECISION_UNSPECIFIED: ApprovalDecision
APPROVAL_DECISION_APPROVE: ApprovalDecision
APPROVAL_DECISION_REJECT: ApprovalDecision
APPROVAL_DECISION_EDIT: ApprovalDecision

class ToolCall(_message.Message):
    __slots__ = ("call_id", "tool_name", "arguments_json")
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    call_id: str
    tool_name: str
    arguments_json: str
    def __init__(self, call_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., arguments_json: _Optional[str] = ...) -> None: ...

class ChatMessage(_message.Message):
    __slots__ = ("message_id", "role", "content", "tool_calls", "tool_call_id", "name", "additional_kwargs_json", "created_at")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALLS_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ADDITIONAL_KWARGS_JSON_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    role: MessageRole
    content: str
    tool_calls: _containers.RepeatedCompositeFieldContainer[ToolCall]
    tool_call_id: str
    name: str
    additional_kwargs_json: str
    created_at: str
    def __init__(self, message_id: _Optional[str] = ..., role: _Optional[_Union[MessageRole, str]] = ..., content: _Optional[str] = ..., tool_calls: _Optional[_Iterable[_Union[ToolCall, _Mapping]]] = ..., tool_call_id: _Optional[str] = ..., name: _Optional[str] = ..., additional_kwargs_json: _Optional[str] = ..., created_at: _Optional[str] = ...) -> None: ...

class ChatRequest(_message.Message):
    __slots__ = ("humanmsg", "thread_id", "mode", "task_json", "client_context_json", "history", "contract_version", "user_id", "username")
    HUMANMSG_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    TASK_JSON_FIELD_NUMBER: _ClassVar[int]
    CLIENT_CONTEXT_JSON_FIELD_NUMBER: _ClassVar[int]
    HISTORY_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    humanmsg: str
    thread_id: str
    mode: ChatMode
    task_json: str
    client_context_json: str
    history: _containers.RepeatedCompositeFieldContainer[ChatMessage]
    contract_version: str
    user_id: str
    username: str
    def __init__(self, humanmsg: _Optional[str] = ..., thread_id: _Optional[str] = ..., mode: _Optional[_Union[ChatMode, str]] = ..., task_json: _Optional[str] = ..., client_context_json: _Optional[str] = ..., history: _Optional[_Iterable[_Union[ChatMessage, _Mapping]]] = ..., contract_version: _Optional[str] = ..., user_id: _Optional[str] = ..., username: _Optional[str] = ...) -> None: ...

class ResumeChatRequest(_message.Message):
    __slots__ = ("thread_id", "resume_json", "user_id", "username", "contract_version", "mode")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RESUME_JSON_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    resume_json: str
    user_id: str
    username: str
    contract_version: str
    mode: ChatMode
    def __init__(self, thread_id: _Optional[str] = ..., resume_json: _Optional[str] = ..., user_id: _Optional[str] = ..., username: _Optional[str] = ..., contract_version: _Optional[str] = ..., mode: _Optional[_Union[ChatMode, str]] = ...) -> None: ...

class ToolCallEvent(_message.Message):
    __slots__ = ("thread_id", "tool_name", "arguments_json", "call_id", "run_id", "is_partial")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    IS_PARTIAL_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    tool_name: str
    arguments_json: str
    call_id: str
    run_id: str
    is_partial: bool
    def __init__(self, thread_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., arguments_json: _Optional[str] = ..., call_id: _Optional[str] = ..., run_id: _Optional[str] = ..., is_partial: _Optional[bool] = ...) -> None: ...

class ToolResultEvent(_message.Message):
    __slots__ = ("thread_id", "tool_name", "result_json", "call_id", "status", "error_json")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ERROR_JSON_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    tool_name: str
    result_json: str
    call_id: str
    status: ToolStatus
    error_json: str
    def __init__(self, thread_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., result_json: _Optional[str] = ..., call_id: _Optional[str] = ..., status: _Optional[_Union[ToolStatus, str]] = ..., error_json: _Optional[str] = ...) -> None: ...

class DoneEvent(_message.Message):
    __slots__ = ("msg", "thread_id", "finish_reason", "usage_json", "status")
    MSG_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    FINISH_REASON_FIELD_NUMBER: _ClassVar[int]
    USAGE_JSON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    msg: str
    thread_id: str
    finish_reason: str
    usage_json: str
    status: ChatStatus
    def __init__(self, msg: _Optional[str] = ..., thread_id: _Optional[str] = ..., finish_reason: _Optional[str] = ..., usage_json: _Optional[str] = ..., status: _Optional[_Union[ChatStatus, str]] = ...) -> None: ...

class ErrorEvent(_message.Message):
    __slots__ = ("code", "message", "hint", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    HINT_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    hint: str
    retryable: bool
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., hint: _Optional[str] = ..., retryable: _Optional[bool] = ...) -> None: ...

class ReasoningChunk(_message.Message):
    __slots__ = ("thread_id", "delta", "run_id", "step_id")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    DELTA_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STEP_ID_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    delta: str
    run_id: str
    step_id: str
    def __init__(self, thread_id: _Optional[str] = ..., delta: _Optional[str] = ..., run_id: _Optional[str] = ..., step_id: _Optional[str] = ...) -> None: ...

class PlanStepInfo(_message.Message):
    __slots__ = ("step_id", "domain", "goal", "depends_on", "expected_tools", "acceptance", "is_visual_check", "status", "error")
    STEP_ID_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    GOAL_FIELD_NUMBER: _ClassVar[int]
    DEPENDS_ON_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_TOOLS_FIELD_NUMBER: _ClassVar[int]
    ACCEPTANCE_FIELD_NUMBER: _ClassVar[int]
    IS_VISUAL_CHECK_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    step_id: str
    domain: str
    goal: str
    depends_on: _containers.RepeatedScalarFieldContainer[str]
    expected_tools: _containers.RepeatedScalarFieldContainer[str]
    acceptance: str
    is_visual_check: bool
    status: str
    error: str
    def __init__(self, step_id: _Optional[str] = ..., domain: _Optional[str] = ..., goal: _Optional[str] = ..., depends_on: _Optional[_Iterable[str]] = ..., expected_tools: _Optional[_Iterable[str]] = ..., acceptance: _Optional[str] = ..., is_visual_check: _Optional[bool] = ..., status: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class PlanEvent(_message.Message):
    __slots__ = ("plan_id", "thread_id", "run_id", "version", "steps", "replan_reason", "status", "created_at")
    PLAN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    STEPS_FIELD_NUMBER: _ClassVar[int]
    REPLAN_REASON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    plan_id: str
    thread_id: str
    run_id: str
    version: int
    steps: _containers.RepeatedCompositeFieldContainer[PlanStepInfo]
    replan_reason: str
    status: str
    created_at: str
    def __init__(self, plan_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., run_id: _Optional[str] = ..., version: _Optional[int] = ..., steps: _Optional[_Iterable[_Union[PlanStepInfo, _Mapping]]] = ..., replan_reason: _Optional[str] = ..., status: _Optional[str] = ..., created_at: _Optional[str] = ...) -> None: ...

class HitlOption(_message.Message):
    __slots__ = ("value", "label", "description", "recommended")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    RECOMMENDED_FIELD_NUMBER: _ClassVar[int]
    value: str
    label: str
    description: str
    recommended: bool
    def __init__(self, value: _Optional[str] = ..., label: _Optional[str] = ..., description: _Optional[str] = ..., recommended: _Optional[bool] = ...) -> None: ...

class HitlField(_message.Message):
    __slots__ = ("name", "label", "type", "required", "default_json", "placeholder", "description", "options")
    NAME_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_JSON_FIELD_NUMBER: _ClassVar[int]
    PLACEHOLDER_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    name: str
    label: str
    type: str
    required: bool
    default_json: str
    placeholder: str
    description: str
    options: _containers.RepeatedCompositeFieldContainer[HitlOption]
    def __init__(self, name: _Optional[str] = ..., label: _Optional[str] = ..., type: _Optional[str] = ..., required: _Optional[bool] = ..., default_json: _Optional[str] = ..., placeholder: _Optional[str] = ..., description: _Optional[str] = ..., options: _Optional[_Iterable[_Union[HitlOption, _Mapping]]] = ...) -> None: ...

class HitlEvent(_message.Message):
    __slots__ = ("hitl_id", "thread_id", "kind", "title", "description", "fields", "options", "allow_multiple", "allow_free_text", "submit_label", "cancel_label", "resume_schema_json", "metadata_json")
    HITL_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MULTIPLE_FIELD_NUMBER: _ClassVar[int]
    ALLOW_FREE_TEXT_FIELD_NUMBER: _ClassVar[int]
    SUBMIT_LABEL_FIELD_NUMBER: _ClassVar[int]
    CANCEL_LABEL_FIELD_NUMBER: _ClassVar[int]
    RESUME_SCHEMA_JSON_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    hitl_id: str
    thread_id: str
    kind: str
    title: str
    description: str
    fields: _containers.RepeatedCompositeFieldContainer[HitlField]
    options: _containers.RepeatedCompositeFieldContainer[HitlOption]
    allow_multiple: bool
    allow_free_text: bool
    submit_label: str
    cancel_label: str
    resume_schema_json: str
    metadata_json: str
    def __init__(self, hitl_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., kind: _Optional[str] = ..., title: _Optional[str] = ..., description: _Optional[str] = ..., fields: _Optional[_Iterable[_Union[HitlField, _Mapping]]] = ..., options: _Optional[_Iterable[_Union[HitlOption, _Mapping]]] = ..., allow_multiple: _Optional[bool] = ..., allow_free_text: _Optional[bool] = ..., submit_label: _Optional[str] = ..., cancel_label: _Optional[str] = ..., resume_schema_json: _Optional[str] = ..., metadata_json: _Optional[str] = ...) -> None: ...

class TrajectoryEvent(_message.Message):
    __slots__ = ("event_id", "run_id", "thread_id", "parent_id", "type", "name", "status", "args_json", "result_json", "started_at", "ended_at", "metadata_json")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ARGS_JSON_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    ENDED_AT_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    run_id: str
    thread_id: str
    parent_id: str
    type: str
    name: str
    status: str
    args_json: str
    result_json: str
    started_at: str
    ended_at: str
    metadata_json: str
    def __init__(self, event_id: _Optional[str] = ..., run_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., parent_id: _Optional[str] = ..., type: _Optional[str] = ..., name: _Optional[str] = ..., status: _Optional[str] = ..., args_json: _Optional[str] = ..., result_json: _Optional[str] = ..., started_at: _Optional[str] = ..., ended_at: _Optional[str] = ..., metadata_json: _Optional[str] = ...) -> None: ...

class ChatChunk(_message.Message):
    __slots__ = ("chunk", "tool_call", "tool_result", "done", "message", "trajectory", "error", "reasoning", "hitl", "plan")
    CHUNK_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALL_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TRAJECTORY_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    REASONING_FIELD_NUMBER: _ClassVar[int]
    HITL_FIELD_NUMBER: _ClassVar[int]
    PLAN_FIELD_NUMBER: _ClassVar[int]
    chunk: str
    tool_call: ToolCallEvent
    tool_result: ToolResultEvent
    done: DoneEvent
    message: ChatMessage
    trajectory: TrajectoryEvent
    error: ErrorEvent
    reasoning: ReasoningChunk
    hitl: HitlEvent
    plan: PlanEvent
    def __init__(self, chunk: _Optional[str] = ..., tool_call: _Optional[_Union[ToolCallEvent, _Mapping]] = ..., tool_result: _Optional[_Union[ToolResultEvent, _Mapping]] = ..., done: _Optional[_Union[DoneEvent, _Mapping]] = ..., message: _Optional[_Union[ChatMessage, _Mapping]] = ..., trajectory: _Optional[_Union[TrajectoryEvent, _Mapping]] = ..., error: _Optional[_Union[ErrorEvent, _Mapping]] = ..., reasoning: _Optional[_Union[ReasoningChunk, _Mapping]] = ..., hitl: _Optional[_Union[HitlEvent, _Mapping]] = ..., plan: _Optional[_Union[PlanEvent, _Mapping]] = ...) -> None: ...

class ToolDescriptor(_message.Message):
    __slots__ = ("name", "display_name", "description", "domain", "skill", "args_schema_json", "result_schema_json", "sandbox_level", "is_write", "is_destructive", "supports_edit_args", "timeout_ms", "version")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    SKILL_FIELD_NUMBER: _ClassVar[int]
    ARGS_SCHEMA_JSON_FIELD_NUMBER: _ClassVar[int]
    RESULT_SCHEMA_JSON_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_LEVEL_FIELD_NUMBER: _ClassVar[int]
    IS_WRITE_FIELD_NUMBER: _ClassVar[int]
    IS_DESTRUCTIVE_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_EDIT_ARGS_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    name: str
    display_name: str
    description: str
    domain: str
    skill: str
    args_schema_json: str
    result_schema_json: str
    sandbox_level: SandboxLevel
    is_write: bool
    is_destructive: bool
    supports_edit_args: bool
    timeout_ms: int
    version: str
    def __init__(self, name: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., domain: _Optional[str] = ..., skill: _Optional[str] = ..., args_schema_json: _Optional[str] = ..., result_schema_json: _Optional[str] = ..., sandbox_level: _Optional[_Union[SandboxLevel, str]] = ..., is_write: _Optional[bool] = ..., is_destructive: _Optional[bool] = ..., supports_edit_args: _Optional[bool] = ..., timeout_ms: _Optional[int] = ..., version: _Optional[str] = ...) -> None: ...

class SkillDefinition(_message.Message):
    __slots__ = ("name", "display_name", "description", "prompt_fragment", "tools", "metadata_json")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FRAGMENT_FIELD_NUMBER: _ClassVar[int]
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    name: str
    display_name: str
    description: str
    prompt_fragment: str
    tools: _containers.RepeatedCompositeFieldContainer[ToolDescriptor]
    metadata_json: str
    def __init__(self, name: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., prompt_fragment: _Optional[str] = ..., tools: _Optional[_Iterable[_Union[ToolDescriptor, _Mapping]]] = ..., metadata_json: _Optional[str] = ...) -> None: ...

class ListToolsRequest(_message.Message):
    __slots__ = ("contract_version", "thread_id")
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    contract_version: str
    thread_id: str
    def __init__(self, contract_version: _Optional[str] = ..., thread_id: _Optional[str] = ...) -> None: ...

class ListToolsResponse(_message.Message):
    __slots__ = ("contract_version", "skills")
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    SKILLS_FIELD_NUMBER: _ClassVar[int]
    contract_version: str
    skills: _containers.RepeatedCompositeFieldContainer[SkillDefinition]
    def __init__(self, contract_version: _Optional[str] = ..., skills: _Optional[_Iterable[_Union[SkillDefinition, _Mapping]]] = ...) -> None: ...

class ToolError(_message.Message):
    __slots__ = ("code", "message", "hint", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    HINT_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    hint: str
    retryable: bool
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., hint: _Optional[str] = ..., retryable: _Optional[bool] = ...) -> None: ...

class ToolCallRequest(_message.Message):
    __slots__ = ("tool_name", "arguments_json", "thread_id", "call_id", "run_id", "mode", "trace_id", "contract_version")
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    tool_name: str
    arguments_json: str
    thread_id: str
    call_id: str
    run_id: str
    mode: ChatMode
    trace_id: str
    contract_version: str
    def __init__(self, tool_name: _Optional[str] = ..., arguments_json: _Optional[str] = ..., thread_id: _Optional[str] = ..., call_id: _Optional[str] = ..., run_id: _Optional[str] = ..., mode: _Optional[_Union[ChatMode, str]] = ..., trace_id: _Optional[str] = ..., contract_version: _Optional[str] = ...) -> None: ...

class ToolCallResult(_message.Message):
    __slots__ = ("success", "result_json", "error", "status", "approval_id", "call_id", "trajectory", "duration_ms", "detail")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    APPROVAL_ID_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TRAJECTORY_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    success: bool
    result_json: str
    error: str
    status: ToolStatus
    approval_id: str
    call_id: str
    trajectory: _containers.RepeatedCompositeFieldContainer[TrajectoryEvent]
    duration_ms: int
    detail: ToolError
    def __init__(self, success: _Optional[bool] = ..., result_json: _Optional[str] = ..., error: _Optional[str] = ..., status: _Optional[_Union[ToolStatus, str]] = ..., approval_id: _Optional[str] = ..., call_id: _Optional[str] = ..., trajectory: _Optional[_Iterable[_Union[TrajectoryEvent, _Mapping]]] = ..., duration_ms: _Optional[int] = ..., detail: _Optional[_Union[ToolError, _Mapping]] = ...) -> None: ...

class ResolveApprovalRequest(_message.Message):
    __slots__ = ("approval_id", "decision", "edited_arguments_json", "thread_id", "call_id", "contract_version")
    APPROVAL_ID_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    EDITED_ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    approval_id: str
    decision: ApprovalDecision
    edited_arguments_json: str
    thread_id: str
    call_id: str
    contract_version: str
    def __init__(self, approval_id: _Optional[str] = ..., decision: _Optional[_Union[ApprovalDecision, str]] = ..., edited_arguments_json: _Optional[str] = ..., thread_id: _Optional[str] = ..., call_id: _Optional[str] = ..., contract_version: _Optional[str] = ...) -> None: ...
