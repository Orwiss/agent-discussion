from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class AnimationIds(_message.Message):
    __slots__ = ("request_id", "stream_id", "target_object_id")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    STREAM_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    stream_id: str
    target_object_id: str
    def __init__(self, request_id: _Optional[str] = ..., stream_id: _Optional[str] = ..., target_object_id: _Optional[str] = ...) -> None: ...
