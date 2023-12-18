#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
@DATE: 2023-11-30 01:39:04
@File: swanlab\cli\main.py
@IDE: vscode
@Description:
    swanlab脚本命令的主入口
"""

import click
from .utils import is_vaild_ip, is_available_port


@click.group()
def cli():
    pass


@cli.command()
# 控制服务发布的ip地址
@click.option(
    "--host",
    "-h",
    default="127.0.0.1",
    type=str,
    help="The host of swanlab web, default by 127.0.0.1",
    callback=is_vaild_ip,
)
# 控制服务发布的端口，默认5092
@click.option(
    "--port",
    "-p",
    default=5092,
    type=int,
    help="The port of swanlab web, default by 5092",
)
# 日志等级
@click.option(
    "--log-level",
    default="info",
    type=click.Choice(["debug", "info", "warning", "error", "critical"]),
    help="The level of log, default by info; You can choose one of [debug, info, warning, error, critical]",
)
def watch(log_level: str, host: tuple, port: int):
    """Run this command to turn on the swanlab service."""
    # 导入必要的模块
    from ..log import swanlog as swl
    from ..server import app
    import uvicorn

    # ---------------------------------- 日志等级处理 ----------------------------------
    swl.setLevel(log_level)
    # ---------------------------------- 服务地址处理 ----------------------------------
    # 拿到当前本机可用的所有ip地址
    ip, ipv4 = host
    ips = [f"http://{ip}:{port}" for ip in ipv4]
    # 判断ip:port是否被占用
    is_available_port(ip, port)
    # ---------------------------------- 日志打印 ----------------------------------
    if ip == "0.0.0.0":
        # 检查每个ip地址的端口占用情况
        swl.info(f"SwanLab Experiment Dashboard running...")
        swl.info(f"Available on: \n" + "\n".join(ips))
    else:
        swl.info(f"SwanLab Experiment Dashboard running on \033[1mhttp://{ip}:{port}\033[0m")
    # ---------------------------------- 启动服务 ----------------------------------

    # 使用 uvicorn 启动 FastAPI 应用，关闭原生日志
    uvicorn.run(app, host=ip, port=port, log_level="critical")


if __name__ == "__main__":
    watch()