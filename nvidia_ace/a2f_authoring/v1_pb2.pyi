from nvidia_ace.audio import v1_pb2 as _v1_pb2
from nvidia_ace.a2f import v1_pb2 as _v1_pb2_1
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AudioClip(_message.Message):
    __slots__ = ("audio_header", "content")
    AUDIO_HEADER_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    audio_header: _v1_pb2.AudioHeader
    content: bytes
    def __init__(self, audio_header: _Optional[_Union[_v1_pb2.AudioHeader, _Mapping]] = ..., content: _Optional[bytes] = ...) -> None: ...

class AudioClipHandle(_message.Message):
    __slots__ = ("audio_clip_id", "blendshape_names")
    AUDIO_CLIP_ID_FIELD_NUMBER: _ClassVar[int]
    BLENDSHAPE_NAMES_FIELD_NUMBER: _ClassVar[int]
    audio_clip_id: str
    blendshape_names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, audio_clip_id: _Optional[str] = ..., blendshape_names: _Optional[_Iterable[str]] = ...) -> None: ...

class FacePoseRequest(_message.Message):
    __slots__ = ("audio_hash", "preferred_emotions", "time_stamp", "face_params", "emotion_pp_params", "blendshape_params")
    class PreferredEmotionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    AUDIO_HASH_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_EMOTIONS_FIELD_NUMBER: _ClassVar[int]
    TIME_STAMP_FIELD_NUMBER: _ClassVar[int]
    FACE_PARAMS_FIELD_NUMBER: _ClassVar[int]
    EMOTION_PP_PARAMS_FIELD_NUMBER: _ClassVar[int]
    BLENDSHAPE_PARAMS_FIELD_NUMBER: _ClassVar[int]
    audio_hash: str
    preferred_emotions: _containers.ScalarMap[str, float]
    time_stamp: float
    face_params: _v1_pb2_1.FaceParameters
    emotion_pp_params: _v1_pb2_1.EmotionPostProcessingParameters
    blendshape_params: _v1_pb2_1.BlendShapeParameters
    def __init__(self, audio_hash: _Optional[str] = ..., preferred_emotions: _Optional[_Mapping[str, float]] = ..., time_stamp: _Optional[float] = ..., face_params: _Optional[_Union[_v1_pb2_1.FaceParameters, _Mapping]] = ..., emotion_pp_params: _Optional[_Union[_v1_pb2_1.EmotionPostProcessingParameters, _Mapping]] = ..., blendshape_params: _Optional[_Union[_v1_pb2_1.BlendShapeParameters, _Mapping]] = ...) -> None: ...

class BlendShapeData(_message.Message):
    __slots__ = ("blendshapes", "time_code", "emotions")
    class EmotionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    BLENDSHAPES_FIELD_NUMBER: _ClassVar[int]
    TIME_CODE_FIELD_NUMBER: _ClassVar[int]
    EMOTIONS_FIELD_NUMBER: _ClassVar[int]
    blendshapes: _containers.RepeatedScalarFieldContainer[float]
    time_code: float
    emotions: _containers.ScalarMap[str, float]
    def __init__(self, blendshapes: _Optional[_Iterable[float]] = ..., time_code: _Optional[float] = ..., emotions: _Optional[_Mapping[str, float]] = ...) -> None: ...
