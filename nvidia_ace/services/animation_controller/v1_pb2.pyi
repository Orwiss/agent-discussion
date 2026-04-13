from nvidia_ace.controller import v1_pb2 as _v1_pb2
from nvidia_ace.status import v1_pb2 as _v1_pb2_1
from nvidia_ace.animation_id import v1_pb2 as _v1_pb2_1_1
from nvidia_ace.animation_data import v1_pb2 as _v1_pb2_1_1_1
from nvidia_ace.a2f import v1_pb2 as _v1_pb2_1_1_1_1
from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AnimationGraphRequest(_message.Message):
    __slots__ = ("animation_ids", "animation_graph_variable_name", "animation_graph_variable_value")
    ANIMATION_IDS_FIELD_NUMBER: _ClassVar[int]
    ANIMATION_GRAPH_VARIABLE_NAME_FIELD_NUMBER: _ClassVar[int]
    ANIMATION_GRAPH_VARIABLE_VALUE_FIELD_NUMBER: _ClassVar[int]
    animation_ids: _v1_pb2_1_1.AnimationIds
    animation_graph_variable_name: str
    animation_graph_variable_value: str
    def __init__(self, animation_ids: _Optional[_Union[_v1_pb2_1_1.AnimationIds, _Mapping]] = ..., animation_graph_variable_name: _Optional[str] = ..., animation_graph_variable_value: _Optional[str] = ...) -> None: ...

class AnimationIdsOrStatus(_message.Message):
    __slots__ = ("animation_ids", "status")
    ANIMATION_IDS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    animation_ids: _v1_pb2_1_1.AnimationIds
    status: _v1_pb2_1.Status
    def __init__(self, animation_ids: _Optional[_Union[_v1_pb2_1_1.AnimationIds, _Mapping]] = ..., status: _Optional[_Union[_v1_pb2_1.Status, _Mapping]] = ...) -> None: ...
