"""
测试 SwdWriter - 写入 round-trip、大文件、flush、上下文管理器
"""

import os
import math
import uuid
import tempfile
import pytest

from swanlab.data.swd.format import (
    SWD_HEADER_SIZE,
    SWD_DTYPE_SCALAR,
    SWD_DTYPE_MEDIA_REF,
    SWD_SCALAR_RECORD_SIZE,
    SWD_MEDIA_RECORD_SIZE,
    SWD_MEDIA_TYPE_IMAGE,
    SWD_MEDIA_TYPE_AUDIO,
    SwdHeader,
    ScalarRecord,
    MediaIndexRecord,
)
from swanlab.data.swd.writer import SwdWriter
from swanlab.data.swd.reader import SwdReader


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def exp_id():
    return uuid.uuid4().bytes


class TestSwdWriterScalar:
    """标量写入测试"""

    def test_create_new_file(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        header_bytes = writer.open()

        assert len(header_bytes) == SWD_HEADER_SIZE
        assert os.path.exists(path)
        assert writer.record_count == 0

        writer.close()

    def test_append_scalar_returns_bytes(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()

        raw = writer.append_scalar(step=0, value=1.5, timestamp=1000000)
        assert len(raw) == SWD_SCALAR_RECORD_SIZE
        assert writer.record_count == 1

        # 验证返回的 bytes 可以正确解析
        record = ScalarRecord.unpack(raw)
        assert record.step == 0
        assert record.value == 1.5
        assert record.timestamp == 1000000

        writer.close()

    def test_write_read_roundtrip(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")

        # 写入
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=5)
        writer.open()
        for i in range(100):
            writer.append_scalar(step=i, value=float(i) * 0.1, timestamp=i * 1000)
        writer.close()

        # 读取
        reader = SwdReader.from_file(path)
        assert reader.record_count == 100
        assert reader.header.metric_id == 5
        assert reader.header.experiment_id == exp_id

        for i in range(100):
            record = reader.read_record(i)
            assert record.step == i
            assert record.value == pytest.approx(float(i) * 0.1)
            assert record.timestamp == i * 1000

        reader.close()

    def test_write_nan_inf(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()

        writer.append_scalar(0, float('nan'), 0)
        writer.append_scalar(1, float('inf'), 0)
        writer.append_scalar(2, float('-inf'), 0)
        writer.close()

        reader = SwdReader.from_file(path)
        assert math.isnan(reader.read_record(0).value)
        assert reader.read_record(1).value == float('inf')
        assert reader.read_record(2).value == float('-inf')
        reader.close()

    def test_large_file(self, tmp_dir, exp_id):
        """写入大量记录并验证"""
        path = os.path.join(tmp_dir, "large.swd")
        n = 10000

        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        for i in range(n):
            writer.append_scalar(step=i, value=float(i), timestamp=i)
        writer.close()

        expected_size = SWD_HEADER_SIZE + n * SWD_SCALAR_RECORD_SIZE
        assert os.path.getsize(path) == expected_size

        reader = SwdReader.from_file(path)
        assert reader.record_count == n
        # 抽样验证
        for i in [0, 1, 999, 5000, 9999]:
            assert reader.read_record(i).step == i
            assert reader.read_record(i).value == float(i)
        reader.close()

    def test_file_size_property(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()

        assert writer.file_size == SWD_HEADER_SIZE

        writer.append_scalar(0, 1.0, 0)
        assert writer.file_size == SWD_HEADER_SIZE + SWD_SCALAR_RECORD_SIZE

        writer.append_scalar(1, 2.0, 0)
        assert writer.file_size == SWD_HEADER_SIZE + 2 * SWD_SCALAR_RECORD_SIZE

        writer.close()

    def test_context_manager(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        with SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0) as writer:
            writer.append_scalar(0, 1.0, 0)
            writer.append_scalar(1, 2.0, 0)

        # 文件应该已关闭并且可读
        reader = SwdReader.from_file(path)
        assert reader.record_count == 2
        reader.close()

    def test_append_to_wrong_type_raises(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_MEDIA_REF, exp_id, metric_id=0)
        writer.open()
        with pytest.raises(TypeError, match="Cannot append scalar"):
            writer.append_scalar(0, 1.0, 0)
        writer.close()

    def test_append_without_open_raises(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        with pytest.raises(RuntimeError, match="not open"):
            writer.append_scalar(0, 1.0, 0)

    def test_double_open_raises(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        with pytest.raises(RuntimeError, match="already open"):
            writer.open()
        writer.close()

    def test_reopen_existing_file(self, tmp_dir, exp_id):
        """测试重新打开已有文件并追加"""
        path = os.path.join(tmp_dir, "test.swd")

        # 第一次写入
        writer1 = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer1.open()
        for i in range(50):
            writer1.append_scalar(i, float(i), i * 1000)
        writer1.close()

        # 第二次打开并追加
        writer2 = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        result = writer2.open()
        assert result == b""  # 已有文件返回空 bytes
        assert writer2.record_count == 50

        for i in range(50, 100):
            writer2.append_scalar(i, float(i), i * 1000)
        writer2.close()

        # 验证
        reader = SwdReader.from_file(path)
        assert reader.record_count == 100
        for i in range(100):
            assert reader.read_record(i).step == i
        reader.close()


class TestSwdWriterMedia:
    """媒体索引写入测试"""

    def test_write_media_index(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "media.swd")
        writer = SwdWriter(path, SWD_DTYPE_MEDIA_REF, exp_id, metric_id=1)
        writer.open()

        raw = writer.append_media_index(
            step=0,
            timestamp=1000000,
            object_key=b"media/m_001/img_001.png",
            media_type=SWD_MEDIA_TYPE_IMAGE,
            width=640,
            height=480,
            file_size=102400,
        )
        assert len(raw) == SWD_MEDIA_RECORD_SIZE
        assert writer.record_count == 1
        writer.close()

        reader = SwdReader.from_file(path)
        record = reader.read_record(0)
        assert record.step == 0
        assert record.object_key_str == "media/m_001/img_001.png"
        assert record.media_type == SWD_MEDIA_TYPE_IMAGE
        assert record.width == 640
        assert record.height == 480
        assert record.file_size == 102400
        reader.close()

    def test_media_wrong_type_raises(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        with pytest.raises(TypeError, match="Cannot append media"):
            writer.append_media_index(0, 0, b"key")
        writer.close()

    def test_media_roundtrip_multiple(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "media.swd")
        n = 500

        writer = SwdWriter(path, SWD_DTYPE_MEDIA_REF, exp_id, metric_id=2)
        writer.open()
        for i in range(n):
            writer.append_media_index(
                step=i,
                timestamp=i * 1000,
                object_key=f"media/m_002/img_{i:04d}.png".encode(),
                media_type=SWD_MEDIA_TYPE_IMAGE,
                width=1920,
                height=1080,
                file_size=i * 100,
            )
        writer.close()

        reader = SwdReader.from_file(path)
        assert reader.record_count == n
        for i in [0, 1, 100, 499]:
            record = reader.read_record(i)
            assert record.step == i
            assert record.object_key_str == f"media/m_002/img_{i:04d}.png"
            assert record.file_size == i * 100
        reader.close()


class TestSwdWriterFlush:
    """flush 和 fsync 测试"""

    def test_flush_writes_to_disk(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()

        writer.append_scalar(0, 1.0, 0)
        writer.flush()

        # flush 后文件大小应正确
        assert os.path.getsize(path) == SWD_HEADER_SIZE + SWD_SCALAR_RECORD_SIZE

        writer.close()

    def test_flush_on_closed_writer(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        writer.close()
        # 关闭后 flush 不应报错
        writer.flush()
