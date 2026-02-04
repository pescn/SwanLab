"""
测试 .swd 格式常量、struct 定义和 NamedTuple 的正确性
"""

import struct
import math
import uuid
import pytest

from swanlab.data.swd.format import (
    SWD_MAGIC,
    SWD_VERSION,
    SWD_HEADER_SIZE,
    SWD_HEADER_FMT,
    SWD_SCALAR_RECORD_FMT,
    SWD_MEDIA_RECORD_FMT,
    SWD_DTYPE_SCALAR,
    SWD_DTYPE_MEDIA_REF,
    SWD_SCALAR_RECORD_SIZE,
    SWD_MEDIA_RECORD_SIZE,
    SWD_OBJECT_KEY_MAX_LEN,
    SWD_MEDIA_TYPE_IMAGE,
    SWD_MEDIA_TYPE_AUDIO,
    SWD_MEDIA_TYPE_TEXT,
    SwdHeader,
    ScalarRecord,
    MediaIndexRecord,
    get_record_size,
    compute_record_count,
    compute_record_offset,
    is_file_valid,
)


class TestStructAlignment:
    """struct 大小对齐测试"""

    def test_header_size(self):
        assert struct.calcsize(SWD_HEADER_FMT) == SWD_HEADER_SIZE == 128

    def test_scalar_record_size(self):
        assert struct.calcsize(SWD_SCALAR_RECORD_FMT) == SWD_SCALAR_RECORD_SIZE == 24

    def test_media_record_size(self):
        assert struct.calcsize(SWD_MEDIA_RECORD_FMT) == SWD_MEDIA_RECORD_SIZE == 80


class TestSwdHeader:
    """Header 解析测试"""

    def test_pack_unpack_roundtrip(self):
        exp_id = uuid.uuid4().bytes
        header_bytes = SwdHeader.pack(
            data_type=SWD_DTYPE_SCALAR,
            record_size=SWD_SCALAR_RECORD_SIZE,
            experiment_id=exp_id,
            metric_id=42,
            sequence=0,
            start_index=0,
            flags=0,
        )
        assert len(header_bytes) == SWD_HEADER_SIZE

        header = SwdHeader.unpack(header_bytes)
        assert header.magic == SWD_MAGIC
        assert header.version == SWD_VERSION
        assert header.flags == 0
        assert header.data_type == SWD_DTYPE_SCALAR
        assert header.record_size == SWD_SCALAR_RECORD_SIZE
        assert header.sequence == 0
        assert header.start_index == 0
        assert header.experiment_id == exp_id
        assert header.metric_id == 42

    def test_pack_media_header(self):
        exp_id = b'\x01' * 16
        header_bytes = SwdHeader.pack(
            data_type=SWD_DTYPE_MEDIA_REF,
            record_size=SWD_MEDIA_RECORD_SIZE,
            experiment_id=exp_id,
            metric_id=7,
            sequence=1,
            start_index=100000,
        )
        header = SwdHeader.unpack(header_bytes)
        assert header.data_type == SWD_DTYPE_MEDIA_REF
        assert header.record_size == SWD_MEDIA_RECORD_SIZE
        assert header.sequence == 1
        assert header.start_index == 100000
        assert header.metric_id == 7

    def test_short_experiment_id_padded(self):
        short_id = b'\xab\xcd'
        header_bytes = SwdHeader.pack(
            data_type=SWD_DTYPE_SCALAR,
            record_size=SWD_SCALAR_RECORD_SIZE,
            experiment_id=short_id,
            metric_id=0,
        )
        header = SwdHeader.unpack(header_bytes)
        assert header.experiment_id == short_id + b'\x00' * 14

    def test_experiment_id_too_long(self):
        with pytest.raises(ValueError, match="experiment_id must be <= 16 bytes"):
            SwdHeader.pack(
                data_type=SWD_DTYPE_SCALAR,
                record_size=SWD_SCALAR_RECORD_SIZE,
                experiment_id=b'\x00' * 17,
                metric_id=0,
            )

    def test_invalid_magic(self):
        header_bytes = SwdHeader.pack(
            data_type=SWD_DTYPE_SCALAR,
            record_size=SWD_SCALAR_RECORD_SIZE,
            experiment_id=b'\x00' * 16,
            metric_id=0,
        )
        # 篡改 magic bytes
        corrupted = b"XXXX" + header_bytes[4:]
        with pytest.raises(ValueError, match="Invalid magic bytes"):
            SwdHeader.unpack(corrupted)

    def test_wrong_size_data(self):
        with pytest.raises(ValueError, match="must be exactly 128 bytes"):
            SwdHeader.unpack(b'\x00' * 32)


class TestScalarRecord:
    """标量记录测试"""

    def test_pack_unpack_roundtrip(self):
        step, value, ts = 100, 3.14159, 1719849600_000000
        data = ScalarRecord.pack(step, value, ts)
        assert len(data) == SWD_SCALAR_RECORD_SIZE

        record = ScalarRecord.unpack(data)
        assert record.step == step
        assert record.value == pytest.approx(value)
        assert record.timestamp == ts

    def test_nan_value(self):
        data = ScalarRecord.pack(0, float('nan'), 0)
        record = ScalarRecord.unpack(data)
        assert math.isnan(record.value)

    def test_inf_value(self):
        data = ScalarRecord.pack(0, float('inf'), 0)
        record = ScalarRecord.unpack(data)
        assert math.isinf(record.value) and record.value > 0

    def test_negative_inf_value(self):
        data = ScalarRecord.pack(0, float('-inf'), 0)
        record = ScalarRecord.unpack(data)
        assert math.isinf(record.value) and record.value < 0

    def test_zero_value(self):
        data = ScalarRecord.pack(0, 0.0, 0)
        record = ScalarRecord.unpack(data)
        assert record.value == 0.0

    def test_large_step(self):
        step = 2**62
        data = ScalarRecord.pack(step, 1.0, 0)
        record = ScalarRecord.unpack(data)
        assert record.step == step

    def test_negative_step(self):
        data = ScalarRecord.pack(-1, 1.0, 0)
        record = ScalarRecord.unpack(data)
        assert record.step == -1

    def test_wrong_size_data(self):
        with pytest.raises(ValueError, match="must be exactly 24 bytes"):
            ScalarRecord.unpack(b'\x00' * 10)


class TestMediaIndexRecord:
    """媒体索引记录测试"""

    def test_pack_unpack_roundtrip(self):
        key = b"media/m_002/a1b2c3d4.png"
        data = MediaIndexRecord.pack(
            step=5,
            timestamp=1719849600_000000,
            object_key=key,
            media_type=SWD_MEDIA_TYPE_IMAGE,
            width=1920,
            height=1080,
            file_size=1048576,
        )
        assert len(data) == SWD_MEDIA_RECORD_SIZE

        record = MediaIndexRecord.unpack(data)
        assert record.step == 5
        assert record.timestamp == 1719849600_000000
        assert record.object_key_str == "media/m_002/a1b2c3d4.png"
        assert record.media_type == SWD_MEDIA_TYPE_IMAGE
        assert record.width == 1920
        assert record.height == 1080
        assert record.file_size == 1048576

    def test_object_key_padded(self):
        key = b"short.png"
        data = MediaIndexRecord.pack(step=0, timestamp=0, object_key=key)
        record = MediaIndexRecord.unpack(data)
        assert len(record.object_key) == SWD_OBJECT_KEY_MAX_LEN
        assert record.object_key_str == "short.png"

    def test_object_key_max_length(self):
        key = b"x" * SWD_OBJECT_KEY_MAX_LEN
        data = MediaIndexRecord.pack(step=0, timestamp=0, object_key=key)
        record = MediaIndexRecord.unpack(data)
        assert record.object_key_str == "x" * SWD_OBJECT_KEY_MAX_LEN

    def test_object_key_too_long(self):
        key = b"x" * (SWD_OBJECT_KEY_MAX_LEN + 1)
        with pytest.raises(ValueError, match="object_key must be <= 48 bytes"):
            MediaIndexRecord.pack(step=0, timestamp=0, object_key=key)

    def test_audio_media_type(self):
        data = MediaIndexRecord.pack(
            step=0, timestamp=0, object_key=b"audio.wav",
            media_type=SWD_MEDIA_TYPE_AUDIO,
        )
        record = MediaIndexRecord.unpack(data)
        assert record.media_type == SWD_MEDIA_TYPE_AUDIO

    def test_text_media_type(self):
        data = MediaIndexRecord.pack(
            step=0, timestamp=0, object_key=b"text.txt",
            media_type=SWD_MEDIA_TYPE_TEXT,
        )
        record = MediaIndexRecord.unpack(data)
        assert record.media_type == SWD_MEDIA_TYPE_TEXT

    def test_wrong_size_data(self):
        with pytest.raises(ValueError, match="must be exactly 80 bytes"):
            MediaIndexRecord.unpack(b'\x00' * 40)


class TestUtilityFunctions:
    """工具函数测试"""

    def test_get_record_size_scalar(self):
        assert get_record_size(SWD_DTYPE_SCALAR) == SWD_SCALAR_RECORD_SIZE

    def test_get_record_size_media(self):
        assert get_record_size(SWD_DTYPE_MEDIA_REF) == SWD_MEDIA_RECORD_SIZE

    def test_get_record_size_unknown(self):
        with pytest.raises(ValueError, match="Unknown data type"):
            get_record_size(99)

    def test_compute_record_count_empty(self):
        assert compute_record_count(SWD_HEADER_SIZE, SWD_SCALAR_RECORD_SIZE) == 0

    def test_compute_record_count_scalar(self):
        file_size = SWD_HEADER_SIZE + 100 * SWD_SCALAR_RECORD_SIZE
        assert compute_record_count(file_size, SWD_SCALAR_RECORD_SIZE) == 100

    def test_compute_record_count_truncated(self):
        # 不完整的记录不计入
        file_size = SWD_HEADER_SIZE + 100 * SWD_SCALAR_RECORD_SIZE + 10
        assert compute_record_count(file_size, SWD_SCALAR_RECORD_SIZE) == 100

    def test_compute_record_count_too_small(self):
        assert compute_record_count(32, SWD_SCALAR_RECORD_SIZE) == 0

    def test_compute_record_offset(self):
        assert compute_record_offset(0, SWD_SCALAR_RECORD_SIZE) == SWD_HEADER_SIZE
        assert compute_record_offset(1, SWD_SCALAR_RECORD_SIZE) == SWD_HEADER_SIZE + SWD_SCALAR_RECORD_SIZE
        assert compute_record_offset(10, SWD_SCALAR_RECORD_SIZE) == SWD_HEADER_SIZE + 10 * SWD_SCALAR_RECORD_SIZE

    def test_is_file_valid(self):
        assert is_file_valid(SWD_HEADER_SIZE, SWD_SCALAR_RECORD_SIZE) is True
        assert is_file_valid(SWD_HEADER_SIZE + SWD_SCALAR_RECORD_SIZE, SWD_SCALAR_RECORD_SIZE) is True
        assert is_file_valid(SWD_HEADER_SIZE + 10, SWD_SCALAR_RECORD_SIZE) is False
        assert is_file_valid(32, SWD_SCALAR_RECORD_SIZE) is False
