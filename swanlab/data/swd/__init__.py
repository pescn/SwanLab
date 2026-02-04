"""
@file: __init__.py
@description: .swd 二进制格式模块

导出 SwdWriter, SwdReader, 和格式常量
"""

from .format import (
    SWD_MAGIC,
    SWD_VERSION,
    SWD_HEADER_SIZE,
    SWD_DTYPE_SCALAR,
    SWD_DTYPE_MEDIA_REF,
    SWD_SCALAR_RECORD_SIZE,
    SWD_MEDIA_RECORD_SIZE,
    SWD_OBJECT_KEY_MAX_LEN,
    SWD_MEDIA_TYPE_IMAGE,
    SWD_MEDIA_TYPE_AUDIO,
    SWD_MEDIA_TYPE_TEXT,
    SWD_MEDIA_TYPE_VIDEO,
    SWD_MEDIA_TYPE_OBJECT3D,
    SwdHeader,
    ScalarRecord,
    MediaIndexRecord,
    get_record_size,
    compute_record_count,
    compute_record_offset,
    is_file_valid,
)
from .writer import SwdWriter
from .reader import SwdReader
from .manifest import ManifestManager
from .console import ConsoleLogAppender
from .converter import BackupToSwdConverter, JsonlToSwdConverter, SwdExporter

__all__ = [
    # 常量
    "SWD_MAGIC",
    "SWD_VERSION",
    "SWD_HEADER_SIZE",
    "SWD_DTYPE_SCALAR",
    "SWD_DTYPE_MEDIA_REF",
    "SWD_SCALAR_RECORD_SIZE",
    "SWD_MEDIA_RECORD_SIZE",
    "SWD_OBJECT_KEY_MAX_LEN",
    "SWD_MEDIA_TYPE_IMAGE",
    "SWD_MEDIA_TYPE_AUDIO",
    "SWD_MEDIA_TYPE_TEXT",
    "SWD_MEDIA_TYPE_VIDEO",
    "SWD_MEDIA_TYPE_OBJECT3D",
    # NamedTuple 类型
    "SwdHeader",
    "ScalarRecord",
    "MediaIndexRecord",
    # 工具函数
    "get_record_size",
    "compute_record_count",
    "compute_record_offset",
    "is_file_valid",
    # 读写器
    "SwdWriter",
    "SwdReader",
    # Manifest
    "ManifestManager",
    # Console
    "ConsoleLogAppender",
    # Converters
    "BackupToSwdConverter",
    "JsonlToSwdConverter",
    "SwdExporter",
]
