# Runway Closure Planner（跑道封闭窗口归并服务）

纯后端 FastAPI 服务：接收某条跑道在一个查询时段内、由多个承包组分别提交的封闭窗口，
返回**裁剪并归并后的最简封闭窗口**（按开始时间升序）以及它们在查询范围内的**补集可用窗口**，
供协调员得到唯一可执行的放行窗口。服务无状态，所有结果均由请求实时计算，不依赖任何预置答案。

## 计算规则

1. **时间格式**：所有时间必须是 UTC 的 RFC3339 整分钟形式 `YYYY-MM-DDTHH:MM:00Z`
   （秒必须为 `00`，时区指示符必须为 `Z`；不接受 `+08:00` 等偏移，也不接受缺省秒）。
2. **区间语义**：统一采用左闭右开区间 `[start, end)`。查询起点必须早于终点；
   每个封闭窗口的起点也必须早于终点，否则整次请求被拒绝。
3. **裁剪**：先把每个封闭窗口裁剪到查询范围 `[query.start, query.end)` 内，
   与查询范围无交集（含仅端点相触）的窗口直接丢弃。
4. **归并**：将裁剪后的窗口按开始时间排序，归并所有**重叠或端点相等**（`a.end == b.start`）
   的窗口，得到最简封闭窗口序列，按开始时间升序返回。
5. **补集**：查询范围内未被归并后封闭窗口覆盖的区间即为可用窗口，同样按开始时间升序返回。
6. **跑道隔离**：归并只发生在单次请求自带的封闭窗口数组内，不同跑道的窗口绝不互相归并；
   服务不保存任何跨请求状态。
7. **边界情形**：
   - `closures` 为空数组时，`available` 精确返回完整查询区间，`closures` 返回 `[]`；
   - 封闭覆盖整个查询范围时，`available` 返回 `[]`；
   - 任一数组项非法时整次请求返回 `422`，响应中不含任何部分计算结果。

## API

### `POST /api/v1/runway-windows`

请求体：

| 字段              | 类型     | 说明                                   |
| ----------------- | -------- | -------------------------------------- |
| `query.start`     | string   | 查询起点（UTC 整分钟，含）             |
| `query.end`       | string   | 查询终点（UTC 整分钟，不含）           |
| `runway`          | string   | 跑道编号，非空、不超过 64 字符         |
| `closures`        | array    | 封闭窗口数组，每项含 `start` / `end`   |

请求示例：

```bash
curl -s -X POST http://localhost:8000/api/v1/runway-windows \
  -H 'Content-Type: application/json' \
  -d '{
    "query": {"start": "2026-09-15T22:00:00Z", "end": "2026-09-16T06:00:00Z"},
    "runway": "09L",
    "closures": [
      {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T01:00:00Z"},
      {"start": "2026-09-16T00:30:00Z", "end": "2026-09-16T02:00:00Z"},
      {"start": "2026-09-16T02:00:00Z", "end": "2026-09-16T03:00:00Z"},
      {"start": "2026-09-15T20:00:00Z", "end": "2026-09-15T22:30:00Z"},
      {"start": "2026-09-16T05:30:00Z", "end": "2026-09-16T08:00:00Z"},
      {"start": "2026-09-16T10:00:00Z", "end": "2026-09-16T11:00:00Z"}
    ]
  }'
```

响应 `200 OK`（越界窗口被裁剪或丢弃，重叠/相接窗口被归并，`available` 为补集）：

```json
{
  "runway": "09L",
  "query": {"start": "2026-09-15T22:00:00Z", "end": "2026-09-16T06:00:00Z"},
  "closures": [
    {"start": "2026-09-15T22:00:00Z", "end": "2026-09-15T22:30:00Z"},
    {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T03:00:00Z"},
    {"start": "2026-09-16T05:30:00Z", "end": "2026-09-16T06:00:00Z"}
  ],
  "available": [
    {"start": "2026-09-15T22:30:00Z", "end": "2026-09-15T23:00:00Z"},
    {"start": "2026-09-16T03:00:00Z", "end": "2026-09-16T05:30:00Z"}
  ]
}
```

### 错误响应

任何校验失败都返回 `422`，且一次性报告全部问题；响应只含错误信封，不含部分结果：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      {"code": "INVALID_RANGE", "path": "closures[0]", "message": "Closure start must be earlier than closure end."},
      {"code": "INVALID_TIME_FORMAT", "path": "closures[1].start", "message": "Expected a UTC RFC3339 whole-minute timestamp like 2026-09-15T22:30:00Z (seconds must be 00, zone designator must be Z)."}
    ]
  }
}
```

稳定的错误代码（`details[].code`）：

| 代码                  | 含义                                             |
| --------------------- | ------------------------------------------------ |
| `INVALID_TIME_FORMAT` | 时间不是 UTC RFC3339 整分钟形式                  |
| `INVALID_RANGE`       | 起点未早于终点（`query` 或 `closures[i]`）       |
| `INVALID_VALUE`       | 字段值非法（如跑道编号为空）                     |
| `MISSING_FIELD`       | 缺少必填字段                                     |
| `INVALID_TYPE`        | 字段类型错误                                     |
| `UNEXPECTED_FIELD`    | 出现未定义的字段                                 |
| `INVALID_JSON`        | 请求体不是合法 JSON（`path` 为 `$`）             |

字段路径（`details[].path`）采用点分隔、数组下标用方括号，如 `closures[2].start`、`query.end`。
未匹配的路由返回 `404` 且 `error.code` 为 `NOT_FOUND`。

另提供 `GET /health` 健康检查，返回 `{"status": "ok"}`。

## 运行

### Docker Compose（推荐）

```bash
# 启动 API（常驻服务，宿主端口默认 8000，可用 API_PORT 覆盖）
docker compose up --build api
API_PORT=9000 docker compose up --build api

# 一次性验收：显式启用 verify profile，任一检查失败则以非零码退出
docker compose --profile verify up --build --exit-code-from verify

# 或 API 已在运行时单独执行验收（退出码即验收结果，API 保持运行）
docker compose run --rm verify
```

`verify` 服务等待 `api` 健康后执行 `app/verify.py` 中的验收用例
（归并/裁剪/补集、空封闭、全覆盖、跑道隔离、各类 422），全部通过才退出 0。
镜像只由 `api` 服务构建一次，`verify` 通过同名镜像复用（不单独声明 `build`，
避免两个服务并行构建同一镜像名时相互冲突），因此请通过上述 compose 命令构建，
不要对 `verify` 单独执行 `docker compose build verify`。

`verify` 位于 `verify` profile 下，不参与默认启动：一次性容器若与常驻的 `api`
同在默认启动集合，验收结束退出时会连带停止 `api`（健康检查随之中断），且部分
compose 版本会把"服务容器已退出"判定为整体启动失败。隔离后 `docker compose up`
只管理常驻 API，验收按需显式触发，两者互不干扰。

### 本地开发（Python 3.12）

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 单元与接口测试
pytest

# 对本地实例跑验收脚本
API_BASE_URL=http://127.0.0.1:8000 python -m app.verify
```

## 项目结构

```
app/
  main.py      # FastAPI 应用与路由
  models.py    # Pydantic 请求/响应模型
  service.py   # 时间解析、业务校验、裁剪-归并-补集算法
  errors.py    # 稳定错误码与统一错误信封
  verify.py    # 一次性验收脚本（compose 的 verify 服务）
tests/
  test_service.py  # 算法与时间解析单元测试
  test_api.py      # TestClient 端到端接口测试
Dockerfile
docker-compose.yml # api（API_PORT 覆盖宿主端口）+ verify（一次性验收）
requirements.txt
pytest.ini
```
