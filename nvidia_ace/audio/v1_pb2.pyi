from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AudioHeader(_message.Message):
    __slots__ = ("audio_format", "channel_count", "samples_per_second", "bits_per_sample")
    class AudioFormat(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        AUDIO_FORMAT_PCM: _ClassVar[AudioHeader.AudioFormat]
    AUDIO_FORMAT_PCM: AudioHeader.AudioFormat
    AUDIO_FORMAT_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_COUNT_FIELD_NUMBER: _ClassVar[int]
    SAMPLES_PER_SECOND_FIELD_NUMBER: _ClassVar[int]
    BITS_PER_SAMPLE_FIELD_NUMBER: _ClassVar[int]
    audio_format: AudioHeader.AudioFormat
    channel_count: int
    samples_per_second: int
    bits_per_sample: int
    def __init__(self, audio_format: _Optional[_Union[AudioHeader.AudioFormat, str]] = ..., channel_count: _Optional[int] = ..., samples_per_second: _Optional[int] = ..., bits_per_sample: _Optional[int] = ...) -> None: ...
