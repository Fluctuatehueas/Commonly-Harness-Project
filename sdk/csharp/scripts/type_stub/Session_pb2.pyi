import Agent_pb2 as _Agent_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SessionInfo(_message.Message):
    __slots__ = ("thread_id", "title", "created_at", "updated_at", "message_count", "pinned", "last_message_preview", "usage_json", "messages")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_COUNT_FIELD_NUMBER: _ClassVar[int]
    PINNED_FIELD_NUMBER: _ClassVar[int]
    LAST_MESSAGE_PREVIEW_FIELD_NUMBER: _ClassVar[int]
    USAGE_JSON_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    pinned: bool
    last_message_preview: str
    usage_json: str
    messages: _containers.RepeatedCompositeFieldContainer[_Agent_pb2.ChatMessage]
    def __init__(self, thread_id: _Optional[str] = ..., title: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ..., message_count: _Optional[int] = ..., pinned: _Optional[bool] = ..., last_message_preview: _Optional[str] = ..., usage_json: _Optional[str] = ..., messages: _Optional[_Iterable[_Union[_Agent_pb2.ChatMessage, _Mapping]]] = ...) -> None: ...

class ListSessionsRequest(_message.Message):
    __slots__ = ("user_id", "include_messages", "messages_limit_per_session", "include_usage")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_MESSAGES_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_LIMIT_PER_SESSION_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_USAGE_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    include_messages: bool
    messages_limit_per_session: int
    include_usage: bool
    def __init__(self, user_id: _Optional[str] = ..., include_messages: _Optional[bool] = ..., messages_limit_per_session: _Optional[int] = ..., include_usage: _Optional[bool] = ...) -> None: ...

class ListSessionsResponse(_message.Message):
    __slots__ = ("sessions",)
    SESSIONS_FIELD_NUMBER: _ClassVar[int]
    sessions: _containers.RepeatedCompositeFieldContainer[SessionInfo]
    def __init__(self, sessions: _Optional[_Iterable[_Union[SessionInfo, _Mapping]]] = ...) -> None: ...

class GetSessionHistoryRequest(_message.Message):
    __slots__ = ("user_id", "thread_id", "limit", "before_message_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    BEFORE_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    limit: int
    before_message_id: str
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., limit: _Optional[int] = ..., before_message_id: _Optional[str] = ...) -> None: ...

class GetSessionHistoryResponse(_message.Message):
    __slots__ = ("session", "messages", "interrupt_json", "session_usage_json")
    SESSION_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    INTERRUPT_JSON_FIELD_NUMBER: _ClassVar[int]
    SESSION_USAGE_JSON_FIELD_NUMBER: _ClassVar[int]
    session: SessionInfo
    messages: _containers.RepeatedCompositeFieldContainer[_Agent_pb2.ChatMessage]
    interrupt_json: str
    session_usage_json: str
    def __init__(self, session: _Optional[_Union[SessionInfo, _Mapping]] = ..., messages: _Optional[_Iterable[_Union[_Agent_pb2.ChatMessage, _Mapping]]] = ..., interrupt_json: _Optional[str] = ..., session_usage_json: _Optional[str] = ...) -> None: ...

class CreateSessionRequest(_message.Message):
    __slots__ = ("user_id", "title")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    title: str
    def __init__(self, user_id: _Optional[str] = ..., title: _Optional[str] = ...) -> None: ...

class RenameSessionRequest(_message.Message):
    __slots__ = ("user_id", "thread_id", "title")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    title: str
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., title: _Optional[str] = ...) -> None: ...

class SetSessionPinnedRequest(_message.Message):
    __slots__ = ("user_id", "thread_id", "pinned")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    PINNED_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    pinned: bool
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., pinned: _Optional[bool] = ...) -> None: ...

class DeleteSessionRequest(_message.Message):
    __slots__ = ("user_id", "thread_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ...) -> None: ...

class DeleteSessionResponse(_message.Message):
    __slots__ = ("success",)
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    success: bool
    def __init__(self, success: _Optional[bool] = ...) -> None: ...
