from nvidia_ace.a2f import v1_pb2 as _v1_pb2
from nvidia_ace.animation_data import v1_pb2 as _v1_pb2_1
from nvidia_ace.audio import v1_pb2 as _v1_pb2_1_1
from nvidia_ace.status import v1_pb2 as _v1_pb2_1_1_1
from google.protobuf import any_pb2 as _any_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EventType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    END_OF_A2F_AUDIO_PROCESSING: _ClassVar[EventType]
END_OF_A2F_AUDIO_PROCESSING: EventType

class AudioStream(_message.Message):
    __slots__ = ("audio_stream_header", "audio_with_emotion", "end_of_audio")
    class EndOfAudio(_message.Message):
        __slots__ = ()
        def __init__(self) -> None: ...
    AUDIO_STREAM_HEADER_FIELD_NUMBER: _ClassVar[int]
    AUDIO_WITH_EMOTION_FIELD_NUMBER: _ClassVar[int]
    END_OF_AUDIO_FIELD_NUMBER: _ClassVar[int]
    audio_stream_header: AudioStreamHeader
    audio_with_emotion: _v1_pb2.AudioWithEmotion
    end_of_audio: AudioStream.EndOfAudio
    def __init__(self, audio_stream_header: _Optional[_Union[AudioStreamHeader, _Mapping]] = ..., audio_with_emotion: _Optional[_Union[_v1_pb2.AudioWithEmotion, _Mapping]] = ..., end_of_audio: _Optional[_Union[AudioStream.EndOfAudio, _Mapping]] = ...) -> None: ...

class AudioStreamHeader(_message.Message):
    __slots__ = ("audio_header", "face_params", "emotion_post_processing_params", "blendshape_params", "emotion_params")
    AUDIO_HEADER_FIELD_NUMBER: _ClassVar[int]
    FACE_PARAMS_FIELD_NUMBER: _ClassVar[int]
    EMOTION_POST_PROCESSING_PARAMS_FIELD_NUMBER: _ClassVar[int]
    BLENDSHAPE_PARAMS_FIELD_NUMBER: _ClassVar[int]
    EMOTION_PARAMS_FIELD_NUMBER: _ClassVar[int]
    audio_header: _v1_pb2_1_1.AudioHeader
    face_params: _v1_pb2.FaceParameters
    emotion_post_processing_params: _v1_pb2.EmotionPostProcessingParameters
    blendshape_params: _v1_pb2.BlendShapeParameters
    emotion_params: _v1_pb2.EmotionParameters
    def __init__(self, audio_header: _Optional[_Union[_v1_pb2_1_1.AudioHeader, _Mapping]] = ..., face_params: _Optional[_Union[_v1_pb2.FaceParameters, _Mapping]] = ..., emotion_post_processing_params: _Optional[_Union[_v1_pb2.EmotionPostProcessingParameters, _Mapping]] = ..., blendshape_params: _Optional[_Union[_v1_pb2.BlendShapeParameters, _Mapping]] = ..., emotion_params: _Optional[_Union[_v1_pb2.EmotionParameters, _Mapping]] = ...) -> None: ...

class Event(_message.Message):
    __slots__ = ("event_type", "metadata")
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    event_type: EventType
    metadata: _any_pb2.Any
    def __init__(self, event_type: _Optional[_Union[EventType, str]] = ..., metadata: _Optional[_Union[_any_pb2.Any, _Mapping]] = ...) -> None: ...

class AnimationDataStreamHeader(_message.Message):
    __slots__ = ("audio_header", "skel_animation_header", "start_time_code_since_epoch")
    AUDIO_HEADER_FIELD_NUMBER: _ClassVar[int]
    SKEL_ANIMATION_HEADER_FIELD_NUMBER: _ClassVar[int]
    START_TIME_CODE_SINCE_EPOCH_FIELD_NUMBER: _ClassVar[int]
    audio_header: _v1_pb2_1_1.AudioHeader
    skel_animation_header: _v1_pb2_1.SkelAnimationHeader
    start_time_code_since_epoch: float
    def __init__(self, audio_header: _Optional[_Union[_v1_pb2_1_1.AudioHeader, _Mapping]] = ..., skel_animation_header: _Optional[_Union[_v1_pb2_1.SkelAnimationHeader, _Mapping]] = ..., start_time_code_since_epoch: _Optional[float] = ...) -> None: ...

class AnimationDataStream(_message.Message):
    __slots__ = ("animation_data_stream_header", "animation_data", "event", "status")
    ANIMATION_DATA_STREAM_HEADER_FIELD_NUMBER: _ClassVar[int]
    ANIMATION_DATA_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    animation_data_stream_header: AnimationDataStreamHeader
    animation_data: _v1_pb2_1.AnimationData
    event: Event
    status: _v1_pb2_1_1_1.Status
    def __init__(self, animation_data_stream_header: _Optional[_Union[AnimationDataStreamHeader, _Mapping]] = ..., animation_data: _Optional[_Union[_v1_pb2_1.AnimationData, _Mapping]] = ..., event: _Optional[_Union[Event, _Mapping]] = ..., status: _Optional[_Union[_v1_pb2_1_1_1.Status, _Mapping]] = ...) -> None: ...
