#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
@DATE: 2023-11-30 20:47:18
@File: swanlab\server\api\data.py
@IDE: vscode
@Description:
    本文件用于处理数据相关的请求，包括获取数据，新建图表等
"""
import random
from fastapi.responses import JSONResponse
from fastapi import APIRouter

router = APIRouter()


# 测试路由，每次请求返回一个0到30的随机数
@router.get("/api/test")
async def _():
    # 生成一个 0 到 30 之间的随机整数
    random_number = random.randint(0, 30)
    return JSONResponse({"data": random_number}, status_code=200, headers={"Access-Control-Allow-Origin": "*"})
