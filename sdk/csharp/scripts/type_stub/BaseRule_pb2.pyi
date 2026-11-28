from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ChatRequest(_message.Message):
    __slots__ = ("humanmsg", "thread_id")
    HUMANMSG_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    humanmsg: str
    thread_id: str
    def __init__(self, humanmsg: _Optional[str] = ..., thread_id: _Optional[str] = ...) -> None: ...

class ToolCallEvent(_message.Message):
    __slots__ = ("thread_id", "tool_name", "arguments_json")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    tool_name: str
    arguments_json: str
    def __init__(self, thread_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., arguments_json: _Optional[str] = ...) -> None: ...

class ToolResultEvent(_message.Message):
    __slots__ = ("thread_id", "tool_name", "result_json")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    tool_name: str
    result_json: str
    def __init__(self, thread_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., result_json: _Optional[str] = ...) -> None: ...

class DoneEvent(_message.Message):
    __slots__ = ("msg",)
    MSG_FIELD_NUMBER: _ClassVar[int]
    msg: str
    def __init__(self, msg: _Optional[str] = ...) -> None: ...

class ChatChunk(_message.Message):
    __slots__ = ("chunk", "tool_call", "tool_result", "done")
    CHUNK_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALL_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    chunk: str
    tool_call: ToolCallEvent
    tool_result: ToolResultEvent
    done: DoneEvent
    def __init__(self, chunk: _Optional[str] = ..., tool_call: _Optional[_Union[ToolCallEvent, _Mapping]] = ..., tool_result: _Optional[_Union[ToolResultEvent, _Mapping]] = ..., done: _Optional[_Union[DoneEvent, _Mapping]] = ...) -> None: ...

class ToolCallRequest(_message.Message):
    __slots__ = ("tool_name", "arguments_json")
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    tool_name: str
    arguments_json: str
    def __init__(self, tool_name: _Optional[str] = ..., arguments_json: _Optional[str] = ...) -> None: ...

class ToolCallResult(_message.Message):
    __slots__ = ("success", "result_json", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    result_json: str
    error: str
    def __init__(self, success: _Optional[bool] = ..., result_json: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...
