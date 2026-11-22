# 流变工坊 · 实时CDC与流式数据同步平台

面向数据库CDC事件捕获、变更日志解析、流式数据转换、维表关联补全、水位线管理、乱序容忍、结果落湖归档与端到端延监控的实时CDC与流式同步平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| CDC数据源 | `cdc_source` | 链路源端 | 源端编号、数据库类型、连接地址 |
| 日志解析 | `binlog_parse` | 解析规则 | 规则编号、源端、日志格式 |
| 事件转换 | `event_trans` | 转换管道 | 管道编号、源端、事件类型 |
| 维表关联 | `dim_join` | 关联配置 | 配置编号、事实表、维度表 |
| 水位管理 | `watermark_mgr` | 水位线 | 水位编号、数据流、当前位点 |
| 乱序处理 | `out_of_order` | 乱序策略 | 策略编号、数据流、时间字段 |
| 结果落盘 | `sink_writer` | 落盘管道 | 管道编号、数据流、目标载体 |
| 延迟监控 | `lag_monitor` | 延迟指标 | 指标编号、数据流、源端位点 |
| 延迟历史观测 | `lag_observation` | 历史采样观测 | 观测编号、恢复代次、检查点编号、日志位点 |
| 延迟分段明细 | `lag_segment` | 待核对回放区间 | 分段编号、恢复代次、日志下界/上界、核对结论 |
| 延迟恢复编排 | `lag_recovery`（专用路由） | 恢复批次 | 编排编号、恢复代次、有效回放窗口、选择分段 |
| 重放派工登记 | `lag_replay`（专用路由） | 可重放段派工 | 派工编号、重放下界/上界、登记位点 |
| DDL处理 | `ddl_handle` | DDL适配 | 适配编号、源端、DDL类型 |
| 断点恢复 | `checkpoint` | 检查点 | 检查点编号、数据流、保存位点 |
| Schema演进 | `schema_evo` | 演进记录 | 记录编号、数据流、变更列 |
| 流去重 | `dedup_stream` | 去重窗口 | 窗口编号、数据流、去重键 |
| 湖仓提交 | `iceberg_commit` | 提交快照 | 快照编号、目标表、数据文件 |
| 小文件合并 | `compaction` | 合并任务 | 任务编号、目标表、文件数量 |
| 快照过期 | `expire_snap` | 过期策略 | 策略编号、目标表、保留数量 |
| 流聚合 | `metric_aggr` | 聚合窗口 | 窗口编号、数据流、聚合键 |
| 增量回填 | `backfill_run` | 回填任务 | 任务编号、目标表、起始位点 |
| 告警规则 | `alert_rule` | 告警策略 | 策略编号、监控指标、告警条件 |
| 数据比对 | `data_diff` | 比对任务 | 任务编号、源端、目标端 |
| 编排调度 | `task_schedule` | 调度计划 | 计划编号、关联链路、Cron表达式 |
| 流审计 | `stream_audit` | 审计记录 | 记录编号、数据流、操作类型 |
| 健康报告 | `stream_health` | 健康评估 | 评估编号、数据流、健康分数 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 延迟分段与恢复编排（按代次切分的核对-回填闭环）

历史延迟观测不再只看最新值，而是按 **数据流 + 恢复代次 + 检查点 + 采样时间** 长期
保留，同一位点数字在不同代次指向不同批次；缺采样的区间不插观测行，查询与复核都
严格按代次切分，不会用其他代次数据填平。

- `GET /api/lag-observations`：按数据流、采样时间、日志位点区间、代次查历史观测，
  返回 `coverage.missing_positions` 显式暴露缺样位点。
- `GET /api/lag-segments`：按流、采样时间、日志位点筛选值班员圈定的延迟分段。
- `POST /api/lag-segments/{id}/confirm`：固化本代核对结论与当时观测（结论不可直接
  改写，变更须先退回待核）；`/return` 带原观测退回，保留筛选定位。
- 恢复批次 `/api/lag-recoveries/{id}`：
  - `/select` 只接收同流、同代次、同检查点、观测完整且已核对的分段；
  - `/interrupt` 在实际登记前中断，选择与结论持久可读回；
  - `/window` 模拟源日志保留范围在同一代次内向前推进（下界只进不退）；
  - `/resume` 沿用原选择续办：窗口收窄首轮带原观测退回并登记失效范围，重新确认后
    再续办才按收窄区间登记重放。
- `GET /api/lag-replays`：重放派工按 `(分段, 代次)` 幂等，同一段重送只派一次；
  `GET /api/lag-recoveries/segments/{segment_id}/dossier` 汇总旧代结论、失效范围和
  实际登记位点。

前端在「延迟恢复编排」页（`/lag-recovery`）提供历史观测、分段核对、续办与复核档案
四个页签；端到端场景见 `backend/tests/test_lag_recovery_flow.py`。

领域：数据工程
子方向：数仓与数据湖
