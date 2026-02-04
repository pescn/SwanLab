"""
@file: reader.py
@description: SwdReader - .swd 文件读取器

支持：
- 从本地文件随机访问读取
- 从 bytes（COS Range GET 响应）直接构建 reader
- 截断恢复：检测并截断不完整的最后一条记录
- Range 读取：按索引范围读取记录
"""

import os
from typing import Optional, Union, List, IO, Any

from .format import (
    SWD_HEADER_SIZE,
    SWD_DTYPE_SCALAR,
    SWD_DTYPE_MEDIA_REF,
    SwdHeader,
    ScalarRecord,
    MediaIndexRecord,
    get_record_size,
    compute_record_count,
    compute_record_offset,
    is_file_valid,
)


class SwdReader:
    """
    .swd 文件读取器

    用法 1 - 从文件读取:
        reader = SwdReader.from_file("data/m_000.swd")
        header = reader.header
        record = reader.read_record(0)
        records = reader.read_range(0, 100)
        reader.close()

    用法 2 - 从 bytes 读取:
        reader = SwdReader.from_bytes(data)
        records = reader.read_range(0, reader.record_count)
    """

    def __init__(self):
        self._header: Optional[SwdHeader] = None
        self._fp: Optional[IO[Any]] = None
        self._data: Optional[bytes] = None
        self._file_path: Optional[str] = None
        self._file_size: int = 0
        self._record_count: int = 0

    @property
    def header(self) -> Optional[SwdHeader]:
        return self._header

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def file_size(self) -> int:
        return self._file_size

    @property
    def data_type(self) -> Optional[int]:
        return self._header.data_type if self._header else None

    @property
    def record_size(self) -> Optional[int]:
        return self._header.record_size if self._header else None

    @staticmethod
    def from_file(file_path: str) -> "SwdReader":
        """
        从本地文件创建 reader

        :param file_path: .swd 文件路径
        :return: SwdReader 实例
        """
        reader = SwdReader()
        reader._file_path = file_path
        reader._file_size = os.path.getsize(file_path)

        if reader._file_size < SWD_HEADER_SIZE:
            raise ValueError(f"File too small for valid .swd: {reader._file_size} bytes")

        reader._fp = open(file_path, "rb")
        header_data = reader._fp.read(SWD_HEADER_SIZE)
        reader._header = SwdHeader.unpack(header_data)
        reader._record_count = compute_record_count(reader._file_size, reader._header.record_size)
        return reader

    @staticmethod
    def from_bytes(data: bytes) -> "SwdReader":
        """
        从 bytes 创建 reader（支持从 COS Range GET 响应直接构建）

        :param data: 完整的 .swd 文件内容（包含 header）
        :return: SwdReader 实例
        """
        reader = SwdReader()
        reader._data = data
        reader._file_size = len(data)

        if reader._file_size < SWD_HEADER_SIZE:
            raise ValueError(f"Data too small for valid .swd: {reader._file_size} bytes")

        header_data = data[:SWD_HEADER_SIZE]
        reader._header = SwdHeader.unpack(header_data)
        reader._record_count = compute_record_count(reader._file_size, reader._header.record_size)
        return reader

    def read_header(self) -> SwdHeader:
        """读取并返回文件头"""
        if self._header is None:
            raise RuntimeError("Reader not initialized")
        return self._header

    def read_record(self, index: int) -> Union[ScalarRecord, MediaIndexRecord]:
        """
        读取第 index 条记录（0-based）

        :param index: 记录索引
        :return: ScalarRecord 或 MediaIndexRecord
        """
        if index < 0 or index >= self._record_count:
            raise IndexError(f"Record index {index} out of range [0, {self._record_count})")

        offset = compute_record_offset(index, self._header.record_size)
        record_data = self._read_bytes(offset, self._header.record_size)
        return self._unpack_record(record_data)

    def read_range(self, start: int, end: int) -> List[Union[ScalarRecord, MediaIndexRecord]]:
        """
        读取 [start, end) 范围内的记录

        :param start: 起始索引（包含）
        :param end: 结束索引（不包含）
        :return: 记录列表
        """
        if start < 0:
            start = 0
        if end > self._record_count:
            end = self._record_count
        if start >= end:
            return []

        offset = compute_record_offset(start, self._header.record_size)
        total_bytes = (end - start) * self._header.record_size
        data = self._read_bytes(offset, total_bytes)

        records = []
        record_size = self._header.record_size
        for i in range(end - start):
            record_data = data[i * record_size:(i + 1) * record_size]
            records.append(self._unpack_record(record_data))
        return records

    def read_all(self) -> List[Union[ScalarRecord, MediaIndexRecord]]:
        """读取所有记录"""
        return self.read_range(0, self._record_count)

    def validate(self) -> bool:
        """
        验证文件完整性

        :return: True 表示文件完整，False 表示有截断
        """
        if self._header is None:
            return False
        return is_file_valid(self._file_size, self._header.record_size)

    def truncate_to_valid(self) -> int:
        """
        截断不完整的尾部记录（仅在文件模式下可用）

        :return: 截断的字节数
        """
        if self._fp is None:
            raise RuntimeError("truncate_to_valid() only available for file-based readers")

        data_size = self._file_size - SWD_HEADER_SIZE
        remainder = data_size % self._header.record_size
        if remainder == 0:
            return 0

        valid_size = SWD_HEADER_SIZE + (data_size // self._header.record_size) * self._header.record_size
        # 需要 r+b 模式才能 truncate
        self._fp.close()
        with open(self._file_path, "r+b") as fp:
            fp.truncate(valid_size)
        # 重新打开
        self._fp = open(self._file_path, "rb")
        truncated = self._file_size - valid_size
        self._file_size = valid_size
        self._record_count = compute_record_count(self._file_size, self._header.record_size)
        return truncated

    def _read_bytes(self, offset: int, length: int) -> bytes:
        """从文件或内存中读取指定位置的字节"""
        if self._data is not None:
            # bytes 模式
            return self._data[offset:offset + length]
        elif self._fp is not None:
            # 文件模式
            self._fp.seek(offset)
            data = self._fp.read(length)
            if len(data) != length:
                raise IOError(f"Expected to read {length} bytes at offset {offset}, got {len(data)}")
            return data
        else:
            raise RuntimeError("Reader not initialized")

    def _unpack_record(self, data: bytes) -> Union[ScalarRecord, MediaIndexRecord]:
        """根据数据类型解包记录"""
        if self._header.data_type == SWD_DTYPE_SCALAR:
            return ScalarRecord.unpack(data)
        elif self._header.data_type == SWD_DTYPE_MEDIA_REF:
            return MediaIndexRecord.unpack(data)
        else:
            raise ValueError(f"Unknown data type: {self._header.data_type}")

    def close(self):
        """关闭文件"""
        if self._fp is not None:
            self._fp.close()
            self._fp = None
        self._data = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __len__(self) -> int:
        return self._record_count

    def __getitem__(self, index: Union[int, slice]) -> Union[ScalarRecord, MediaIndexRecord, List]:
        if isinstance(index, slice):
            start, stop, step = index.indices(self._record_count)
            if step != 1:
                return [self.read_record(i) for i in range(start, stop, step)]
            return self.read_range(start, stop)
        if index < 0:
            index += self._record_count
        return self.read_record(index)
