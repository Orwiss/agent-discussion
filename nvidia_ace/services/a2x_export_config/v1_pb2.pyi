from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ConfigsTypeRequest(_message.Message):
    __slots__ = ("config_type",)
    class ConfigType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        YAML: _ClassVar[ConfigsTypeRequest.ConfigType]
        JSON: _ClassVar[ConfigsTypeRequest.ConfigType]
    YAML: ConfigsTypeRequest.ConfigType
    JSON: ConfigsTypeRequest.ConfigType
    CONFIG_TYPE_FIELD_NUMBER: _ClassVar[int]
    config_type: ConfigsTypeRequest.ConfigType
    def __init__(self, config_type: _Optional[_Union[ConfigsTypeRequest.ConfigType, str]] = ...) -> None: ...

class A2XConfig(_message.Message):
    __slots__ = ("name", "content")
    NAME_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    name: str
    content: str
    def __init__(self, name: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...
