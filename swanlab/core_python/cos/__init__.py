"""
@file: __init__.py
@description: COS 抽象层导出

可插拔后端支持 COS/OSS/S3 Express/MinIO
"""

from .client import CosClient, CosConfig
from .appender import CosAppender
from .retry import retry_with_backoff

__all__ = [
    "CosClient",
    "CosConfig",
    "CosAppender",
    "retry_with_backoff",
]
