"""
测试 SwdReader - 随机访问、Range 读取、截断恢复、from_bytes
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
    SwdHeader,
    ScalarRecord,
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


def create_scalar_file(path, exp_id, n=100, metric_id=0):
    """辅助函数：创建包含 n 条标量记录的 .swd 文件"""
    writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=metric_id)
    writer.open()
    for i in range(n):
        writer.append_scalar(step=i, value=float(i) * 0.5, timestamp=i * 1000000)
    writer.close()


class TestSwdReaderFile:
    """文件模式读取测试"""

    def test_from_file(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        reader = SwdReader.from_file(path)
        assert reader.record_count == 50
        assert reader.data_type == SWD_DTYPE_SCALAR
        assert reader.record_size == SWD_SCALAR_RECORD_SIZE
        assert reader.file_size == SWD_HEADER_SIZE + 50 * SWD_SCALAR_RECORD_SIZE
        reader.close()

    def test_read_record_random_access(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        n = 1000
        create_scalar_file(path, exp_id, n=n)

        reader = SwdReader.from_file(path)
        # 随机访问各种索引
        for i in [0, 1, 50, 500, 999]:
            record = reader.read_record(i)
            assert record.step == i
            assert record.value == pytest.approx(float(i) * 0.5)
            assert record.timestamp == i * 1000000

        # 乱序访问
        record_500 = reader.read_record(500)
        record_0 = reader.read_record(0)
        record_999 = reader.read_record(999)
        assert record_500.step == 500
        assert record_0.step == 0
        assert record_999.step == 999

        reader.close()

    def test_read_record_out_of_range(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=10)

        reader = SwdReader.from_file(path)
        with pytest.raises(IndexError):
            reader.read_record(10)
        with pytest.raises(IndexError):
            reader.read_record(-11)
        reader.close()

    def test_file_too_small(self, tmp_dir):
        path = os.path.join(tmp_dir, "tiny.swd")
        with open(path, "wb") as f:
            f.write(b'\x00' * 32)
        with pytest.raises(ValueError, match="too small"):
            SwdReader.from_file(path)


class TestSwdReaderRange:
    """Range 读取测试"""

    def test_read_range(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=100)

        reader = SwdReader.from_file(path)
        records = reader.read_range(10, 20)
        assert len(records) == 10
        for i, record in enumerate(records):
            assert record.step == 10 + i
        reader.close()

    def test_read_range_full(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        reader = SwdReader.from_file(path)
        records = reader.read_range(0, 50)
        assert len(records) == 50
        reader.close()

    def test_read_range_empty(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        reader = SwdReader.from_file(path)
        assert reader.read_range(50, 50) == []
        assert reader.read_range(10, 10) == []
        assert reader.read_range(20, 10) == []  # start > end
        reader.close()

    def test_read_range_clamp(self, tmp_dir, exp_id):
        """超出范围时自动裁剪"""
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        reader = SwdReader.from_file(path)
        records = reader.read_range(-10, 1000)
        assert len(records) == 50
        reader.close()

    def test_read_all(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=100)

        reader = SwdReader.from_file(path)
        records = reader.read_all()
        assert len(records) == 100
        reader.close()


class TestSwdReaderTruncation:
    """截断恢复测试"""

    def test_validate_valid_file(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        reader = SwdReader.from_file(path)
        assert reader.validate() is True
        reader.close()

    def test_validate_truncated_file(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        # 在文件末尾添加不完整的记录
        with open(path, "ab") as f:
            f.write(b'\x00' * 10)

        reader = SwdReader.from_file(path)
        assert reader.validate() is False
        assert reader.record_count == 50  # 不完整的记录不计入
        reader.close()

    def test_truncate_to_valid(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        # 添加 10 字节的不完整记录
        with open(path, "ab") as f:
            f.write(b'\xff' * 10)

        reader = SwdReader.from_file(path)
        truncated = reader.truncate_to_valid()
        assert truncated == 10
        assert reader.record_count == 50
        assert reader.validate() is True
        reader.close()

        # 验证文件大小
        expected_size = SWD_HEADER_SIZE + 50 * SWD_SCALAR_RECORD_SIZE
        assert os.path.getsize(path) == expected_size

    def test_truncate_already_valid(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        reader = SwdReader.from_file(path)
        truncated = reader.truncate_to_valid()
        assert truncated == 0
        reader.close()

    def test_truncate_bytes_mode_raises(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=10)

        with open(path, "rb") as f:
            data = f.read()

        reader = SwdReader.from_bytes(data)
        with pytest.raises(RuntimeError, match="only available for file-based"):
            reader.truncate_to_valid()
        reader.close()

    def test_reopen_after_truncation(self, tmp_dir, exp_id):
        """截断后写入器能正确续写"""
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        # 模拟写到一半崩溃
        with open(path, "ab") as f:
            f.write(b'\x00' * 15)  # 不完整的记录

        # 截断恢复
        reader = SwdReader.from_file(path)
        reader.truncate_to_valid()
        reader.close()

        # 续写
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        assert writer.record_count == 50
        for i in range(50, 60):
            writer.append_scalar(i, float(i), 0)
        writer.close()

        # 验证
        reader = SwdReader.from_file(path)
        assert reader.record_count == 60
        for i in range(60):
            assert reader.read_record(i).step == i
        reader.close()


class TestSwdReaderFromBytes:
    """from_bytes 模式测试"""

    def test_from_bytes(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        with open(path, "rb") as f:
            data = f.read()

        reader = SwdReader.from_bytes(data)
        assert reader.record_count == 50
        assert reader.header.experiment_id == exp_id

        for i in range(50):
            record = reader.read_record(i)
            assert record.step == i
        reader.close()

    def test_from_bytes_empty(self, exp_id):
        """只有 header 的 bytes"""
        header_bytes = SwdHeader.pack(
            data_type=SWD_DTYPE_SCALAR,
            record_size=SWD_SCALAR_RECORD_SIZE,
            experiment_id=exp_id,
            metric_id=0,
        )
        reader = SwdReader.from_bytes(header_bytes)
        assert reader.record_count == 0
        assert reader.read_all() == []
        reader.close()

    def test_from_bytes_too_small(self):
        with pytest.raises(ValueError, match="too small"):
            SwdReader.from_bytes(b'\x00' * 10)

    def test_from_bytes_with_truncated_data(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=50)

        with open(path, "rb") as f:
            data = f.read()

        # 添加不完整的数据
        data_with_extra = data + b'\x00' * 10
        reader = SwdReader.from_bytes(data_with_extra)
        # 不完整的记录不计入
        assert reader.record_count == 50
        reader.close()


class TestSwdReaderSlicing:
    """切片和 __len__ 测试"""

    def test_len(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=100)

        reader = SwdReader.from_file(path)
        assert len(reader) == 100
        reader.close()

    def test_getitem_int(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=100)

        reader = SwdReader.from_file(path)
        assert reader[0].step == 0
        assert reader[99].step == 99
        assert reader[-1].step == 99
        assert reader[-100].step == 0
        reader.close()

    def test_getitem_slice(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=100)

        reader = SwdReader.from_file(path)
        records = reader[10:20]
        assert len(records) == 10
        assert records[0].step == 10
        assert records[9].step == 19
        reader.close()

    def test_getitem_slice_step(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=100)

        reader = SwdReader.from_file(path)
        records = reader[0:10:2]
        assert len(records) == 5
        assert records[0].step == 0
        assert records[1].step == 2
        assert records[4].step == 8
        reader.close()

    def test_context_manager(self, tmp_dir, exp_id):
        path = os.path.join(tmp_dir, "test.swd")
        create_scalar_file(path, exp_id, n=10)

        with SwdReader.from_file(path) as reader:
            assert reader.record_count == 10
            assert reader[0].step == 0
