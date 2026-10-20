"""对象存储抽象。

两个后端：
  - `local`：文件落在磁盘，由 API 以受控路由提供下载（默认，自托管友好）
  - `s3`   ：S3 / 阿里云 OSS / Cloudflare R2 / MinIO 通用（S3 兼容协议），走预签名 URL

关键设计：**存储 key 与租户绑定**（`org/{org_id}/...`）。
下载路由会校验 key 前缀是否属于当前组织，从根上杜绝「改一下 URL 就能下别人的片子」。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, StorageSettings
from ..domain.errors import Forbidden, NotFound, UnsupportedOperation
from ..observability import get_logger

log = get_logger(__name__)

#: 不安全字符：保留 Unicode 字母/数字/下划线，以及点、连字符、空格。
#:
#: 用 `re.UNICODE` 让 `\w` 匹配中日韩等文字 —— 它们是**合法的文件名字符**。
#: 早期版本写死 `[^A-Za-z0-9._\-]`，结果整段中文被压成一个「-」：
#: 「便利店霓虹灯」变成 `-`，用户下载时看到 `clue_-_211f.png`，信息完全丢失。
_UNSAFE_CHARS = re.compile(r"[^\w.\- ]", re.UNICODE)

#: Windows 保留设备名。同名文件在这些系统上无法创建 ——
#: S3 后端无所谓，但本地存储后端会直接失败，因此统一加前缀规避。
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)

#: 名称长度上限，**按字节**而不是字符。
#: 文件系统与 S3 的限制都以字节计，而一个中文字符占 3 字节 ——
#: 按字符限制会让 CJK 名称实际超限。
_MAX_NAME_BYTES = 200


def _truncate_utf8(text: str, limit: int) -> str:
    """按 UTF-8 字节数截断，且不切断多字节字符。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", "ignore")


def _truncate_keep_suffix(name: str, limit: int) -> str:
    """截断时保住扩展名 —— 丢掉 .png/.mp4 会让下游无法判断类型。"""
    if len(name.encode("utf-8")) <= limit:
        return name
    stem, dot, suffix = name.rpartition(".")
    if not dot or len(suffix) > 12:
        return _truncate_utf8(name, limit)
    budget = limit - len(suffix.encode("utf-8")) - 1
    if budget <= 0:
        return _truncate_utf8(name, limit)
    return f"{_truncate_utf8(stem, budget)}.{suffix}"


def sanitize_filename(name: str, *, fallback: str = "file") -> str:
    """把任意名字清洗成安全的存储段。

    要同时满足三件事，缺一不可：

    1. **杜绝路径穿越**：只取最后一段，且把所有路径分隔符替换掉。
       不能只靠 `Path(name).name` —— POSIX 下它不认反斜杠，
       `..\\..\\etc\\passwd` 会被当成一个普通文件名整体保留。
    2. **保留可读性**：中文等 Unicode 字母原样保留。
       用户下载成片时看到的文件名应该有意义。
    3. **跨平台可落盘**：规避 Windows 保留设备名与结尾的点/空格，并按字节限长。
    """
    # 1) **先做 NFKC 归一化**，再拆分路径。
    #
    #    顺序很关键：NFKC 会把全角斜杠「／」（U+FF0F）转成 ASCII「/」。
    #    若先拆分后归一化，这类分隔符就躲过了拆分，只能靠字符替换兜住 ——
    #    能兜住，但语义上把「一条路径」当成了「一个文件名」，不够干净。
    normalized = unicodedata.normalize("NFKC", name or "")
    # 2) 统一分隔符后取最后一段，兼容 Windows 风格的反斜杠
    base = normalized.replace("\\", "/").rsplit("/", 1)[-1].strip()

    # 3) 替换不安全字符并压缩连续连字符
    cleaned = _UNSAFE_CHARS.sub("-", base)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    # Windows 不允许文件名以点、连字符或空格结尾
    cleaned = cleaned.strip(".- ")

    # 4) 规避 Windows 保留设备名（不分大小写；带扩展名也算，如 CON.txt）
    if cleaned.split(".", 1)[0].lower() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    if not cleaned:
        cleaned = fallback

    return _truncate_keep_suffix(cleaned, _MAX_NAME_BYTES)


def build_key(*, org_id: str, project_id: str | None, category: str, filename: str) -> str:
    parts = [
        "org",
        sanitize_filename(org_id, fallback="unknown"),
    ]
    if project_id:
        parts += ["projects", sanitize_filename(project_id, fallback="unknown")]
    parts += [sanitize_filename(category, fallback="misc"), sanitize_filename(filename)]
    return "/".join(parts)


def key_belongs_to_org(key: str, org_id: str) -> bool:
    """租户隔离的硬校验。任何按 key 的读取都必须先过这一关。"""
    normalized = key.replace("\\", "/").lstrip("/")
    return normalized.startswith(f"org/{sanitize_filename(org_id, fallback='unknown')}/")


@dataclass
class StoredObject:
    key: str
    size_bytes: int
    sha256: str
    content_type: str


class Storage(ABC):
    backend: str = "base"

    @abstractmethod
    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject: ...

    @abstractmethod
    async def put_file(
        self, key: str, source: Path, *, content_type: str = "application/octet-stream"
    ) -> StoredObject: ...

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def iter_chunks(self, key: str, *, chunk_size: int = 256 * 1024) -> AsyncIterator[bytes]:
        """流式读取。

        这里**不能写 `async def`**：实现是异步生成器函数，调用它返回的是
        AsyncIterator 而非 Coroutine。声明成 `async def` 会让类型变成 Coroutine，
        进而在 `StreamingResponse(...)` 处报错，也会误导调用方多写一个 await。
        """
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def delete(self, key: str) -> bool: ...

    @abstractmethod
    async def size(self, key: str) -> int: ...

    async def presigned_url(self, key: str, *, expires_in: int = 3600) -> str | None:
        """返回可直连的 URL。本地后端返回 None（改由 API 路由代理下载）。"""
        return None

    async def materialize(self, key: str, target: Path) -> Path:
        """把对象落成磁盘文件（引擎需要真实文件路径）。"""
        data = await self.get_bytes(key)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        await asyncio.to_thread(_write)
        return target


# --------------------------------------------------------------------------- #
# 本地
# --------------------------------------------------------------------------- #
class LocalStorage(Storage):
    """本地磁盘后端。

    所有文件 IO 都走 `asyncio.to_thread`：成片动辄几十 MB，
    直接在事件循环里 `read_bytes()` 会把整个服务的响应卡住——
    这类问题在压测时才会暴露，代价很高。
    """

    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # 双重保险：拼接后再确认仍在根目录内
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise Forbidden("非法的存储路径")
        return candidate

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # 原子替换：读端永远看不到半截文件

    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        path = self._path(key)
        await asyncio.to_thread(self._write_atomic, path, data)
        return StoredObject(key, len(data), hashlib.sha256(data).hexdigest(), content_type)

    async def put_file(
        self, key: str, source: Path, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        data = await asyncio.to_thread(source.read_bytes)
        return await self.put_bytes(key, data, content_type=content_type)

    async def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not await asyncio.to_thread(path.is_file):
            raise NotFound("文件不存在")
        return await asyncio.to_thread(path.read_bytes)

    async def iter_chunks(self, key: str, *, chunk_size: int = 256 * 1024) -> AsyncIterator[bytes]:
        path = self._path(key)
        if not await asyncio.to_thread(path.is_file):
            raise NotFound("文件不存在")
        # 逐块在线程里读：内存占用恒定，且不阻塞事件循环
        with path.open("rb") as fh:
            while True:
                chunk = await asyncio.to_thread(fh.read, chunk_size)
                if not chunk:
                    break
                yield chunk

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if await asyncio.to_thread(path.is_file):
            await asyncio.to_thread(path.unlink)
            return True
        return False

    async def size(self, key: str) -> int:
        path = self._path(key)
        if not await asyncio.to_thread(path.is_file):
            return 0
        return int(await asyncio.to_thread(lambda: path.stat().st_size))

    def local_path(self, key: str) -> Path:
        return self._path(key)

    async def copy_tree_from(self, source_dir: Path, prefix: str) -> list[StoredObject]:
        """把引擎产出的整个目录搬进存储（引擎在本地磁盘上工作）。"""
        # 目录遍历是阻塞 IO，放到线程里做，避免大目录时卡住事件循环
        files = await asyncio.to_thread(lambda: [p for p in sorted(source_dir.rglob("*")) if p.is_file()])
        out: list[StoredObject] = []
        for path in files:
            rel = path.relative_to(source_dir).as_posix()
            out.append(await self.put_file(f"{prefix}/{rel}", path))
        return out


# --------------------------------------------------------------------------- #
# S3 兼容
# --------------------------------------------------------------------------- #
class S3Storage(Storage):
    backend = "s3"

    def __init__(self, settings: StorageSettings) -> None:
        self.cfg = settings
        self.bucket = settings.s3_bucket

    def _session(self) -> Any:
        try:
            import aioboto3  # 延迟导入：未安装 S3 依赖时不影响其它后端
        except ImportError as exc:  # pragma: no cover
            raise UnsupportedOperation("使用 S3 存储需要安装可选依赖：pip install 'reelagent[s3]'") from exc
        return aioboto3.Session()

    def _client_kwargs(self) -> dict[str, Any]:
        cfg = self.cfg
        kwargs: dict[str, Any] = {
            "aws_access_key_id": cfg.s3_access_key_id.get_secret_value(),
            "aws_secret_access_key": cfg.s3_secret_access_key.get_secret_value(),
        }
        if cfg.s3_region:
            kwargs["region_name"] = cfg.s3_region
        if cfg.s3_endpoint_url:
            kwargs["endpoint_url"] = cfg.s3_endpoint_url
        return kwargs

    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        async with self._session().client("s3", **self._client_kwargs()) as client:
            await client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return StoredObject(key, len(data), hashlib.sha256(data).hexdigest(), content_type)

    async def put_file(
        self, key: str, source: Path, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        data = await asyncio.to_thread(source.read_bytes)
        return await self.put_bytes(key, data, content_type=content_type)

    async def get_bytes(self, key: str) -> bytes:
        async with self._session().client("s3", **self._client_kwargs()) as client:
            try:
                resp = await client.get_object(Bucket=self.bucket, Key=key)
                return await resp["Body"].read()
            except Exception as exc:
                raise NotFound("文件不存在") from exc

    async def iter_chunks(self, key: str, *, chunk_size: int = 256 * 1024) -> AsyncIterator[bytes]:
        async with self._session().client("s3", **self._client_kwargs()) as client:
            resp = await client.get_object(Bucket=self.bucket, Key=key)
            stream = resp["Body"]
            async for chunk in stream.iter_chunks(chunk_size):
                yield chunk

    async def exists(self, key: str) -> bool:
        async with self._session().client("s3", **self._client_kwargs()) as client:
            try:
                await client.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:
                return False

    async def delete(self, key: str) -> bool:
        async with self._session().client("s3", **self._client_kwargs()) as client:
            await client.delete_object(Bucket=self.bucket, Key=key)
        return True

    async def size(self, key: str) -> int:
        async with self._session().client("s3", **self._client_kwargs()) as client:
            try:
                resp = await client.head_object(Bucket=self.bucket, Key=key)
                return int(resp.get("ContentLength", 0))
            except Exception:
                return 0

    async def presigned_url(self, key: str, *, expires_in: int = 3600) -> str | None:
        cfg = self.cfg
        if cfg.s3_public_base_url:
            return f"{cfg.s3_public_base_url.rstrip('/')}/{key}"
        async with self._session().client("s3", **self._client_kwargs()) as client:
            try:
                return await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=expires_in,
                )
            except Exception as exc:  # pragma: no cover
                log.warning("s3_presign_failed", key=key, error=str(exc))
                return None


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #
_storage: Storage | None = None


def build_storage(settings: Settings) -> Storage:
    if settings.storage.backend == "s3" and settings.storage.s3_bucket:
        return S3Storage(settings.storage)
    return LocalStorage(settings.storage.local_dir)


def get_storage(settings: Settings | None = None) -> Storage:
    global _storage
    if _storage is None:
        if settings is None:
            from ..config import get_settings

            settings = get_settings()
        _storage = build_storage(settings)
    return _storage


def reset_storage() -> None:
    """测试用：切换配置后重置单例。"""
    global _storage
    _storage = None


def content_type_for(filename: str) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    ext = Path(filename).suffix.lower()
    return {
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".json": "application/json",
        ".zip": "application/zip",
    }.get(ext, "application/octet-stream")


async def purge_org_storage(settings: Settings, org_id: str) -> int:
    """删除某组织的全部对象（账号注销时调用）。"""
    storage = get_storage(settings)
    root = (
        storage.local_path(f"org/{sanitize_filename(org_id, fallback='unknown')}")
        if isinstance(storage, LocalStorage)
        else None
    )
    if root and root.exists():
        count = sum(1 for p in root.rglob("*") if p.is_file())
        shutil.rmtree(root, ignore_errors=True)
        return count
    return 0
