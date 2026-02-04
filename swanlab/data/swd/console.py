"""
@file: console.py
@description: ConsoleLogAppender - 控制台日志追加器

支持本地文件追加和 COS AppendObject 纯文本日志上传。
日志以纯文本格式存储（每行一条），支持 AppendObject 模式追加。
"""

import os
from typing import Optional

from swanlab.log import swanlog


class ConsoleLogAppender:
    """
    控制台日志追加器

    将控制台日志以纯文本格式追加写入到本地文件和/或 COS。

    用法:
        appender = ConsoleLogAppender(local_path="console/output.log")
        appender.append("2025-07-01 14:30:22 [INFO] Training started\\n")
        appender.flush()
        appender.close()
    """

    def __init__(
        self,
        local_path: Optional[str] = None,
        cos_client=None,
        cos_key: Optional[str] = None,
    ):
        """
        :param local_path: 本地日志文件路径
        :param cos_client: CosClient 实例（可选，用于 COS 上传）
        :param cos_key: COS 上的对象键名（可选）
        """
        self._local_path = local_path
        self._cos_client = cos_client
        self._cos_key = cos_key
        self._fp = None
        self._cos_position = 0
        self._buffer = []
        self._buffer_size = 0
        # 缓冲区大小阈值（4KB），达到阈值时自动 flush 到 COS
        self._buffer_threshold = 4096

    def open(self):
        """打开本地日志文件"""
        if self._local_path is not None:
            os.makedirs(os.path.dirname(self._local_path), exist_ok=True)
            self._fp = open(self._local_path, "a", encoding="utf-8")

        # 获取 COS 上的当前位置
        if self._cos_client is not None and self._cos_key is not None:
            try:
                self._cos_position = self._cos_client.head_object_size(self._cos_key)
            except Exception:
                self._cos_position = 0

    def append(self, text: str):
        """
        追加一行日志

        :param text: 日志文本（应包含换行符）
        """
        # 写入本地文件
        if self._fp is not None:
            self._fp.write(text)
            self._fp.flush()

        # 缓冲到 COS 上传队列
        if self._cos_client is not None and self._cos_key is not None:
            encoded = text.encode("utf-8")
            self._buffer.append(encoded)
            self._buffer_size += len(encoded)

            # 达到阈值时自动 flush
            if self._buffer_size >= self._buffer_threshold:
                self.flush_to_cos()

    def flush_to_cos(self):
        """将缓冲区数据 flush 到 COS"""
        if not self._buffer or self._cos_client is None:
            return

        data = b"".join(self._buffer)
        try:
            self._cos_position = self._cos_client.append_object(
                key=self._cos_key,
                data=data,
                position=self._cos_position,
            )
            self._buffer.clear()
            self._buffer_size = 0
        except Exception as e:
            swanlog.debug(f"Failed to flush console log to COS: {e}")

    def flush(self):
        """刷新所有缓冲区"""
        if self._fp is not None:
            self._fp.flush()
        self.flush_to_cos()

    def close(self):
        """关闭日志追加器"""
        self.flush()
        if self._fp is not None:
            self._fp.close()
            self._fp = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
