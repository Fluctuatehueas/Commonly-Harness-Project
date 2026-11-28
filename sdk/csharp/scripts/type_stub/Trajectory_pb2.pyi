import Agent_pb2 as _Agent_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TrajectoryRun(_message.Message):
    __slots__ = ("run_id", "started_at", "ended_at", "status", "events", "summary_json")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    ENDED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_JSON_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    started_at: str
    ended_at: str
    status: str
    events: _containers.RepeatedCompositeFieldContainer[_Agent_pb2.TrajectoryEvent]
    summary_json: str
    def __init__(self, run_id: _Optional[str] = ..., started_at: _Optional[str] = ..., ended_at: _Optional[str] = ..., status: _Optional[str] = ..., events: _Optional[_Iterable[_Union[_Agent_pb2.TrajectoryEvent, _Mapping]]] = ..., summary_json: _Optional[str] = ...) -> None: ...

class GetTrajectoryRequest(_message.Message):
    __slots__ = ("user_id", "thread_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ...) -> None: ...

class GetTrajectoryResponse(_message.Message):
    __slots__ = ("thread_id", "runs", "latest_run_id", "event_count", "generated_at")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    RUNS_FIELD_NUMBER: _ClassVar[int]
    LATEST_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_COUNT_FIELD_NUMBER: _ClassVar[int]
    GENERATED_AT_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    runs: _containers.RepeatedCompositeFieldContainer[TrajectoryRun]
    latest_run_id: str
    event_count: int
    generated_at: str
    def __init__(self, thread_id: _Optional[str] = ..., runs: _Optional[_Iterable[_Union[TrajectoryRun, _Mapping]]] = ..., latest_run_id: _Optional[str] = ..., event_count: _Optional[int] = ..., generated_at: _Optional[str] = ...) -> None: ...
