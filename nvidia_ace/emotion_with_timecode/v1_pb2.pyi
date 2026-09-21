from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class EmotionWithTimeCode(_message.Message):
    __slots__ = ("time_code", "emotion")
    class EmotionEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    EMOTION_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    emotion: _containers.ScalarMap[str, float]
    def __init__(self, time_code: _Optional[float] = ..., emotion: _Optional[_Mapping[str, float]] = ...) -> None: ...
