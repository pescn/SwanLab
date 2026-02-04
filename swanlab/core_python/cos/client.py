"""
@file: client.py
@description: CosClient - 对象存储客户端抽象

可插拔后端支持 COS/OSS/S3 Express/MinIO。
目前实现基于 HTTP 预签名 URL 的方式，与 SwanLab 后端配合使用。
"""

import requests
from io import BytesIO
from typing import Optional, Dict, Any

from pydantic import BaseModel

from swanlab.log import swanlog
from .retry import retry_with_backoff


class CosConfig(BaseModel):
    """COS 配置"""
    bucket: str = ""
    region: str = ""
    prefix: str = ""
    # 预签名 URL 基础地址（由后端提供）
    presign_base_url: str = ""
    # 临时凭证（可选，用于直接 SDK 调用）
    secret_id: Optional[str] = None
    secret_key: Optional[str] = None
    token: Optional[str] = None
    token_expiry: Optional[int] = None


class CosClient:
    """
    COS 对象存储客户端

    支持两种模式:
    1. 预签名 URL 模式：通过后端获取预签名 URL，直接 PUT/GET
    2. SDK 直接模式：使用临时凭证直接调用 COS SDK（未来扩展）

    用法:
        client = CosClient(config)
        client.append_object(key="m_000.swd", data=b"...", position=64)
        size = client.head_object_size(key="m_000.swd")
        url = client.generate_presigned_url(key="m_000.swd", method="GET")
    """

    def __init__(self, config: CosConfig):
        self._config = config
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/octet-stream",
        })

    @property
    def config(self) -> CosConfig:
        return self._config

    def update_credentials(
        self,
        secret_id: str,
        secret_key: str,
        token: str,
        token_expiry: int,
    ):
        """更新临时凭证"""
        self._config.secret_id = secret_id
        self._config.secret_key = secret_key
        self._config.token = token
        self._config.token_expiry = token_expiry

    def append_object(
        self,
        key: str,
        data: bytes,
        position: int,
        presigned_url: Optional[str] = None,
        max_retries: int = 3,
    ) -> int:
        """
        追加写入对象存储

        :param key: 对象键名
        :param data: 要追加的数据
        :param position: 追加的起始位置（用于 position 校验）
        :param presigned_url: 预签名 URL（如果提供则直接使用）
        :param max_retries: 最大重试次数
        :return: 追加后的新位置（position + len(data)）
        """
        url = presigned_url or self._build_url(key)

        def _do_append():
            resp = self._session.put(
                url,
                data=data,
                headers={
                    "Content-Type": "application/octet-stream",
                    "x-cos-append-position": str(position),
                },
                timeout=30,
            )
            resp.raise_for_status()
            return position + len(data)

        return retry_with_backoff(
            _do_append,
            max_retries=max_retries,
            retryable_exceptions=(requests.RequestException,),
            operation_name=f"COS append {key}",
        )

    def put_object(
        self,
        key: str,
        data: bytes,
        presigned_url: Optional[str] = None,
        max_retries: int = 3,
    ):
        """
        覆盖写入对象（用于 manifest.json 等非追加文件）

        :param key: 对象键名
        :param data: 数据
        :param presigned_url: 预签名 URL
        :param max_retries: 最大重试次数
        """
        url = presigned_url or self._build_url(key)

        def _do_put():
            resp = self._session.put(
                url,
                data=data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=30,
            )
            resp.raise_for_status()

        retry_with_backoff(
            _do_put,
            max_retries=max_retries,
            retryable_exceptions=(requests.RequestException,),
            operation_name=f"COS put {key}",
        )

    def head_object_size(
        self,
        key: str,
        presigned_url: Optional[str] = None,
    ) -> int:
        """
        获取对象大小（HEAD 请求）

        :param key: 对象键名
        :param presigned_url: 预签名 URL
        :return: 对象大小（bytes），不存在时返回 0
        """
        url = presigned_url or self._build_url(key)
        try:
            resp = self._session.head(url, timeout=10)
            if resp.status_code == 404:
                return 0
            resp.raise_for_status()
            return int(resp.headers.get("Content-Length", 0))
        except requests.RequestException as e:
            swanlog.debug(f"HEAD request failed for {key}: {e}")
            return 0

    def get_object_range(
        self,
        key: str,
        start: int,
        end: int,
        presigned_url: Optional[str] = None,
    ) -> bytes:
        """
        Range GET 读取对象的部分内容

        :param key: 对象键名
        :param start: 起始字节（包含）
        :param end: 结束字节（包含）
        :param presigned_url: 预签名 URL
        :return: 读取的字节数据
        """
        url = presigned_url or self._build_url(key)
        resp = self._session.get(
            url,
            headers={"Range": f"bytes={start}-{end}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content

    def generate_presigned_url(
        self,
        key: str,
        method: str = "GET",
        expires: int = 3600,
    ) -> str:
        """
        生成预签名 URL（需要后端支持）

        :param key: 对象键名
        :param method: HTTP 方法
        :param expires: 有效期（秒）
        :return: 预签名 URL
        """
        # 此方法需要与后端 API 配合实现
        # 暂时返回基础 URL
        return self._build_url(key)

    def _build_url(self, key: str) -> str:
        """构建对象 URL"""
        prefix = self._config.prefix.rstrip("/")
        base = self._config.presign_base_url.rstrip("/")
        if prefix:
            return f"{base}/{prefix}/{key}"
        return f"{base}/{key}"

    def close(self):
        """关闭 HTTP 会话"""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
