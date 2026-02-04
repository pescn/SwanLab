"""
@file: format.py
@description: .swd 二进制格式常量、struct 定义和 NamedTuple 类型

.swd 文件格式规格：

Header (128 bytes, 创建时一次性写入):
    Magic:         4B   ":SWD"
    Version:       2B   u16
    Flags:         2B   u16 (预留)
    DataType:      2B   u16 (1=float64 标量, 2=media_ref 媒体索引)
    RecordSize:    2B   u16 (24 或 80)
    Sequence:      4B   u32 (分片序号，>5GB 时递增)
    StartIndex:    8B   i64 (本文件第一条记录的全局索引)
    ExperimentID: 16B   UUID bytes
    MetricID:      4B   u32
    Reserved:     84B

Scalar Record (24 bytes):
    step:       i64  8B
    value:      f64  8B
    timestamp:  i64  8B (epoch microseconds)

Media Index Record (80 bytes):
    step:        i64    8B
    timestamp:   i64    8B
    object_key:  bytes  48B (null-padded)
    media_type:  u8     1B  (0=image, 1=audio, 2=text, ...)
    width:       u16    2B
    height:      u16    2B
    file_size:   u32    4B
    reserved:    bytes  7B
"""

import struct
from typing import NamedTuple, Optional

# ---------------------------------- 常量 ----------------------------------

# 文件标识，对应现有 DataStore 的 ":SWL"
SWD_MAGIC = b":SWD"

# 当前格式版本
SWD_VERSION = 1

# Header 固定大小
SWD_HEADER_SIZE = 128

# 数据类型枚举
SWD_DTYPE_SCALAR = 1       # float64 标量
SWD_DTYPE_MEDIA_REF = 2    # 媒体索引引用

# 记录大小
SWD_SCALAR_RECORD_SIZE = 24
SWD_MEDIA_RECORD_SIZE = 80

# object_key 字段最大长度
SWD_OBJECT_KEY_MAX_LEN = 48

# 媒体类型枚举
SWD_MEDIA_TYPE_IMAGE = 0
SWD_MEDIA_TYPE_AUDIO = 1
SWD_MEDIA_TYPE_TEXT = 2
SWD_MEDIA_TYPE_VIDEO = 3
SWD_MEDIA_TYPE_OBJECT3D = 4

# Header 预留区域大小
_SWD_HEADER_RESERVED_SIZE = 84

# ---------------------------------- struct 格式 ----------------------------------

# Header: magic(4s) + version(H) + flags(H) + dtype(H) + record_size(H)
#        + sequence(I) + start_index(q) + experiment_id(16s) + metric_id(I) + reserved(20s)
SWD_HEADER_FMT = "<4sHHHHIq16sI84s"

# Scalar Record: step(q) + value(d) + timestamp(q)
SWD_SCALAR_RECORD_FMT = "<qdq"

# Media Index Record: step(q) + timestamp(q) + object_key(48s) + media_type(B)
#                    + width(H) + height(H) + file_size(I) + reserved(7s)
SWD_MEDIA_RECORD_FMT = "<qq48sBHHI7s"

# 编译时验证 struct 大小
assert struct.calcsize(SWD_HEADER_FMT) == SWD_HEADER_SIZE, \
    f"Header struct size mismatch: {struct.calcsize(SWD_HEADER_FMT)} != {SWD_HEADER_SIZE}"
assert struct.calcsize(SWD_SCALAR_RECORD_FMT) == SWD_SCALAR_RECORD_SIZE, \
    f"Scalar record struct size mismatch: {struct.calcsize(SWD_SCALAR_RECORD_FMT)} != {SWD_SCALAR_RECORD_SIZE}"
assert struct.calcsize(SWD_MEDIA_RECORD_FMT) == SWD_MEDIA_RECORD_SIZE, \
    f"Media record struct size mismatch: {struct.calcsize(SWD_MEDIA_RECORD_FMT)} != {SWD_MEDIA_RECORD_SIZE}"


# ---------------------------------- NamedTuple 类型 ----------------------------------

class SwdHeader(NamedTuple):
    """解析后的 .swd 文件头"""
    magic: bytes
    version: int
    flags: int
    data_type: int
    record_size: int
    sequence: int
    start_index: int
    experiment_id: bytes
    metric_id: int
    reserved: bytes

    @staticmethod
    def pack(
        data_type: int,
        record_size: int,
        experiment_id: bytes,
        metric_id: int,
        sequence: int = 0,
        start_index: int = 0,
        flags: int = 0,
    ) -> bytes:
        """将参数打包为 64 字节的 header bytes"""
        if len(experiment_id) > 16:
            raise ValueError(f"experiment_id must be <= 16 bytes, got {len(experiment_id)}")
        # 右侧填充零字节到 16 字节
        experiment_id = experiment_id.ljust(16, b'\x00')
        return struct.pack(
            SWD_HEADER_FMT,
            SWD_MAGIC,
            SWD_VERSION,
            flags,
            data_type,
            record_size,
            sequence,
            start_index,
            experiment_id,
            metric_id,
            b'\x00' * _SWD_HEADER_RESERVED_SIZE,
        )

    @staticmethod
    def unpack(data: bytes) -> "SwdHeader":
        """从 64 字节的 bytes 中解析出 SwdHeader"""
        if len(data) != SWD_HEADER_SIZE:
            raise ValueError(f"Header data must be exactly {SWD_HEADER_SIZE} bytes, got {len(data)}")
        fields = struct.unpack(SWD_HEADER_FMT, data)
        header = SwdHeader(*fields)
        if header.magic != SWD_MAGIC:
            raise ValueError(f"Invalid magic bytes: {header.magic!r}, expected {SWD_MAGIC!r}")
        return header


class ScalarRecord(NamedTuple):
    """标量记录（24 bytes）"""
    step: int
    value: float
    timestamp: int  # epoch microseconds

    @staticmethod
    def pack(step: int, value: float, timestamp: int) -> bytes:
        """打包为 24 字节的 bytes"""
        return struct.pack(SWD_SCALAR_RECORD_FMT, step, value, timestamp)

    @staticmethod
    def unpack(data: bytes) -> "ScalarRecord":
        """从 24 字节的 bytes 中解析出 ScalarRecord"""
        if len(data) != SWD_SCALAR_RECORD_SIZE:
            raise ValueError(f"Scalar record must be exactly {SWD_SCALAR_RECORD_SIZE} bytes, got {len(data)}")
        return ScalarRecord(*struct.unpack(SWD_SCALAR_RECORD_FMT, data))


class MediaIndexRecord(NamedTuple):
    """媒体索引记录（80 bytes）"""
    step: int
    timestamp: int  # epoch microseconds
    object_key: bytes  # null-padded, max 48 bytes
    media_type: int    # 0=image, 1=audio, 2=text, ...
    width: int
    height: int
    file_size: int
    reserved: bytes

    @staticmethod
    def pack(
        step: int,
        timestamp: int,
        object_key: bytes,
        media_type: int = SWD_MEDIA_TYPE_IMAGE,
        width: int = 0,
        height: int = 0,
        file_size: int = 0,
    ) -> bytes:
        """打包为 80 字节的 bytes"""
        if len(object_key) > SWD_OBJECT_KEY_MAX_LEN:
            raise ValueError(
                f"object_key must be <= {SWD_OBJECT_KEY_MAX_LEN} bytes, got {len(object_key)}"
            )
        # 右侧填充零字节到 48 字节
        object_key = object_key.ljust(SWD_OBJECT_KEY_MAX_LEN, b'\x00')
        return struct.pack(
            SWD_MEDIA_RECORD_FMT,
            step,
            timestamp,
            object_key,
            media_type,
            width,
            height,
            file_size,
            b'\x00' * 7,
        )

    @staticmethod
    def unpack(data: bytes) -> "MediaIndexRecord":
        """从 80 字节的 bytes 中解析出 MediaIndexRecord"""
        if len(data) != SWD_MEDIA_RECORD_SIZE:
            raise ValueError(f"Media record must be exactly {SWD_MEDIA_RECORD_SIZE} bytes, got {len(data)}")
        return MediaIndexRecord(*struct.unpack(SWD_MEDIA_RECORD_FMT, data))

    @property
    def object_key_str(self) -> str:
        """返回去除 null 填充的 object_key 字符串"""
        return self.object_key.rstrip(b'\x00').decode('utf-8')


def get_record_size(data_type: int) -> int:
    """根据数据类型返回记录大小"""
    if data_type == SWD_DTYPE_SCALAR:
        return SWD_SCALAR_RECORD_SIZE
    elif data_type == SWD_DTYPE_MEDIA_REF:
        return SWD_MEDIA_RECORD_SIZE
    else:
        raise ValueError(f"Unknown data type: {data_type}")


def compute_record_count(file_size: int, record_size: int) -> int:
    """根据文件大小和记录大小计算有效记录数"""
    if file_size < SWD_HEADER_SIZE:
        return 0
    data_size = file_size - SWD_HEADER_SIZE
    return data_size // record_size


def compute_record_offset(index: int, record_size: int) -> int:
    """计算第 N 条记录在文件中的字节偏移"""
    return SWD_HEADER_SIZE + index * record_size


def is_file_valid(file_size: int, record_size: int) -> bool:
    """检查文件大小是否完整（无截断记录）"""
    if file_size < SWD_HEADER_SIZE:
        return False
    data_size = file_size - SWD_HEADER_SIZE
    return data_size % record_size == 0
