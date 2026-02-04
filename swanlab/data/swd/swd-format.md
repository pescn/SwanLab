# .swd 二进制格式规格 (SwanLab Data)

> 版本: 1 | 字节序: Little-Endian

## 概述

`.swd` 是 SwanLab 的定长记录二进制文件格式，用于存储实验指标数据。每个 `.swd` 文件包含一个固定大小的 **Header** 和零到多条等长的 **Record**，文件内仅存储**单一数据类型**。

```
┌──────────────────────────────────┐
│         Header (128 bytes)       │  ← 创建时一次性写入
├──────────────────────────────────┤
│         Record 0                 │  ← 偏移 0x80
├──────────────────────────────────┤
│         Record 1                 │
├──────────────────────────────────┤
│         ...                      │
├──────────────────────────────────┤
│         Record N-1               │
└──────────────────────────────────┘
```

文件大小 = `128 + N × record_size`

---

## 设计目标

| 目标 | 实现方式 |
|------|----------|
| O(1) 随机访问 | 定长记录，第 N 条偏移量 = `128 + N × record_size` |
| 一次 HEAD 推导记录数 | `record_count = (file_size - 128) / record_size` |
| HTTP Range 增量读取 | `Range: bytes=(128 + N × rs)-(128 + (N+1) × rs - 1)` |
| 对象存储追加写 | 与 COS/S3 AppendObject 语义天然兼容 |
| 崩溃恢复 | `(file_size - 128) % record_size ≠ 0` → 截断尾部即可 |

---

## 1. Magic 标识

```
0x3A 0x53 0x57 0x44  →  ":SWD"
```

所有 `.swd` 文件以 `:SWD` 开头，用于快速识别文件类型。类比现有 DataStore 格式的 `:SWL`。

---

## 2. Header

**总大小: 128 字节**

struct 格式 (Python): `<4sHHHHIq16sI84s`

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| `0x00` | 4 B | `bytes` | **Magic** | 固定 `":SWD"` (`0x3A534744`) |
| `0x04` | 2 B | `uint16` | **Version** | 格式版本，当前为 `1` |
| `0x06` | 2 B | `uint16` | **Flags** | 预留标志位，当前为 `0` |
| `0x08` | 2 B | `uint16` | **DataType** | 数据类型 (见 §2.1) |
| `0x0A` | 2 B | `uint16` | **RecordSize** | 每条记录的字节数 (见 §2.1) |
| `0x0C` | 4 B | `uint32` | **Sequence** | 分片序号，单文件时为 `0` |
| `0x10` | 8 B | `int64` | **StartIndex** | 本文件第一条记录的全局索引 |
| `0x18` | 16 B | `bytes` | **ExperimentID** | 实验 UUID 原始字节，不足 16 字节右侧补 `\x00` |
| `0x28` | 4 B | `uint32` | **MetricID** | 指标 ID，对应 Manifest 中的 `id` |
| `0x2C` | 84 B | `bytes` | **Reserved** | 预留区域，全部为 `\x00` |

### 2.1 DataType 枚举

| 值 | 名称 | RecordSize | 说明 |
|----|------|------------|------|
| `1` | `SWD_DTYPE_SCALAR` | 24 | float64 标量数据 |
| `2` | `SWD_DTYPE_MEDIA_REF` | 80 | 媒体索引引用 |

### 2.2 Header 校验规则

- **Magic** 必须为 `b":SWD"`，否则拒绝读取
- **ExperimentID** 长度不得超过 16 字节
- 重新打开已有文件时，**DataType** 必须与预期一致

### 2.3 Reserved 区域

84 字节预留空间，供未来版本扩展使用，可能的用途包括:

- 文件级校验和 (CRC32/xxHash)
- 压缩算法标记
- 加密参数
- 降采样元数据

当前版本全部填充 `\x00`，读取时忽略。

---

## 3. Scalar Record (标量记录)

**大小: 24 字节** | DataType = `1`

struct 格式: `<qdq`

```
┌──────────────┬──────────────┬──────────────┐
│     step     │    value     │  timestamp   │
│    8 bytes   │   8 bytes    │   8 bytes    │
│    int64     │   float64    │    int64     │
└──────────────┴──────────────┴──────────────┘
 0x00           0x08           0x10
```

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| `0x00` | 8 B | `int64` | **step** | 训练步数，支持负数 |
| `0x08` | 8 B | `float64` | **value** | 标量值 (IEEE 754)，支持 `NaN`、`±Inf` |
| `0x10` | 8 B | `int64` | **timestamp** | 写入时间，epoch 微秒 |

### 容量估算

| 记录数 | 数据大小 | 文件总大小 (含 Header) |
|--------|----------|----------------------|
| 1,000 | 24 KB | ~24.1 KB |
| 100,000 | 2.4 MB | ~2.4 MB |
| 1,000,000 | 24 MB | ~24 MB |
| 100,000,000 | 2.4 GB | ~2.4 GB |

---

## 4. Media Index Record (媒体索引记录)

**大小: 80 字节** | DataType = `2`

struct 格式: `<qq48sBHHI7s`

```
┌────────┬──────────┬──────────────────────────────┬───────┬───────┬────────┬──────────┬──────────┐
│  step  │timestamp │         object_key           │media  │ width │ height │file_size │ reserved │
│ 8 B    │  8 B     │          48 B                │type   │  2 B  │  2 B   │   4 B    │   7 B    │
│ int64  │  int64   │    bytes (null-padded)        │ uint8 │uint16 │ uint16 │  uint32  │  bytes   │
└────────┴──────────┴──────────────────────────────┴───────┴───────┴────────┴──────────┴──────────┘
 0x00     0x08       0x10                           0x40    0x41    0x43     0x45       0x49
```

| 偏移 | 大小 | 类型 | 字段 | 说明 |
|------|------|------|------|------|
| `0x00` | 8 B | `int64` | **step** | 训练步数 |
| `0x08` | 8 B | `int64` | **timestamp** | 写入时间，epoch 微秒 |
| `0x10` | 48 B | `bytes` | **object_key** | 对象存储 key (UTF-8，右侧 `\x00` 填充) |
| `0x40` | 1 B | `uint8` | **media_type** | 媒体类型 (见 §4.1) |
| `0x41` | 2 B | `uint16` | **width** | 宽度 (像素)，非图片/视频为 `0` |
| `0x43` | 2 B | `uint16` | **height** | 高度 (像素)，非图片/视频为 `0` |
| `0x45` | 4 B | `uint32` | **file_size** | 媒体文件大小 (字节)，上限 ~4 GB |
| `0x49` | 7 B | `bytes` | **reserved** | 预留，全部为 `\x00` |

> **注意**: 媒体索引记录存储的是**索引信息**（指向实际媒体文件的引用），不是媒体文件本身。实际文件通过 Manifest 中的 `media_prefix` + `object_key` 拼接为完整存储路径。

### 4.1 media_type 枚举

| 值 | 名称 | 说明 |
|----|------|------|
| `0` | `IMAGE` | 图片 (PNG/JPEG/...) |
| `1` | `AUDIO` | 音频 |
| `2` | `TEXT` | 文本 |
| `3` | `VIDEO` | 视频 |
| `4` | `OBJECT3D` | 3D 对象 |

### 4.2 object_key 编码

- 最大 48 字节 (UTF-8 编码后)
- 不足 48 字节时右侧用 `\x00` 填充
- 读取时通过 `rstrip(b'\x00').decode('utf-8')` 还原
- 典型值: `"a1b2c3d4.png"`、`"step_100_img_0.jpg"`

---

## 5. 文件命名与目录结构

每个指标对应一个 `.swd` 文件，命名规则: `m_{metric_id:03d}.swd`

```
run-20250701_143022-abc123/
├── manifest.json                 # 指标注册表 (JSON)
├── backup.swanlab                # 非指标元数据
├── data/                         # .swd 数据文件
│   ├── m_000.swd                 # metric_id=0  "loss"      (Scalar)
│   ├── m_001.swd                 # metric_id=1  "accuracy"  (Scalar)
│   └── m_002.swd                 # metric_id=2  "images"    (MediaRef)
├── media/                        # 实际媒体文件
│   └── m_002/
│       ├── a1b2c3d4.png
│       └── e5f6g7h8.png
└── console/                      # 控制台日志 (纯文本)
```

---

## 6. Manifest

`manifest.json` 是 `.swd` 文件的元数据注册表。通过原子写入 (先写临时文件，再 `os.replace()`) 保证一致性。

```json
{
  "version": 1,
  "experiment_id": "exp_abc123",
  "status": "running",
  "metrics": {
    "loss": {
      "id": 0,
      "dtype": "float64",
      "record_size": 24,
      "files": [
        { "name": "m_000.swd", "records": 100000, "bytes": 2400128 }
      ]
    },
    "train_images": {
      "id": 2,
      "dtype": "media_ref",
      "record_size": 80,
      "media_prefix": "media/m_002/",
      "files": [
        { "name": "m_002.swd", "records": 5000 }
      ]
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `version` | Manifest 格式版本，当前为 `1` |
| `experiment_id` | 实验 ID 字符串 |
| `status` | `"running"` / `"completed"` / `"crashed"` / `"stopped"` |
| `metrics.*.id` | 指标数字 ID，对应文件名 `m_{id:03d}.swd` |
| `metrics.*.dtype` | `"float64"` 或 `"media_ref"` |
| `metrics.*.record_size` | 记录大小 (字节)，冗余校验用 |
| `metrics.*.media_prefix` | 媒体文件路径前缀 (仅 `media_ref` 类型) |
| `metrics.*.files[].name` | `.swd` 文件名 |
| `metrics.*.files[].records` | 当前记录数 |
| `metrics.*.files[].bytes` | 当前文件字节数 (可选) |

---

## 7. 关键操作

### 7.1 计算公式

```
record_count   = (file_size - 128) / record_size
record_offset  = 128 + index × record_size
is_valid       = (file_size - 128) % record_size == 0
```

### 7.2 HTTP Range 读取

```http
# 读取第 N 条 Scalar Record
Range: bytes=(128 + N*24)-(128 + (N+1)*24 - 1)

# 读取第 10~19 条 (共 10 条)
Range: bytes=(128 + 10*24)-(128 + 20*24 - 1)

# 即
Range: bytes=368-607
```

### 7.3 崩溃恢复

```
file_size = 128 + 99 × 24 + 13    (最后一条记录不完整)

valid_records = (file_size - 128) // 24 = 99
valid_size    = 128 + 99 × 24 = 2504

→ truncate(2504)                    截断 13 字节碎片，恢复为有效文件
```

### 7.4 分片 (>5 GB)

当单文件超过 5 GB 时，可创建新分片:

```
m_000.swd       Sequence=0, StartIndex=0,        records=200000000
m_000_001.swd   Sequence=1, StartIndex=200000000, records=...
```

---

## 8. 对象存储映射

在云端模式下，`.swd` 文件通过 **AppendObject** 追加写入对象存储:

```
cos://bucket/{workspace}/{project}/{experiment_id}/
├── manifest.json          ← PUT 覆盖写
├── m_000.swd              ← AppendObject 追加写
├── m_001.swd              ← AppendObject 追加写
├── m_002.swd              ← AppendObject 追加写
├── media/m_002/
│   └── *.png              ← PUT (预签名 URL)
└── console.log            ← AppendObject (纯文本)
```

### 写入流程

1. **创建**: `AppendObject(position=0, data=header_128bytes)` — 写入 Header
2. **追加**: `AppendObject(position=current_size, data=record_bytes)` — 追加记录
3. **校验**: 每次追加前 `HEAD` 获取远端 size，与本地预期 position 对比

### 增量同步 (前端)

```
1. HEAD m_000.swd → Content-Length → record_count
2. Range GET 全部或最近 N 条
3. 轮询 HEAD (每 3~5 秒)，文件变大 → Range GET 仅新增部分
4. 浏览器缓存已读取的字节范围
```

---

## 9. 与现有格式对比

| | .swd | JSONL (.log) | backup (.swanlab) |
|---|---|---|---|
| 结构 | 128B Header + 定长记录 | 变长 JSON 行 | LevelDB 风格 Block |
| Magic | `:SWD` | 无 | `:SWL` |
| 单条标量大小 | 24 B | ~50-200 B | ~100-500 B |
| 随机访问 | O(1) | O(n) | O(n) |
| HTTP 增量读取 | Range GET | 不支持 | 不支持 |
| 类型安全 | struct 强类型 | JSON 弱类型 | Protobuf |
| 崩溃恢复 | 截断尾部 | 丢弃末行 | Block checksum |
| 适用场景 | 高频数据点 | 通用日志 | 元数据+全量 |

---

## 10. 实现参考

| 模块 | 文件 | 职责 |
|------|------|------|
| 格式定义 | `swanlab/data/swd/format.py` | 常量、struct 格式、NamedTuple |
| 写入 | `swanlab/data/swd/writer.py` | SwdWriter |
| 读取 | `swanlab/data/swd/reader.py` | SwdReader (文件 + bytes 双模式) |
| 注册表 | `swanlab/data/swd/manifest.py` | ManifestManager |
| 格式转换 | `swanlab/data/swd/converter.py` | JSONL↔SWD、SWD→CSV/Parquet |
| 控制台 | `swanlab/data/swd/console.py` | ConsoleLogAppender |
| COS 上传 | `swanlab/core_python/cos/appender.py` | CosAppender |
