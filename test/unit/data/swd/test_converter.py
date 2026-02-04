"""
测试 SwdExporter 和 JsonlToSwdConverter
"""

import csv
import json
import os
import uuid
import tempfile
import pytest

from swanlab.data.swd.format import SWD_DTYPE_SCALAR, SWD_SCALAR_RECORD_SIZE
from swanlab.data.swd.writer import SwdWriter
from swanlab.data.swd.reader import SwdReader
from swanlab.data.swd.converter import JsonlToSwdConverter, SwdExporter


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def exp_id():
    return uuid.uuid4().bytes


class TestJsonlToSwdConverter:
    """JSONL → .swd 转换测试"""

    def test_convert_scalar_jsonl(self, tmp_dir, exp_id):
        # 创建 JSONL 输入
        jsonl_path = os.path.join(tmp_dir, "input.log")
        with open(jsonl_path, "w") as f:
            for i in range(50):
                f.write(json.dumps({"index": i, "data": float(i) * 0.1}) + "\n")

        swd_path = os.path.join(tmp_dir, "output.swd")
        converter = JsonlToSwdConverter(jsonl_path, swd_path, exp_id, metric_id=0)
        count = converter.convert()

        assert count == 50
        assert os.path.exists(swd_path)

        reader = SwdReader.from_file(swd_path)
        assert reader.record_count == 50
        assert reader.data_type == SWD_DTYPE_SCALAR
        # 验证数据
        record = reader.read_record(10)
        assert record.step == 10
        assert record.value == pytest.approx(1.0)
        reader.close()

    def test_convert_empty_jsonl(self, tmp_dir, exp_id):
        jsonl_path = os.path.join(tmp_dir, "empty.log")
        with open(jsonl_path, "w") as f:
            pass  # 空文件

        swd_path = os.path.join(tmp_dir, "output.swd")
        converter = JsonlToSwdConverter(jsonl_path, swd_path, exp_id, metric_id=0)
        count = converter.convert()

        assert count == 0

    def test_convert_with_invalid_lines(self, tmp_dir, exp_id):
        jsonl_path = os.path.join(tmp_dir, "mixed.log")
        with open(jsonl_path, "w") as f:
            f.write(json.dumps({"index": 0, "data": 1.0}) + "\n")
            f.write("invalid json line\n")
            f.write(json.dumps({"index": 1, "data": "not a number"}) + "\n")
            f.write(json.dumps({"index": 2, "data": 3.0}) + "\n")

        swd_path = os.path.join(tmp_dir, "output.swd")
        converter = JsonlToSwdConverter(jsonl_path, swd_path, exp_id, metric_id=0)
        count = converter.convert()

        # 只有 2 条有效记录
        assert count == 2


class TestSwdExporter:
    """导出测试"""

    def _create_test_swd(self, path, exp_id, n=50):
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        for i in range(n):
            writer.append_scalar(step=i, value=float(i) * 0.5, timestamp=i * 1000000)
        writer.close()

    def test_to_csv(self, tmp_dir, exp_id):
        swd_path = os.path.join(tmp_dir, "test.swd")
        self._create_test_swd(swd_path, exp_id, n=20)

        csv_path = os.path.join(tmp_dir, "output.csv")
        exporter = SwdExporter(swd_path)
        exporter.to_csv(csv_path)

        assert os.path.exists(csv_path)
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert rows[0] == ["step", "value", "timestamp"]
        assert len(rows) == 21  # header + 20 records
        assert rows[1][0] == "0"
        assert float(rows[1][1]) == 0.0

    def test_to_csv_no_header(self, tmp_dir, exp_id):
        swd_path = os.path.join(tmp_dir, "test.swd")
        self._create_test_swd(swd_path, exp_id, n=5)

        csv_path = os.path.join(tmp_dir, "output.csv")
        exporter = SwdExporter(swd_path)
        exporter.to_csv(csv_path, include_header=False)

        with open(csv_path, "r") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 5  # no header

    def test_to_jsonl(self, tmp_dir, exp_id):
        swd_path = os.path.join(tmp_dir, "test.swd")
        self._create_test_swd(swd_path, exp_id, n=10)

        jsonl_path = os.path.join(tmp_dir, "output.jsonl")
        exporter = SwdExporter(swd_path)
        exporter.to_jsonl(jsonl_path)

        assert os.path.exists(jsonl_path)
        with open(jsonl_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 10

        first = json.loads(lines[0])
        assert first["step"] == 0
        assert first["value"] == 0.0
        assert first["timestamp"] == 0

        last = json.loads(lines[9])
        assert last["step"] == 9
        assert last["value"] == pytest.approx(4.5)

    def test_roundtrip_jsonl(self, tmp_dir, exp_id):
        """完整往返：.swd → JSONL → .swd"""
        # 原始 .swd
        swd_path = os.path.join(tmp_dir, "original.swd")
        self._create_test_swd(swd_path, exp_id, n=100)

        # 导出到 JSONL
        jsonl_path = os.path.join(tmp_dir, "exported.jsonl")
        SwdExporter(swd_path).to_jsonl(jsonl_path)

        # 从 JSONL 转回 .swd
        swd2_path = os.path.join(tmp_dir, "converted.swd")
        count = JsonlToSwdConverter(jsonl_path, swd2_path, exp_id, metric_id=0).convert()
        assert count == 100

        # 验证数据一致
        reader1 = SwdReader.from_file(swd_path)
        reader2 = SwdReader.from_file(swd2_path)
        assert reader1.record_count == reader2.record_count

        for i in [0, 50, 99]:
            r1 = reader1.read_record(i)
            r2 = reader2.read_record(i)
            assert r1.step == r2.step
            assert r1.value == pytest.approx(r2.value)

        reader1.close()
        reader2.close()
