from nvidia_ace.emotion_with_timecode import v1_pb2 as _v1_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EmotionAggregate(_message.Message):
    __slots__ = ("input_emotions", "a2e_output", "a2f_smoothed_output")
    INPUT_EMOTIONS_FIELD_NUMBER: _ClassVar[int]
    A2E_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    A2F_SMOOTHED_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    input_emotions: _containers.RepeatedCompositeFieldContainer[_v1_pb2.EmotionWithTimeCode]
    a2e_output: _containers.RepeatedCompositeFieldContainer[_v1_pb2.EmotionWithTimeCode]
    a2f_smoothed_output: _containers.RepeatedCompositeFieldContainer[_v1_pb2.EmotionWithTimeCode]
    def __init__(self, input_emotions: _Optional[_Iterable[_Union[_v1_pb2.EmotionWithTimeCode, _Mapping]]] = ..., a2e_output: _Optional[_Iterable[_Union[_v1_pb2.EmotionWithTimeCode, _Mapping]]] = ..., a2f_smoothed_output: _Optional[_Iterable[_Union[_v1_pb2.EmotionWithTimeCode, _Mapping]]] = ...) -> None: ...
