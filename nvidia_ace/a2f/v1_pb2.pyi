from nvidia_ace.animation_id import v1_pb2 as _v1_pb2
from nvidia_ace.status import v1_pb2 as _v1_pb2_1
from nvidia_ace.audio import v1_pb2 as _v1_pb2_1_1
from nvidia_ace.emotion_with_timecode import v1_pb2 as _v1_pb2_1_1_1
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AudioStream(_message.Message):
    __slots__ = ("audio_stream_header", "audio_with_emotion")
    AUDIO_STREAM_HEADER_FIELD_NUMBER: _ClassVar[int]
    AUDIO_WITH_EMOTION_FIELD_NUMBER: _ClassVar[int]
    audio_stream_header: AudioStreamHeader
    audio_with_emotion: AudioWithEmotion
    def __init__(self, audio_stream_header: _Optional[_Union[AudioStreamHeader, _Mapping]] = ..., audio_with_emotion: _Optional[_Union[AudioWithEmotion, _Mapping]] = ...) -> None: ...

class AudioStreamHeader(_message.Message):
    __slots__ = ("animation_ids", "audio_header", "face_params", "emotion_post_processing_params", "blendshape_params", "emotion_params")
    ANIMATION_IDS_FIELD_NUMBER: _ClassVar[int]
    AUDIO_HEADER_FIELD_NUMBER: _ClassVar[int]
    FACE_PARAMS_FIELD_NUMBER: _ClassVar[int]
    EMOTION_POST_PROCESSING_PARAMS_FIELD_NUMBER: _ClassVar[int]
    BLENDSHAPE_PARAMS_FIELD_NUMBER: _ClassVar[int]
    EMOTION_PARAMS_FIELD_NUMBER: _ClassVar[int]
    animation_ids: _v1_pb2.AnimationIds
    audio_header: _v1_pb2_1_1.AudioHeader
    face_params: FaceParameters
    emotion_post_processing_params: EmotionPostProcessingParameters
    blendshape_params: BlendShapeParameters
    emotion_params: EmotionParameters
    def __init__(self, animation_ids: _Optional[_Union[_v1_pb2.AnimationIds, _Mapping]] = ..., audio_header: _Optional[_Union[_v1_pb2_1_1.AudioHeader, _Mapping]] = ..., face_params: _Optional[_Union[FaceParameters, _Mapping]] = ..., emotion_post_processing_params: _Optional[_Union[EmotionPostProcessingParameters, _Mapping]] = ..., blendshape_params: _Optional[_Union[BlendShapeParameters, _Mapping]] = ..., emotion_params: _Optional[_Union[EmotionParameters, _Mapping]] = ...) -> None: ...

class FloatArray(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, values: _Optional[_Iterable[float]] = ...) -> None: ...

class FaceParameters(_message.Message):
    __slots__ = ("float_params", "integer_params", "float_array_params")
    class FloatParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    class IntegerParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    class FloatArrayParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: FloatArray
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[FloatArray, _Mapping]] = ...) -> None: ...
    FLOAT_PARAMS_FIELD_NUMBER: _ClassVar[int]
    INTEGER_PARAMS_FIELD_NUMBER: _ClassVar[int]
    FLOAT_ARRAY_PARAMS_FIELD_NUMBER: _ClassVar[int]
    float_params: _containers.ScalarMap[str, float]
    integer_params: _containers.ScalarMap[str, int]
    float_array_params: _containers.MessageMap[str, FloatArray]
    def __init__(self, float_params: _Optional[_Mapping[str, float]] = ..., integer_params: _Optional[_Mapping[str, int]] = ..., float_array_params: _Optional[_Mapping[str, FloatArray]] = ...) -> None: ...

class BlendShapeParameters(_message.Message):
    __slots__ = ("bs_weight_multipliers", "bs_weight_offsets", "enable_clamping_bs_weight")
    class BsWeightMultipliersEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    class BsWeightOffsetsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    BS_WEIGHT_MULTIPLIERS_FIELD_NUMBER: _ClassVar[int]
    BS_WEIGHT_OFFSETS_FIELD_NUMBER: _ClassVar[int]
    ENABLE_CLAMPING_BS_WEIGHT_FIELD_NUMBER: _ClassVar[int]
    bs_weight_multipliers: _containers.ScalarMap[str, float]
    bs_weight_offsets: _containers.ScalarMap[str, float]
    enable_clamping_bs_weight: bool
    def __init__(self, bs_weight_multipliers: _Optional[_Mapping[str, float]] = ..., bs_weight_offsets: _Optional[_Mapping[str, float]] = ..., enable_clamping_bs_weight: bool = ...) -> None: ...

class EmotionParameters(_message.Message):
    __slots__ = ("live_transition_time", "beginning_emotion")
    class BeginningEmotionEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    LIVE_TRANSITION_TIME_FIELD_NUMBER: _ClassVar[int]
    BEGINNING_EMOTION_FIELD_NUMBER: _ClassVar[int]
    live_transition_time: float
    beginning_emotion: _containers.ScalarMap[str, float]
    def __init__(self, live_transition_time: _Optional[float] = ..., beginning_emotion: _Optional[_Mapping[str, float]] = ...) -> None: ...

class EmotionPostProcessingParameters(_message.Message):
    __slots__ = ("emotion_contrast", "live_blend_coef", "enable_preferred_emotion", "preferred_emotion_strength", "emotion_strength", "max_emotions")
    EMOTION_CONTRAST_FIELD_NUMBER: _ClassVar[int]
    LIVE_BLEND_COEF_FIELD_NUMBER: _ClassVar[int]
    ENABLE_PREFERRED_EMOTION_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_EMOTION_STRENGTH_FIELD_NUMBER: _ClassVar[int]
    EMOTION_STRENGTH_FIELD_NUMBER: _ClassVar[int]
    MAX_EMOTIONS_FIELD_NUMBER: _ClassVar[int]
    emotion_contrast: float
    live_blend_coef: float
    enable_preferred_emotion: bool
    preferred_emotion_strength: float
    emotion_strength: float
    max_emotions: int
    def __init__(self, emotion_contrast: _Optional[float] = ..., live_blend_coef: _Optional[float] = ..., enable_preferred_emotion: bool = ..., preferred_emotion_strength: _Optional[float] = ..., emotion_strength: _Optional[float] = ..., max_emotions: _Optional[int] = ...) -> None: ...

class AudioWithEmotion(_message.Message):
    __slots__ = ("audio_buffer", "emotions")
    AUDIO_BUFFER_FIELD_NUMBER: _ClassVar[int]
    EMOTIONS_FIELD_NUMBER: _ClassVar[int]
    audio_buffer: bytes
    emotions: _containers.RepeatedCompositeFieldContainer[_v1_pb2_1_1_1.EmotionWithTimeCode]
    def __init__(self, audio_buffer: _Optional[bytes] = ..., emotions: _Optional[_Iterable[_Union[_v1_pb2_1_1_1.EmotionWithTimeCode, _Mapping]]] = ...) -> None: ...
