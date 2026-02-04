"""
测试 COS 客户端和 Appender（使用 mock 对象存储）
"""

import os
import uuid
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from swanlab.core_python.cos.client import CosClient, CosConfig
from swanlab.core_python.cos.appender import CosAppender
from swanlab.core_python.cos.retry import retry_with_backoff
from swanlab.data.swd.format import SWD_HEADER_SIZE, SWD_DTYPE_SCALAR, SWD_SCALAR_RECORD_SIZE
from swanlab.data.swd import SwdHeader, ScalarRecord, SwdWriter


@pytest.fixture
def cos_config():
    return CosConfig(
        bucket="test-bucket",
        region="ap-shanghai",
        prefix="ws/proj/exp_123",
        presign_base_url="https://test-bucket.cos.ap-shanghai.myqcloud.com",
    )


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestRetryWithBackoff:
    """重试工具测试"""

    def test_success_first_try(self):
        fn = MagicMock(return_value=42)
        result = retry_with_backoff(fn, max_retries=3)
        assert result == 42
        assert fn.call_count == 1

    def test_success_after_retry(self):
        fn = MagicMock(side_effect=[ValueError("fail"), ValueError("fail"), 42])
        result = retry_with_backoff(fn, max_retries=3, base_delay=0.01)
        assert result == 42
        assert fn.call_count == 3

    def test_all_retries_fail(self):
        fn = MagicMock(side_effect=ValueError("always fails"))
        with pytest.raises(ValueError, match="always fails"):
            retry_with_backoff(fn, max_retries=2, base_delay=0.01)
        assert fn.call_count == 2

    def test_non_retryable_exception(self):
        fn = MagicMock(side_effect=TypeError("type error"))
        with pytest.raises(TypeError):
            retry_with_backoff(
                fn,
                max_retries=3,
                base_delay=0.01,
                retryable_exceptions=(ValueError,),
            )
        assert fn.call_count == 1


class TestCosClient:
    """COS 客户端测试"""

    def test_build_url_with_prefix(self, cos_config):
        client = CosClient(cos_config)
        url = client._build_url("m_000.swd")
        assert url == "https://test-bucket.cos.ap-shanghai.myqcloud.com/ws/proj/exp_123/m_000.swd"
        client.close()

    def test_build_url_without_prefix(self):
        config = CosConfig(presign_base_url="https://bucket.cos.example.com")
        client = CosClient(config)
        url = client._build_url("m_000.swd")
        assert url == "https://bucket.cos.example.com/m_000.swd"
        client.close()

    def test_update_credentials(self, cos_config):
        client = CosClient(cos_config)
        client.update_credentials(
            secret_id="new_id",
            secret_key="new_key",
            token="new_token",
            token_expiry=12345,
        )
        assert client.config.secret_id == "new_id"
        assert client.config.secret_key == "new_key"
        assert client.config.token == "new_token"
        client.close()

    @patch("swanlab.core_python.cos.client.requests.Session")
    def test_head_object_size_404(self, mock_session_cls, cos_config):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_session.head.return_value = mock_resp

        client = CosClient(cos_config)
        size = client.head_object_size("m_000.swd")
        assert size == 0
        client.close()

    @patch("swanlab.core_python.cos.client.requests.Session")
    def test_head_object_size_exists(self, mock_session_cls, cos_config):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "2464"}
        mock_resp.raise_for_status = MagicMock()
        mock_session.head.return_value = mock_resp

        client = CosClient(cos_config)
        size = client.head_object_size("m_000.swd")
        assert size == 2464
        client.close()

    def test_context_manager(self, cos_config):
        with CosClient(cos_config) as client:
            assert client.config.bucket == "test-bucket"


class TestCosAppender:
    """COS Appender 测试"""

    def test_create_with_header(self, cos_config):
        mock_client = MagicMock(spec=CosClient)
        mock_client.head_object_size.return_value = 0
        mock_client.append_object.return_value = SWD_HEADER_SIZE

        appender = CosAppender(mock_client, "m_000.swd")

        exp_id = uuid.uuid4().bytes
        header_bytes = SwdHeader.pack(
            data_type=SWD_DTYPE_SCALAR,
            record_size=SWD_SCALAR_RECORD_SIZE,
            experiment_id=exp_id,
            metric_id=0,
        )

        result = appender.create_with_header(header_bytes)
        assert result is True
        assert appender.initialized is True
        assert appender.remote_position == SWD_HEADER_SIZE

        mock_client.append_object.assert_called_once()

    def test_create_with_header_already_exists(self, cos_config):
        mock_client = MagicMock(spec=CosClient)
        mock_client.head_object_size.return_value = SWD_HEADER_SIZE + 100

        appender = CosAppender(mock_client, "m_000.swd")

        header_bytes = SwdHeader.pack(
            data_type=SWD_DTYPE_SCALAR,
            record_size=SWD_SCALAR_RECORD_SIZE,
            experiment_id=b'\x00' * 16,
            metric_id=0,
        )

        result = appender.create_with_header(header_bytes)
        assert result is False  # 文件已存在
        assert appender.initialized is True
        mock_client.append_object.assert_not_called()

    def test_append_success(self, cos_config):
        mock_client = MagicMock(spec=CosClient)
        mock_client.head_object_size.return_value = SWD_HEADER_SIZE
        mock_client.append_object.return_value = SWD_HEADER_SIZE + SWD_SCALAR_RECORD_SIZE

        appender = CosAppender(mock_client, "m_000.swd")
        appender._initialized = True
        appender._remote_position = SWD_HEADER_SIZE

        record_bytes = ScalarRecord.pack(0, 1.5, 1000000)
        result = appender.append(record_bytes)
        assert result is True

    def test_append_not_initialized(self):
        mock_client = MagicMock(spec=CosClient)
        appender = CosAppender(mock_client, "m_000.swd")

        with pytest.raises(RuntimeError, match="not initialized"):
            appender.append(b'\x00' * 24)

    def test_append_empty_data(self):
        mock_client = MagicMock(spec=CosClient)
        appender = CosAppender(mock_client, "m_000.swd")
        appender._initialized = True
        assert appender.append(b"") is False

    def test_invalid_header_size(self, cos_config):
        mock_client = MagicMock(spec=CosClient)
        appender = CosAppender(mock_client, "m_000.swd")

        with pytest.raises(ValueError, match="Header must be 128 bytes"):
            appender.create_with_header(b'\x00' * 32)

    def test_sync_from_local(self, cos_config, tmp_dir):
        """测试从本地文件同步到远端"""
        mock_client = MagicMock(spec=CosClient)
        # 远端不存在
        mock_client.head_object_size.return_value = 0
        mock_client.append_object.side_effect = [SWD_HEADER_SIZE, SWD_HEADER_SIZE + 240]

        appender = CosAppender(mock_client, "m_000.swd")

        # 创建本地 .swd 文件
        path = os.path.join(tmp_dir, "m_000.swd")
        exp_id = uuid.uuid4().bytes
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        for i in range(10):
            writer.append_scalar(i, float(i), 0)
        writer.close()

        transferred = appender.sync_from_local(path)
        assert transferred > 0

    def test_sync_from_local_already_synced(self, cos_config, tmp_dir):
        """远端大小 >= 本地大小，不需要同步"""
        mock_client = MagicMock(spec=CosClient)

        path = os.path.join(tmp_dir, "m_000.swd")
        exp_id = uuid.uuid4().bytes
        writer = SwdWriter(path, SWD_DTYPE_SCALAR, exp_id, metric_id=0)
        writer.open()
        writer.append_scalar(0, 1.0, 0)
        writer.close()

        local_size = os.path.getsize(path)
        mock_client.head_object_size.return_value = local_size

        appender = CosAppender(mock_client, "m_000.swd")
        transferred = appender.sync_from_local(path)
        assert transferred == 0
