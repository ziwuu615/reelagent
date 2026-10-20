"""产物文件服务。

三种访问路径，安全边界各不相同：

  /files/{key}                    登录用户访问自己的产物。
                                  **先校验 key 前缀属于当前组织**，再读文件。
                                  这是整个产品最容易出现越权的地方，因此校验写死在路由里。
  /s/{token}                      分享链接。签名 token 自带过期时间，无需登录。
  /api/v1/artifacts/{id}/download 按 Artifact ID 下载，走标准鉴权 + 租户过滤。

响应统一带 `Cache-Control: private`（分享链接除外）与 `ETag`，
让浏览器缓存住大文件，避免每次进 Studio 都重下几十 MB 的成片。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from ...config import Settings
from ...db.models import Artifact
from ...deps import AppSettings, DbSession
from ...domain.enums import ArtifactKind
from ...domain.errors import Forbidden, NotFound
from ...domain.ids import parse_ulid_timestamp
from ...observability import get_logger
from ...security import CurrentPrincipal, TokenError, read_share_token
from ...services import storage as storage_svc

log = get_logger(__name__)
router = APIRouter(tags=["files"])

CHUNK = 256 * 1024


# --------------------------------------------------------------------------- #
# 内部：统一的流式响应
# --------------------------------------------------------------------------- #
async def _stream(
    settings: Settings,
    *,
    key: str,
    content_type: str,
    filename: str,
    etag: str = "",
    cache_control: str = "private, max-age=3600",
    disposition: str = "inline",
) -> Response:
    storage = storage_svc.get_storage(settings)
    if not await storage.exists(key):
        raise NotFound("文件不存在或已被清理")

    size = await storage.size(key)
    headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Cache-Control": cache_control,
        "Accept-Ranges": "none",
    }
    if etag:
        headers["ETag"] = f'"{etag}"'
    if size:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        storage.iter_chunks(key, chunk_size=CHUNK),
        media_type=content_type,
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# 登录用户访问
# --------------------------------------------------------------------------- #
@router.get("/files/{key:path}", include_in_schema=False)
async def get_file(
    key: str,
    request: Request,
    settings: AppSettings,
    principal: CurrentPrincipal,
) -> Response:
    # 租户隔离的硬边界：key 必须属于当前组织
    if not storage_svc.key_belongs_to_org(key, principal.org_id):
        log.warning(
            "cross_tenant_file_access_blocked",
            org_id=principal.org_id,
            actor=principal.actor_label,
            key=key[:120],
        )
        raise Forbidden("无权访问该文件")

    filename = key.rsplit("/", 1)[-1]
    return await _stream(
        settings,
        key=key,
        content_type=storage_svc.content_type_for(filename),
        filename=filename,
    )


@router.get("/api/v1/artifacts/{artifact_id}/download", tags=["artifacts"])
async def download_artifact(
    artifact_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: CurrentPrincipal,
    disposition: str = "attachment",
) -> Response:
    artifact = (
        await session.execute(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.org_id == principal.org_id,  # 租户过滤
                Artifact.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise NotFound("产物不存在")

    artifact.download_count = int(artifact.download_count) + 1
    await session.flush()

    return await _stream(
        settings,
        key=artifact.storage_key,
        content_type=artifact.content_type,
        filename=artifact.filename or "download",
        etag=artifact.sha256[:32],
        disposition=disposition if disposition in {"inline", "attachment"} else "attachment",
    )


# --------------------------------------------------------------------------- #
# 分享链接
# --------------------------------------------------------------------------- #
@router.get("/s/{token}", include_in_schema=False)
async def shared_file(
    token: str,
    settings: AppSettings,
    session: DbSession,
) -> Response:
    """公开分享。token 自签名且带过期时间，无需登录。"""
    try:
        data = read_share_token(settings, token)
    except TokenError as exc:
        raise NotFound(str(exc)) from exc

    artifact_id = str(data.get("aid") or "")
    artifact = (
        await session.execute(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.is_public.is_(True),
                Artifact.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise NotFound("分享的内容不存在或已取消分享")

    # 分享页允许跨源引用，且用较长的公共缓存
    response = await _stream(
        settings,
        key=artifact.storage_key,
        content_type=artifact.content_type,
        filename=artifact.filename or "download",
        etag=artifact.sha256[:32],
        cache_control="public, max-age=86400",
    )
    response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return response


# --------------------------------------------------------------------------- #
# 产物元信息
# --------------------------------------------------------------------------- #
@router.get("/api/v1/projects/{project_id}/artifacts", tags=["artifacts"])
async def list_project_artifacts(
    project_id: str,
    session: DbSession,
    principal: CurrentPrincipal,
    kind: str = "",
) -> dict[str, object]:
    from ...services import projects as projects_svc

    # 先确认项目归属，避免用别人的 project_id 探测产物
    await projects_svc.get_project(session, org_id=principal.org_id, project_id=project_id)

    kinds = None
    if kind:
        try:
            kinds = [ArtifactKind(kind)]
        except ValueError as exc:
            from ...domain.errors import ValidationFailed

            raise ValidationFailed(f"未知的产物类型：{kind}") from exc

    artifacts = await projects_svc.list_artifacts(
        session, org_id=principal.org_id, project_id=project_id, kinds=kinds
    )
    return {
        "items": [
            {
                "id": a.id,
                "kind": a.kind.value,
                "filename": a.filename,
                "size_bytes": a.size_bytes,
                "content_type": a.content_type,
                "is_public": a.is_public,
                "shot_key": a.shot_key,
                "created_at": a.created_at.isoformat(),
                "url": f"/api/v1/artifacts/{a.id}/download?disposition=inline",
                "created_hint": parse_ulid_timestamp(a.id).isoformat(),
            }
            for a in artifacts
        ],
        "total": len(artifacts),
    }


@router.post("/api/v1/artifacts/{artifact_id}/share", tags=["artifacts"])
async def toggle_share(
    artifact_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: CurrentPrincipal,
    enable: bool = True,
) -> dict[str, object]:
    """开启/关闭公开分享，返回带签名的分享链接。"""
    import time

    from ...security import issue_share_token

    artifact = (
        await session.execute(
            select(Artifact).where(Artifact.id == artifact_id, Artifact.org_id == principal.org_id)
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise NotFound("产物不存在")

    artifact.is_public = bool(enable)
    if enable:
        ttl = 30 * 24 * 3600
        token = issue_share_token(settings, artifact_id=artifact.id, expires_at=int(time.time()) + ttl)
        artifact.share_token = token[:48]
        url = settings.public_url(f"/s/{token}")
    else:
        artifact.share_token = None
        url = ""

    await session.flush()
    return {"is_public": artifact.is_public, "share_url": url}


@router.get("/api/v1/projects/{project_id}/final", tags=["artifacts"])
async def final_artifact(
    project_id: str,
    session: DbSession,
    principal: CurrentPrincipal,
) -> dict[str, object]:
    """取成片入口，供前端播放器直接用。"""
    from ...services import projects as projects_svc

    await projects_svc.get_project(session, org_id=principal.org_id, project_id=project_id)
    artifact = await projects_svc.find_final_artifact(session, org_id=principal.org_id, project_id=project_id)
    if artifact is None:
        return {"available": False}
    return {
        "available": True,
        "id": artifact.id,
        "kind": artifact.kind.value,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "url": f"/api/v1/artifacts/{artifact.id}/download?disposition=inline",
    }
