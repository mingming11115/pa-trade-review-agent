# PA Market Analysis Demo

PA Market Analysis Demo 是一个面向交易复盘、行情分析和任务编排的全栈应用。
项目由 **FastAPI 后端** 和 **React + Vite 前端** 组成，支持 K 线行情展示、交易复盘、实时分析、分析历史管理、提示词管理、个人模型配置等能力。

> 免责声明：本项目中的分析结果仅用于研究与复盘，不构成投资建议。

## 主要功能

- **行情分析看板**：支持 ES / NQ 等品种的多周期 K 线展示与分析
- **历史复盘**：基于交易记录自动生成复盘窗口并进行分析
- **实时分析**：针对实时行情进行持续轮询与自动触发分析
- **分析历史管理**：查看、筛选、重试、收藏和恢复历史分析结果
- **交易日志管理**：支持手工录入交易、导入 Excel / CSV 交易文件
- **追问助手**：围绕已完成分析结果继续追问和补充判断
- **提示词管理**：管理员可在线查看、编辑与回滚提示词版本
- **个人中心**：管理模型配置、Token 用量与导入记录
- **预警规则**：支持价格/收盘类告警规则配置

## 技术栈

### 后端

- FastAPI
- SQLAlchemy Async
- PostgreSQL / asyncpg
- LangGraph / LLM 工具链
- Uvicorn

### 前端

- React 19
- TypeScript
- Vite
- lightweight-charts
- Vitest

## 项目结构

```text
PA/
├── backend/      # FastAPI 后端服务
├── frontend/     # React 前端应用
├── docs/         # 设计文档与规划
├── Makefile      # 常用开发命令
└── .env.example  # 环境变量示例
```

## 本地运行

### 1. 准备环境

请先安装：

- Python 3.11+
- Node.js 18+
- PostgreSQL

### 2. 配置环境变量

复制并修改环境变量示例文件：

```bash
cp .env.example backend/.env
```

根据你的实际环境修改以下配置：

- `DATABASE_URL`
- `HIST_BASE_URL`
- `HIST_API_KEY`
- `FRONTEND_ORIGIN`
- `AUTH_REQUIRED`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

### 3. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认运行在 `http://localhost:5173`，后端默认运行在 `http://localhost:8000`。

## 常用命令

在项目根目录下可以直接使用 `Makefile`：

```bash
make backend-dev   # 启动后端
make frontend-dev  # 启动前端
make backend-test  # 后端测试
make frontend-test # 前端测试
make test          # 运行全部测试
make build         # 构建前端
```

## 测试

### 后端测试

```bash
cd backend
pytest
```

### 前端测试

```bash
cd frontend
npm test -- --run
```

## 环境变量说明

` .env.example ` 中包含了项目运行所需的主要配置项，常见配置如下：

- `HIST_BASE_URL`：历史行情服务地址
- `HIST_API_KEY`：历史行情 API Key
- `DATABASE_URL`：数据库连接字符串
- `FRONTEND_ORIGIN`：允许跨域的前端地址
- `COLLECTOR_ENABLED`：是否开启分钟级行情采集
- `LIVE_WS_ENABLED`：是否开启实时成交采集
- `AUTH_REQUIRED`：是否启用登录认证
- `LANGFUSE_*`：可观测性追踪配置

## 后端接口

后端提供的核心 API 包括：

- `GET /api/v1/health`
- `GET /api/v1/market/bars`
- `POST /api/v1/demo/analyze`
- `POST /api/v1/demo/analyze/stream`
- `GET /api/v1/analyses`
- `GET /api/v1/trades/recent`
- `POST /api/v1/trades/import/preview`
- `POST /api/v1/trades/import/confirm`
- `GET /api/v1/admin/orchestration`

## 开发说明

- 前端主入口在 `frontend/src/App.tsx`
- 后端主入口在 `backend/app/main.py`
- 示例环境变量在 `.env.example`
- 数据与分析工作流相关逻辑分布在 `backend/app/analysis/` 目录下

## 许可证

如果你准备开源到 GitHub，建议在这里补充项目许可证信息，例如 MIT、Apache-2.0 等。
