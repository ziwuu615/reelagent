"""智谱 AI（BigModel）供应商实现。

接入它的主要理由是 **CogVideoX-Flash 是官方免费的视频生成模型**，
适合在没有付费额度时把整条流水线跑通。

协议特点（与火山方舟不同，这正是供应商抽象层要隔离的差异）：
  - 鉴权：`Authorization: Bearer <id>.<secret>`，Key 形如 `xxxx.yyyy`
  - 生成：**统一异步任务模型** —— POST 拿 task id，再 GET `/async-result/{id}` 轮询
  - 图像也提供异步接口（`/images/generations` 的异步版本），这里统一走异步，
    避免同步接口在长耗时任务上触发网关超时
  - 参考图不是 base64 而必须是**可公网访问的 URL 或本服务上传的文件**，
    因此图生视频时若传入本地关键帧，需要先有图床；这里对本地文件明确报错，
    而不是静默忽略参考图（静默忽略会让「一致性」悄悄失效）。

未验证说明：本模块在编写时**没有可用的智谱 Key**，因此未做联网实测。
接口按官方文档实现，首次接入时请用 `scripts/probe_zhipu.py` 逐级验证。
"""

from __future__ import annotations

import base64
import contextlib
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

from .base import (
    ImageProvider,
    MediaResult,
    ProviderError,
    VideoProvider,
    estimate_image_cost,
    estimate_video_cost,
)

#: 智谱开放平台默认 Base URL
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

#: 免费视频模型
FREE_VIDEO_MODEL = "cogvideox-flash"

# --------------------------------------------------------------------------- #
# 尺寸约束
# --------------------------------------------------------------------------- #
#: 智谱图像接口的硬性要求（错误码 1214）：
#:   长宽均在 512~2880 之间、**必须是 32 的整数倍**、总像素 < 2^22
#:
#: 这条约束踩过一次坑：常见的 16:9 分辨率（1280x720、1920x1080）里，
#: 720 和 1080 都不是 32 的倍数，会直接被 400 拒绝。
#: 而且报错发生在重试 3 次之后才暴露，浪费了整整一个阶段的时间。
#: 所以这里在**发请求之前**就把尺寸对齐，而不是等接口报错再处理。
_MIN_SIDE = 512
_MAX_SIDE = 2880
_MAX_PIXELS = 2**22  # 4_194_304


def _align_side(value: int) -> int:
    """把单边长度对齐成 32 的整数倍并夹到合法区间。"""
    snapped = int(round(value / 32.0) * 32)
    return max(_MIN_SIDE, min(_MAX_SIDE, snapped))


def normalize_size(size: tuple[int, int]) -> tuple[int, int]:
    """把期望尺寸转换为智谱可接受的尺寸。

    步骤：先按像素上限等比缩放 → 对齐到 32 的倍数 → 若对齐后仍超限则逐边回退。
    逐边回退保证一定终止（每次至少减 32，且有下界 512）。
    """
    width, height = int(size[0]), int(size[1])

    if width * height > _MAX_PIXELS:
        scale = (_MAX_PIXELS / (width * height)) ** 0.5
        width, height = int(width * scale), int(height * scale)

    width, height = _align_side(width), _align_side(height)

    while width * height > _MAX_PIXELS and (width > _MIN_SIDE or height > _MIN_SIDE):
        if width >= height and width > _MIN_SIDE:
            width -= 32
        elif height > _MIN_SIDE:
            height -= 32
        else:  # pragma: no cover - 512x512 已在限额内，不会走到这里
            break

    return width, height


# --------------------------------------------------------------------------- #
# HTTP 工具
# --------------------------------------------------------------------------- #
def _headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _parse_error_body(detail: str) -> tuple[str, str]:
    """从错误体里取出上游的**业务错误码**与消息。

    智谱在 HTTP 429 这一个状态码下塞了多种语义完全不同的情况：

        code 1305  模型当前访问量过大      瞬时，几秒到几十秒恢复
        code 1302  账户已达到速率限制      分钟级配额窗口
        code 1113  余额不足或无可用资源包   **重试永远不会成功**

    早期实现对所有 429 都硬编码成「模型访问量过大（code 1305）属瞬时过载」，
    结果把「余额不足」也伪装成了「稍后重试即可」——
    系统会徒劳重试四次、白等几分钟，最后给出的原因还是错的。
    错误处理一旦丢掉上游的真实判据，排查就只能靠猜。
    """
    parsed: object = None
    with contextlib.suppress(Exception):
        parsed = json.loads(detail)

    if isinstance(parsed, dict):
        err = parsed.get("error") or parsed
        if isinstance(err, dict):
            code = str(err.get("code") or "")
            message = str(err.get("message") or "")
            if code or message:
                return code, message
    return "", detail


def _request_json(
    url: str, *, api_key: str, payload: dict | None = None, timeout: float = 60.0, method: str = "POST"
) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(api_key), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        # 读取错误体只是为了给出更清晰的提示，失败也不该掩盖原始状态码
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:400]

        code, message = _parse_error_body(detail)
        label = f"code {code}：{message}" if code else (message or exc.reason)

        # 消息里**原样保留上游业务码**，上层的重试分类与用户提示都依赖它。
        # 不在适配器里替上游"翻译"结论 —— 那是越权且会丢信息。
        raise ProviderError(f"智谱 HTTP {exc.code} [{label}]") from exc
    except Exception as exc:
        raise ProviderError(f"{type(exc).__name__}: {exc}") from exc


def _download(url: str, out_path: Path, *, timeout: float = 300.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "reelagent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out_path.write_bytes(resp.read())
    return out_path


# --------------------------------------------------------------------------- #
# 异步任务轮询（图像与视频共用）
# --------------------------------------------------------------------------- #
def _extract_task_id(body: dict) -> str:
    """从异步响应里取任务 ID。

    ⚠️ **绝对不要回退到 `request_id`**。智谱的**每个**响应都带 `request_id`
    （包括同步返回图片的那种），它只是链路追踪 ID，不是任务句柄。
    早期版本拿它当兜底，结果：图像接口明明同步返回了图片，代码却握着
    request_id 去轮询 `/async-result/...`，收到「任务 xxx 不存在」这种
    极具误导性的报错。此坑已踩过一次，故写明。
    """
    return str(body.get("id") or body.get("task_id") or "")


def _extract_direct_url(body: dict) -> str:
    """同步响应里直接可用的结果 URL（智谱图像接口走这条路）。"""
    items = body.get("data") or []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
    for key in ("url", "video_url"):
        if body.get(key):
            return str(body[key])
    return ""


def _submit_task(
    base_url: str,
    api_key: str,
    endpoint: str,
    payload: dict,
    *,
    timeout: float = 120.0,
) -> tuple[str, dict]:
    """提交生成任务，返回 (任务 ID, 原始响应体)。"""
    body = _request_json(f"{base_url}{endpoint}", api_key=api_key, payload=payload, timeout=timeout)
    task_id = _extract_task_id(body)
    if not task_id:
        raise ProviderError(f"智谱未返回任务 ID：{str(body)[:300]}")
    return task_id, body


def _await_task(
    base_url: str,
    api_key: str,
    task_id: str,
    *,
    result_keys: tuple[str, ...],
    poll_interval: float = 4.0,
    poll_timeout: float = 900.0,
) -> str:
    """轮询直到成功，返回结果 URL。

    `result_keys` 用于兼容不同模型返回结构的差异
    （视频是 `video_result`，图像是 `image_result`）。
    """
    deadline = time.time() + poll_timeout
    last_status = "UNKNOWN"

    while time.time() < deadline:
        time.sleep(poll_interval)
        state = _request_json(
            f"{base_url}/async-result/{task_id}", api_key=api_key, payload=None, method="GET"
        )
        last_status = str(state.get("task_status") or state.get("status") or "").upper()

        if last_status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
            for key in result_keys:
                items = state.get(key) or []
                if items and isinstance(items, list):
                    url = items[0].get("url") or items[0].get("video_url")
                    if url:
                        return str(url)
            # 有些返回直接把 url 放在顶层
            for key in ("url", "video_url"):
                if state.get(key):
                    return str(state[key])
            raise ProviderError(f"任务成功但未找到结果 URL：{str(state)[:300]}")

        if last_status in {"FAIL", "FAILED", "ERROR", "CANCELED", "CANCELLED"}:
            raise ProviderError(f"智谱任务失败（{last_status}）：{str(state)[:300]}")

    raise ProviderError(f"智谱任务轮询超时（{poll_timeout}s，最后状态 {last_status}，task={task_id}）")


# --------------------------------------------------------------------------- #
# 图像
# --------------------------------------------------------------------------- #
class ZhipuImageProvider(ImageProvider):
    name = "zhipu"
    model = "glm-image"

    #: 智谱的图像参考图同样要求公网 URL，无法接收本地文件。
    #: 流水线据此在不带参考图的情况下生成（一致性降级但成片仍出）。
    accepts_local_ref_images = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        poll_interval: float = 3.0,
        poll_timeout: float = 600.0,
    ) -> None:
        if not api_key:
            raise ProviderError("智谱图像供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or self.model
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def generate(
        self,
        *,
        prompt: str,
        out_path: Path,
        size: tuple[int, int],
        ref_images: list[Path] | None = None,
        seed: int = 0,
        #: 是否使用优速版模型。当前仅 DashScope 支持（express 档），
        #: 其他供应商忽略此参数 —— 但**必须显式接受**，否则调用方传了
        #: 就会 TypeError，片段生成失败、镜头判为未完成，导致 generate
        #: 阶段在每次 resume 时重跑。抽象基类与所有实现签名必须一致。
        use_fast: bool = False,
    ) -> MediaResult:
        started = time.perf_counter()

        # 发请求前先对齐尺寸：智谱要求长宽为 32 的整数倍，
        # 而常规的 1280x720 / 1920x1080 都不满足，直接发会被 400 拒绝
        actual_size = normalize_size(size)

        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "size": f"{actual_size[0]}x{actual_size[1]}",
        }
        if seed:
            payload["seed"] = seed
        if ref_images:
            # 流水线在 accepts_local_ref_images=False 时不会传参考图；
            # 若仍被直接传入，明确报错而不是静默忽略 ——
            # 静默丢掉参考图会让「跨镜头一致性」变成假数据。
            raise ProviderError(
                "智谱图像接口需要公网可访问的参考图 URL；当前传入的是本地文件。"
                "请先配置图床（REEL_STORAGE__BACKEND=s3 并暴露公网地址），"
                "或改用支持 base64 参考图的供应商（火山方舟）。"
            )

        task_id, body = _submit_task(
            self.base_url,
            self.api_key,
            "/images/generations",
            payload,
            # 智谱图像接口是同步的，生成耗时直接体现在这个 HTTP 请求上；
            # 首张（冷启动）可能超过 60 秒，因此给足余量。
            timeout=max(120.0, self.poll_timeout / 2),
        )

        # 智谱图像接口返回体里**同时**有：
        #   data[].url  —— 真正的结果（说明是同步返回）
        #   id / request_id —— 追踪信息
        # 所以先取直接结果，取不到才退回异步轮询。
        url = _extract_direct_url(body)
        mode = "sync"
        if not url:
            mode = "async"
            url = _await_task(
                self.base_url,
                self.api_key,
                task_id,
                result_keys=("image_result", "data"),
                poll_interval=self.poll_interval,
                poll_timeout=self.poll_timeout,
            )
        _download(url, out_path)

        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=estimate_image_cost(1),
            latency_ms=(time.perf_counter() - started) * 1000,
            meta={
                # 记录实际尺寸：因 32 倍数约束，实际尺寸可能与请求尺寸略有出入
                # （例如 9:16 的 540x960 会被对齐成 544x960，宽高比偏差约 0.7%）。
                # 写进 meta 让下游（合成器 / 一致性评分）能感知，而不是默默接受。
                "requested_size": (int(size[0]), int(size[1])),
                "size": actual_size,
                "size_adjusted": actual_size != (int(size[0]), int(size[1])),
                "mode": mode,
                "task_id": task_id,
            },
        )


# --------------------------------------------------------------------------- #
# 视频
# --------------------------------------------------------------------------- #
class ZhipuVideoProvider(VideoProvider):
    #: 真实视频模型返回 MP4 容器
    output_suffix = "mp4"

    name = "zhipu"
    model = FREE_VIDEO_MODEL

    #: 智谱要求参考图为公网 URL，无法接收本地文件。
    #: 流水线会据此把该镜头降级为纯文生视频（一致性降级，但成片仍可出）。
    #: 若要恢复图生视频，需要配置对象存储并暴露公网地址。
    accepts_local_keyframe = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        poll_interval: float = 5.0,
        poll_timeout: float = 900.0,
    ) -> None:
        if not api_key:
            raise ProviderError("智谱视频供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or self.model
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def generate(
        self,
        *,
        prompt: str,
        out_path: Path,
        duration_s: float,
        size: tuple[int, int],
        keyframe: Path | None = None,
        seed: int = 0,
        #: 优速版开关：仅 DashScope 支持，其余供应商忽略但必须接受，
        #: 否则调用方传参会 TypeError，导致片段生成失败。
        use_fast: bool = False,
    ) -> MediaResult:
        started = time.perf_counter()
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "with_audio": False,
        }
        if seed:
            payload["seed"] = seed

        # 本供应商声明 accepts_local_keyframe = False，因此关键帧**一律不参与生成**。
        # 记录降级事实，让「一致性未生效」可见而不是静默。
        consistency = "text_only" if keyframe is None else "text_only(keyframe_ignored)"

        task_id, _body = _submit_task(self.base_url, self.api_key, "/videos/generations", payload)
        url = _await_task(
            self.base_url,
            self.api_key,
            task_id,
            result_keys=("video_result",),
            poll_interval=self.poll_interval,
            poll_timeout=self.poll_timeout,
        )
        _download(url, out_path)

        # CogVideoX-Flash 免费，因此成本记 0；换成付费模型时这里要按官方价目表调整
        cost = 0.0 if self.model == FREE_VIDEO_MODEL else estimate_video_cost(duration_s)

        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=cost,
            latency_ms=(time.perf_counter() - started) * 1000,
            meta={
                "task_id": task_id,
                "duration_s": duration_s,
                "size": size,
                "free": cost == 0.0,
                "consistency": consistency,
            },
        )


# --------------------------------------------------------------------------- #
# 测试辅助
# --------------------------------------------------------------------------- #
def _data_uri(path: Path) -> str:
    """把本地文件转成 data URI（部分智谱接口接受，供备用）。"""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
