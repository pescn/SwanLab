"""
@file: retry.py
@description: 指数退避重试工具

复用 api/service.py 中 upload_file 的重试模式：
- 指数退避：2^(attempt-1) 秒 (1s, 2s, 4s, ...)
- 可配置最大重试次数
- 支持异常过滤
"""

import time
from typing import Callable, TypeVar, Optional, Type, Tuple

from swanlab.log import swanlog

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    operation_name: str = "operation",
) -> T:
    """
    执行带指数退避重试的操作

    :param fn: 要执行的函数（无参数）
    :param max_retries: 最大重试次数
    :param base_delay: 基础延迟（秒）
    :param max_delay: 最大延迟（秒）
    :param retryable_exceptions: 可重试的异常类型元组
    :param operation_name: 操作名称（用于日志）
    :return: fn 的返回值
    :raises: fn 抛出的最后一个异常（如果所有重试都失败）
    """
    last_exception: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            last_exception = e
            if attempt == max_retries:
                swanlog.warning(
                    f"{operation_name} failed after {max_retries} attempts: {e}"
                )
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            swanlog.debug(
                f"{operation_name} attempt {attempt}/{max_retries} failed: {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            time.sleep(delay)
    # 不应该到达这里
    raise last_exception  # type: ignore
