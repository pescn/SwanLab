"""
@file: appender.py
@description: CosAppender - 管理单个 .swd 文件的 AppendObject 操作

负责:
- 创建远端 .swd 文件（写入 header）
- 追加记录数据（带 position 校验）
- HEAD 请求推导远端 size
- 进程重启时从差异处续传
"""

from typing import Optional

from swanlab.data.swd.format import SWD_HEADER_SIZE
from swanlab.log import swanlog
from .client import CosClient


class CosAppender:
    """
    管理单个 .swd 文件的 COS AppendObject 操作

    用法:
        appender = CosAppender(cos_client, object_key="m_000.swd")
        appender.create_with_header(header_bytes)
        appender.append(record_bytes)

    容错:
        - 重启后调用 get_remote_size() 获取远端实际大小
        - 通过 position 校验确保数据一致性
        - 如果远端已有数据，跳过重复的 append
    """

    def __init__(
        self,
        cos_client: CosClient,
        object_key: str,
        presigned_url: Optional[str] = None,
    ):
        """
        :param cos_client: COS 客户端
        :param object_key: 对象键名 (e.g., "m_000.swd")
        :param presigned_url: 预签名 URL（可选）
        """
        self._client = cos_client
        self._object_key = object_key
        self._presigned_url = presigned_url
        # 内存中跟踪的远端位置
        self._remote_position: int = 0
        self._initialized = False

    @property
    def object_key(self) -> str:
        return self._object_key

    @property
    def remote_position(self) -> int:
        return self._remote_position

    @property
    def initialized(self) -> bool:
        return self._initialized

    def get_remote_size(self) -> int:
        """
        HEAD 请求获取远端对象大小

        :return: 远端对象大小（bytes），不存在时返回 0
        """
        size = self._client.head_object_size(
            key=self._object_key,
            presigned_url=self._presigned_url,
        )
        self._remote_position = size
        return size

    def create_with_header(self, header_bytes: bytes) -> bool:
        """
        在远端创建 .swd 文件（写入 header）

        :param header_bytes: 64 字节的 header 数据
        :return: True 表示成功创建，False 表示文件已存在
        """
        if len(header_bytes) != SWD_HEADER_SIZE:
            raise ValueError(f"Header must be {SWD_HEADER_SIZE} bytes, got {len(header_bytes)}")

        # 检查远端是否已存在
        remote_size = self.get_remote_size()
        if remote_size > 0:
            swanlog.debug(
                f"Remote file {self._object_key} already exists ({remote_size} bytes), skipping header creation"
            )
            self._initialized = True
            return False

        # 创建新文件并写入 header
        try:
            self._remote_position = self._client.append_object(
                key=self._object_key,
                data=header_bytes,
                position=0,
                presigned_url=self._presigned_url,
            )
            self._initialized = True
            return True
        except Exception as e:
            swanlog.warning(f"Failed to create remote .swd file {self._object_key}: {e}")
            raise

    def append(self, data: bytes) -> bool:
        """
        追加数据到远端 .swd 文件

        :param data: 要追加的记录数据
        :return: True 表示成功，False 表示跳过（数据已存在）
        """
        if not self._initialized:
            raise RuntimeError("CosAppender not initialized, call create_with_header() first")

        if not data:
            return False

        try:
            # 获取远端实际大小以做 position 校验
            remote_size = self.get_remote_size()
            expected_position = remote_size

            # 如果远端大小已包含该数据，说明之前的重试已成功
            if remote_size >= self._remote_position + len(data):
                swanlog.debug(
                    f"Data already exists at remote for {self._object_key}, "
                    f"remote_size={remote_size}, skipping"
                )
                self._remote_position = remote_size
                return False

            self._remote_position = self._client.append_object(
                key=self._object_key,
                data=data,
                position=expected_position,
                presigned_url=self._presigned_url,
            )
            return True
        except Exception as e:
            swanlog.debug(f"Failed to append to {self._object_key}: {e}")
            raise

    def sync_from_local(self, local_file_path: str) -> int:
        """
        从本地 .swd 文件同步到远端（用于进程重启后续传）

        读取本地 .swd 文件，对比远端大小，从差异处续传。

        :param local_file_path: 本地 .swd 文件路径
        :return: 传输的字节数
        """
        import os

        local_size = os.path.getsize(local_file_path)
        remote_size = self.get_remote_size()

        if remote_size >= local_size:
            swanlog.debug(
                f"Remote {self._object_key} ({remote_size} bytes) >= local ({local_size} bytes), "
                f"no sync needed"
            )
            return 0

        # 从远端 size 处开始读取本地文件并上传
        with open(local_file_path, "rb") as f:
            if remote_size == 0:
                # 远端不存在，上传全部内容
                data = f.read()
            else:
                # 从差异处开始读
                f.seek(remote_size)
                data = f.read()

            if not data:
                return 0

            position = remote_size
            if remote_size == 0:
                # 新文件：先创建 header，再追加数据
                self.create_with_header(data[:SWD_HEADER_SIZE])
                data = data[SWD_HEADER_SIZE:]
                position = SWD_HEADER_SIZE
                if not data:
                    return SWD_HEADER_SIZE

            self._remote_position = self._client.append_object(
                key=self._object_key,
                data=data,
                position=position,
                presigned_url=self._presigned_url,
            )
            transferred = len(data) + (SWD_HEADER_SIZE if remote_size == 0 else 0)
            swanlog.debug(f"Synced {transferred} bytes to {self._object_key}")
            return transferred
