# syntax=docker/dockerfile:1.7
# ============================================================================
# ReelAgent 生产镜像（多阶段构建）
#
# 设计要点：
#   1. builder 阶段只负责「装依赖」，编译工具链不进最终镜像；
#   2. 依赖层与源码层分离：只改业务代码时不会重装依赖，构建秒级完成；
#   3. 运行时**非 root**（appuser, uid/gid 10001），并用固定数字 uid，
#      这样 K8s 的 runAsNonRoot / 卷权限校验才不会因为用户名解析而失败；
#   4. 镜像内安装 ffmpeg —— 引擎合成 MP4 依赖它（reelagent_cloud 通过
#      shutil.which("ffmpeg") 探测，不在 PATH 上就自动降级为 GIF+WAV）；
#   5. HEALTHCHECK 打 /livez（不查数据库），理由见文件末尾注释。
# ============================================================================
ARG PYTHON_VERSION=3.12

# 可选：apt 镜像源主机名（默认留空 = 用 Debian 官方源）。
#
# 为什么需要它：Debian 官方源国内很慢。实测在阿里云 ECS 上构建时，
# `apt-get install ffmpeg` 的 206 个包跑了一小时还没下完 —— 而构建时间
# 直接影响「改一行代码等多久能看到结果」。换成国内镜像后是分钟级。
#
# 因为要影响构建速度又不想改变海外构建的默认行为，做成**可选参数**：
#     docker compose build --build-arg APT_MIRROR=mirrors.aliyun.com
# 不传则完全不动，行为与之前一致。
#
# 注意 ARG 在 FROM 之前声明是"全局作用域"，每个 stage 内必须重新声明才能使用。
ARG APT_MIRROR=""

# --------------------------------------------------------------------------- #
# 阶段一：builder —— 只装三方依赖，产出可搬运的虚拟环境 /opt/venv
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS builder

ARG APT_MIRROR
# 可选：PyPI 镜像地址（默认留空 = 官方 pypi.org）。
# 国内拉 PyPI 极慢：实测在阿里云 ECS 上，光是收集 55 个包的元数据就花了
# 十几分钟。换成国内镜像后是分钟级。
#     docker compose build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
# 留空时 pip 行为与之前完全一致，不影响海外构建。
ARG PIP_INDEX_URL=""

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# 换源：Debian 12 (bookworm) 及更早用 /etc/apt/sources.list；
# Debian 13 (trixie) 起改用 deb822 格式的 /etc/apt/sources.list.d/debian.sources。
# 两个文件都试，缺哪个都 `|| true`，避免因为发行版差异把构建搞挂。
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
      echo "[builder] apt 已切换到 ${APT_MIRROR}"; \
    fi

# 极少数纯源码包（版本更新滞后、无 cp312 wheel）需要现编，工具链只留在本阶段
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libffi-dev \
 && rm -rf /var/lib/apt/lists/*

# 依赖装进独立 venv，runtime 阶段整目录拷贝即可（同一基础镜像，路径一致）
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# 先只拷 pyproject.toml + README.md（setuptools 读 README 作为 long_description）：
# 这一层只要依赖没变就一直命中缓存
COPY pyproject.toml README.md ./

# 从 pyproject.toml 抽出运行期依赖（含 s3 / sentry 两个可选组）写成 requirements.txt。
# 用 tomllib（3.11+ 标准库）解析，避免引入额外的工具或维护第二份依赖清单。
RUN python -c "\
import pathlib, tomllib;\
p = tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'));\
x = p['project'].get('optional-dependencies', {});\
deps = p['project']['dependencies'] + x.get('s3', []) + x.get('sentry', []);\
pathlib.Path('requirements.txt').write_text(chr(10).join(deps) + chr(10), encoding='utf-8')" \
 && pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt

# 再拷源码，安装项目本身（--no-deps：依赖上一层的都装好了）以获得控制台入口
# reelagent-server / reelagent-worker / reelagent-admin
COPY reelagent ./reelagent
COPY reelagent_cloud ./reelagent_cloud
RUN pip install --no-deps .

# --------------------------------------------------------------------------- #
# 阶段二：runtime —— 只保留运行必需品
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS runtime

# 每个 stage 都要重新声明，全局 ARG 不会自动带进来
ARG APT_MIRROR

# PYTHONUNBUFFERED：容器日志立刻刷出，否则 structlog 的 JSON 日志会被缓冲成块
# PYTHONDONTWRITEBYTECODE：容器文件系统只读场景下不要写 .pyc
# PYTHONFAULTHANDLER：崩溃时打印 Python 栈，便于排查 worker 段错误
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 换源（理由同 builder 阶段）：ffmpeg 这一步要装 206 个包，
# 官方源在国内能拖到一个小时以上，这是整条构建链最主要的耗时来源。
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
      echo "[runtime] apt 已切换到 ${APT_MIRROR}"; \
    fi

# ffmpeg：成片合成必需。
# ca-certificates：调用 HTTPS 供应商 API（火山方舟 / DashScope / Stripe）必需。
# tini：作为 1 号进程回收僵尸进程并正确转发 SIGTERM —— worker 的优雅停机依赖它，
#       否则 `reelagent-worker` 可能收不到信号而被直接 SIGKILL，正在生成的成片全废。
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

# 刷新字体缓存：ffmpeg 的 drawtext 滤镜需要 fontconfig。
# 单独一条 RUN，并且 `|| true` 只作用于它自己 —— 如果和上面的 apt-get 写在一起，
# 一旦 ffmpeg 安装失败会被 `|| true` 顺手吞掉，构建"成功"却生不出 MP4。
RUN fc-cache -f >/dev/null 2>&1 || true

# 非 root 运行：固定 uid/gid 10001，与 K8s securityContext 的 fsGroup/runAsUser 对齐
RUN groupadd --gid 10001 appuser \
 && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser reelagent ./reelagent
COPY --chown=appuser:appuser reelagent_cloud ./reelagent_cloud
COPY --chown=appuser:appuser scripts ./scripts
COPY --chown=appuser:appuser pyproject.toml README.md ./

# 产物 / 本地存储目录，预先建好并授权（命名卷首次挂载会继承这里的属主）
RUN mkdir -p /app/workspace/storage /app/workspace/engine \
 && chown -R appuser:appuser /app/workspace

USER appuser

EXPOSE 8000

# 存活探针走 /livez：它不查数据库、不查存储。
# 反过来若用 /readyz（数据库 + 存储双向探测）当 HEALTHCHECK，数据库抖动
# 或对象存储偶发 5xx 会让 Docker 判定容器"不健康"进而重启，把一次可自愈的
# 依赖抖动放大成一次进程重启 —— 这是生产上最常见的雪崩引信。
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=3).status == 200 else 1)"]

# 默认跑 HTTP 服务；worker 由 docker-compose / K8s 覆盖 command 为 reelagent-worker
# 用 tini 作为 1 号进程，确保 SIGTERM 能传到 uvicorn / worker
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["reelagent-server", "--host", "0.0.0.0", "--port", "8000"]
