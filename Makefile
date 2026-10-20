# ============================================================================
# ReelAgent 开发/运维常用命令
#
# 说明：
#   * 本文件用 **tab** 缩进（Makefile 的硬性要求，空格会报
#     "missing separator"，编辑器里请确认没有把 tab 转成空格）。
#   * 目标以 *nix / macOS / WSL / Git Bash 为主；Windows 原生 CMD 下
#     请直接用 `make` 不可用时对应的 docker compose 命令（见每个目标注释）。
#   * Python 解释器统一用 `PY` 变量，方便切虚拟环境：
#         make test PY=/usr/bin/python3.12
#     容器内或 CI 里也能直接复用：`make lint PY=python`。
# ============================================================================

# 虚拟环境里的解释器（Windows 用 Scripts，*nix 用 bin）
PY ?= .venv/bin/python
# Windows 用户可直接覆盖：make test PY=.venv/Scripts/python.exe
PIP ?= $(PY) -m pip
COMPOSE ?= docker compose
IMAGE ?= reelagent
TAG ?= local

.DEFAULT_GOAL := help
.PHONY: help install dev api worker migrate test test-fast lint fmt typecheck \
        docker-build docker-up docker-down docker-logs clean clean-all smoke smoke-docker

# --------------------------------------------------------------------------- #
help:  ## 显示所有可用目标
	@echo "ReelAgent 常用目标："
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Windows 提示：PY=.venv/Scripts/python.exe，例如 make test PY=.venv/Scripts/python.exe"

# --------------------------------------------------------------------------- #
install:  ## 安装项目与开发依赖（可编辑模式）
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,s3,sentry]"

dev:  ## 启动本地依赖（postgres + redis），宿主机裸跑 Python 用
	$(COMPOSE) -f docker-compose.dev.yml up -d
	@echo "已启动：postgres=localhost:5432 redis=localhost:6379"
	@echo "需要在 .env 里把 REEL_DATABASE__URL / REEL_REDIS__URL 指向 localhost"

api:  ## 本地启动 API（热重载）
	$(PY) -m uvicorn reelagent_cloud.api.app:app --host 0.0.0.0 --port 8000 --reload

worker:  ## 本地启动 worker
	$(PY) -m reelagent_cloud.worker

migrate:  ## 执行数据库迁移（无 alembic.ini 时降级为建表，见脚本内注释）
	$(PY) scripts/wait_for_db.py --timeout 60
	@if [ -f alembic.ini ]; then $(PY) -m alembic upgrade head; \
	else echo "未找到 alembic.ini：先执行 \`$(PY) -m alembic init reelagent_cloud/db/migrations\` 并生成首个版本"; fi

# --------------------------------------------------------------------------- #
test:  ## 跑测试并输出覆盖率
	$(PY) -m pytest --cov=reelagent --cov=reelagent_cloud --cov-report=term-missing

test-fast:  ## 只跑单元测试（跳过 integration 标记）
	$(PY) -m pytest -m "not integration"

lint:  ## ruff check（含安全规则）
	$(PY) -m ruff check .

fmt:  ## 自动格式化 + 自动修可修问题
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck:  ## mypy 静态类型检查
	$(PY) -m mypy .

# --------------------------------------------------------------------------- #
docker-build:  ## 构建生产镜像
	$(COMPOSE) build

docker-up:  ## 起完整生产形态（含 migrate → api → worker）
	$(COMPOSE) up -d --build
	@echo "API: http://127.0.0.1:$${REEL_HTTP_PORT:-8000}/livez"

docker-down:  ## 停止并移除容器（**保留**数据卷）
	$(COMPOSE) down --remove-orphans

docker-logs:  ## 跟踪所有服务日志
	$(COMPOSE) logs -f --tail=100

# --------------------------------------------------------------------------- #
smoke:  ## 冒烟测试：本机起服务并逐个探测关键端点
	@echo "==> 检查 /livez（存活，不查库）"
	curl -fsS http://127.0.0.1:8000/livez && echo ""
	@echo "==> 检查 /readyz（就绪，查库 + 查存储，未就绪返回 503）"
	curl -fsS http://127.0.0.1:8000/readyz && echo ""
	@echo "==> 检查 /health（人类可读汇总）"
	curl -fsS http://127.0.0.1:8000/health && echo ""
	@echo "==> 检查 /metrics（Prometheus 抓取端点）"
	curl -fsS http://127.0.0.1:8000/metrics | head -n 5
	@echo "冒烟通过"

smoke-docker:  ## 冒烟测试（容器形态，容器内自探，不依赖宿主端口）
	$(COMPOSE) exec -T api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())"

# --------------------------------------------------------------------------- #
clean:  ## 清理本地缓存与临时产物（**不动** workspace 里的成片）
	find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml build dist
	rm -rf tests/_tmp
	@echo "清理完成（workspace/ 与数据卷未动）"

clean-all: clean  ## 彻底清理：连本地 workspace 与数据库卷一起删（危险）
	rm -rf workspace
	@echo "如需删除数据卷：$(COMPOSE) down -v"
