from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FileEntry(_message.Message):
    __slots__ = ("path", "size", "modified_at")
    PATH_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    MODIFIED_AT_FIELD_NUMBER: _ClassVar[int]
    path: str
    size: int
    modified_at: str
    def __init__(self, path: _Optional[str] = ..., size: _Optional[int] = ..., modified_at: _Optional[str] = ...) -> None: ...

class ListThreadFilesRequest(_message.Message):
    __slots__ = ("user_id", "thread_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ...) -> None: ...

class ListThreadFilesResponse(_message.Message):
    __slots__ = ("source", "files")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    FILES_FIELD_NUMBER: _ClassVar[int]
    source: str
    files: _containers.RepeatedCompositeFieldContainer[FileEntry]
    def __init__(self, source: _Optional[str] = ..., files: _Optional[_Iterable[_Union[FileEntry, _Mapping]]] = ...) -> None: ...

class ReadThreadFileRequest(_message.Message):
    __slots__ = ("user_id", "thread_id", "path")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    path: str
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., path: _Optional[str] = ...) -> None: ...

class ReadThreadFileResponse(_message.Message):
    __slots__ = ("path", "content", "encoding")
    PATH_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    path: str
    content: str
    encoding: str
    def __init__(self, path: _Optional[str] = ..., content: _Optional[str] = ..., encoding: _Optional[str] = ...) -> None: ...

class WriteThreadFileRequest(_message.Message):
    __slots__ = ("user_id", "thread_id", "path", "content")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    thread_id: str
    path: str
    content: str
    def __init__(self, user_id: _Optional[str] = ..., thread_id: _Optional[str] = ..., path: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class WriteThreadFileResponse(_message.Message):
    __slots__ = ("success",)
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    success: bool
    def __init__(self, success: _Optional[bool] = ...) -> None: ...
