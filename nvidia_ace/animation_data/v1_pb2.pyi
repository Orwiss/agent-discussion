from nvidia_ace.animation_id import v1_pb2 as _v1_pb2
from nvidia_ace.audio import v1_pb2 as _v1_pb2_1
from nvidia_ace.status import v1_pb2 as _v1_pb2_1_1
from google.protobuf import any_pb2 as _any_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AnimationDataStreamHeader(_message.Message):
    __slots__ = ("animation_ids", "source_service_id", "audio_header", "skel_animation_header", "start_time_code_since_epoch")
    ANIMATION_IDS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_SERVICE_ID_FIELD_NUMBER: _ClassVar[int]
    AUDIO_HEADER_FIELD_NUMBER: _ClassVar[int]
    SKEL_ANIMATION_HEADER_FIELD_NUMBER: _ClassVar[int]
    START_TIME_CODE_SINCE_EPOCH_FIELD_NUMBER: _ClassVar[int]
    animation_ids: _v1_pb2.AnimationIds
    source_service_id: str
    audio_header: _v1_pb2_1.AudioHeader
    skel_animation_header: SkelAnimationHeader
    start_time_code_since_epoch: float
    def __init__(self, animation_ids: _Optional[_Union[_v1_pb2.AnimationIds, _Mapping]] = ..., source_service_id: _Optional[str] = ..., audio_header: _Optional[_Union[_v1_pb2_1.AudioHeader, _Mapping]] = ..., skel_animation_header: _Optional[_Union[SkelAnimationHeader, _Mapping]] = ..., start_time_code_since_epoch: _Optional[float] = ...) -> None: ...

class AnimationDataStream(_message.Message):
    __slots__ = ("animation_data_stream_header", "animation_data", "status")
    ANIMATION_DATA_STREAM_HEADER_FIELD_NUMBER: _ClassVar[int]
    ANIMATION_DATA_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    animation_data_stream_header: AnimationDataStreamHeader
    animation_data: AnimationData
    status: _v1_pb2_1_1.Status
    def __init__(self, animation_data_stream_header: _Optional[_Union[AnimationDataStreamHeader, _Mapping]] = ..., animation_data: _Optional[_Union[AnimationData, _Mapping]] = ..., status: _Optional[_Union[_v1_pb2_1_1.Status, _Mapping]] = ...) -> None: ...

class AnimationData(_message.Message):
    __slots__ = ("skel_animation", "audio", "camera", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _any_pb2.Any
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[_any_pb2.Any, _Mapping]] = ...) -> None: ...
    SKEL_ANIMATION_FIELD_NUMBER: _ClassVar[int]
    AUDIO_FIELD_NUMBER: _ClassVar[int]
    CAMERA_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    skel_animation: SkelAnimation
    audio: AudioWithTimeCode
    camera: Camera
    metadata: _containers.MessageMap[str, _any_pb2.Any]
    def __init__(self, skel_animation: _Optional[_Union[SkelAnimation, _Mapping]] = ..., audio: _Optional[_Union[AudioWithTimeCode, _Mapping]] = ..., camera: _Optional[_Union[Camera, _Mapping]] = ..., metadata: _Optional[_Mapping[str, _any_pb2.Any]] = ...) -> None: ...

class AudioWithTimeCode(_message.Message):
    __slots__ = ("time_code", "audio_buffer")
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    AUDIO_BUFFER_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    audio_buffer: bytes
    def __init__(self, time_code: _Optional[float] = ..., audio_buffer: _Optional[bytes] = ...) -> None: ...

class SkelAnimationHeader(_message.Message):
    __slots__ = ("blend_shapes", "joints")
    BLEND_SHAPES_FIELD_NUMBER: _ClassVar[int]
    JOINTS_FIELD_NUMBER: _ClassVar[int]
    blend_shapes: _containers.RepeatedScalarFieldContainer[str]
    joints: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, blend_shapes: _Optional[_Iterable[str]] = ..., joints: _Optional[_Iterable[str]] = ...) -> None: ...

class SkelAnimation(_message.Message):
    __slots__ = ("blend_shape_weights", "translations", "rotations", "scales")
    BLEND_SHAPE_WEIGHTS_FIELD_NUMBER: _ClassVar[int]
    TRANSLATIONS_FIELD_NUMBER: _ClassVar[int]
    ROTATIONS_FIELD_NUMBER: _ClassVar[int]
    SCALES_FIELD_NUMBER: _ClassVar[int]
    blend_shape_weights: _containers.RepeatedCompositeFieldContainer[FloatArrayWithTimeCode]
    translations: _containers.RepeatedCompositeFieldContainer[Float3ArrayWithTimeCode]
    rotations: _containers.RepeatedCompositeFieldContainer[QuatFArrayWithTimeCode]
    scales: _containers.RepeatedCompositeFieldContainer[Float3ArrayWithTimeCode]
    def __init__(self, blend_shape_weights: _Optional[_Iterable[_Union[FloatArrayWithTimeCode, _Mapping]]] = ..., translations: _Optional[_Iterable[_Union[Float3ArrayWithTimeCode, _Mapping]]] = ..., rotations: _Optional[_Iterable[_Union[QuatFArrayWithTimeCode, _Mapping]]] = ..., scales: _Optional[_Iterable[_Union[Float3ArrayWithTimeCode, _Mapping]]] = ...) -> None: ...

class Camera(_message.Message):
    __slots__ = ("position", "rotation", "focal_length", "focus_distance")
    POSITION_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    FOCAL_LENGTH_FIELD_NUMBER: _ClassVar[int]
    FOCUS_DISTANCE_FIELD_NUMBER: _ClassVar[int]
    position: _containers.RepeatedCompositeFieldContainer[Float3WithTimeCode]
    rotation: _containers.RepeatedCompositeFieldContainer[QuatFWithTimeCode]
    focal_length: _containers.RepeatedCompositeFieldContainer[FloatWithTimeCode]
    focus_distance: _containers.RepeatedCompositeFieldContainer[FloatWithTimeCode]
    def __init__(self, position: _Optional[_Iterable[_Union[Float3WithTimeCode, _Mapping]]] = ..., rotation: _Optional[_Iterable[_Union[QuatFWithTimeCode, _Mapping]]] = ..., focal_length: _Optional[_Iterable[_Union[FloatWithTimeCode, _Mapping]]] = ..., focus_distance: _Optional[_Iterable[_Union[FloatWithTimeCode, _Mapping]]] = ...) -> None: ...

class FloatArrayWithTimeCode(_message.Message):
    __slots__ = ("time_code", "values")
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    values: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, time_code: _Optional[float] = ..., values: _Optional[_Iterable[float]] = ...) -> None: ...

class Float3ArrayWithTimeCode(_message.Message):
    __slots__ = ("time_code", "values")
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    values: _containers.RepeatedCompositeFieldContainer[Float3]
    def __init__(self, time_code: _Optional[float] = ..., values: _Optional[_Iterable[_Union[Float3, _Mapping]]] = ...) -> None: ...

class QuatFArrayWithTimeCode(_message.Message):
    __slots__ = ("time_code", "values")
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    values: _containers.RepeatedCompositeFieldContainer[QuatF]
    def __init__(self, time_code: _Optional[float] = ..., values: _Optional[_Iterable[_Union[QuatF, _Mapping]]] = ...) -> None: ...

class Float3WithTimeCode(_message.Message):
    __slots__ = ("time_code", "value")
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    value: Float3
    def __init__(self, time_code: _Optional[float] = ..., value: _Optional[_Union[Float3, _Mapping]] = ...) -> None: ...

class QuatFWithTimeCode(_message.Message):
    __slots__ = ("time_code", "value")
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    value: QuatF
    def __init__(self, time_code: _Optional[float] = ..., value: _Optional[_Union[QuatF, _Mapping]] = ...) -> None: ...

class FloatWithTimeCode(_message.Message):
    __slots__ = ("time_code", "value")
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    time_code: float
    value: float
    def __init__(self, time_code: _Optional[float] = ..., value: _Optional[float] = ...) -> None: ...

class QuatF(_message.Message):
    __slots__ = ("real", "i", "j", "k")
    REAL_FIELD_NUMBER: _ClassVar[int]
    I_FIELD_NUMBER: _ClassVar[int]
    J_FIELD_NUMBER: _ClassVar[int]
    K_FIELD_NUMBER: _ClassVar[int]
    real: float
    i: float
    j: float
    k: float
    def __init__(self, real: _Optional[float] = ..., i: _Optional[float] = ..., j: _Optional[float] = ..., k: _Optional[float] = ...) -> None: ...

class Float3(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ...) -> None: ...
