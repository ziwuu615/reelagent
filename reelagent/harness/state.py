"""状态持久化：SQLite 支撑的 Checkpoint Store。

这一层解决的问题是：长任务中断后不该从头再来。
  - 每完成一个阶段就落盘，崩溃后从最后一个成功的阶段续跑
  - 镜头级粒度：只重跑失败的那一个镜头，已生成的产物不动
  - 全链路 Tracing：每次模型调用都有 span，能定位慢在哪一步

设计要点：
1. 所有中间产物都落盘，进程被杀掉后用 `resume` 能从最后一个未完成阶段继续。
2. span 表记录每一次 LLM / 工具 / 供应商调用的完整链路，可做耗时与成本归因。
3. 线程安全（并发生成镜头时多线程写同一个库）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schema import Project, Shot

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    started_at  REAL,
    finished_at REAL,
    error       TEXT,
    output      TEXT,
    PRIMARY KEY (project_id, name)
);

CREATE TABLE IF NOT EXISTS shots (
    project_id  TEXT NOT NULL,
    shot_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    data        TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (project_id, shot_id)
);

CREATE TABLE IF NOT EXISTS spans (
    span_id     TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    parent_id   TEXT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    duration_ms REAL,
    attrs       TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_project ON spans(project_id, started_at);

CREATE TABLE IF NOT EXISTS costs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    category    TEXT NOT NULL,
    provider    TEXT,
    model       TEXT,
    unit        TEXT,
    quantity    REAL NOT NULL DEFAULT 0,
    usd         REAL NOT NULL DEFAULT 0,
    ts          REAL NOT NULL,
    meta        TEXT
);
CREATE INDEX IF NOT EXISTS idx_costs_project ON costs(project_id);
"""

STAGE_PENDING = "pending"
STAGE_RUNNING = "running"
STAGE_DONE = "done"
STAGE_FAILED = "failed"


@dataclass
class StageRecord:
    name: str
    status: str
    attempts: int
    started_at: float | None
    finished_at: float | None
    error: str | None
    output: dict[str, Any] | None


class Store:
    """线程安全的 SQLite checkpoint store。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"无法在 {self.path} 打开/创建状态数据库：{exc}。\n"
                "常见原因：该目录不可写（只读盘、容器挂载、沙箱限制）。\n"
                "解决办法：把 workspace 指到一个可写目录，例如 "
                "`REEL_WORKSPACE=./workspace` 或 `--workspace <可写路径>`。"
            ) from exc
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 项目
    # ------------------------------------------------------------------ #
    def save_project(self, project: Project) -> None:
        project.touch()
        payload = json.dumps(project.to_dict(), ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects(id, data, created_at, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (project.id, payload, project.created_at, project.updated_at),
            )
            self._conn.commit()

    def load_project(self, project_id: str) -> Project | None:
        """载入项目，并**以 shots 表为准**校正内嵌剧本里的镜头。

        存在的理由：镜头状态被写在了两个地方 ——
          (a) `shots` 表（Director 逐镜保存，权威）
          (b) `projects.data.script.shots[]`（项目 JSON 里的副本）
        两份副本会分叉。真实踩到的场景：片段文件被改名后只更新了 (a)，
        而 compose() 读的是 (b)，于是拿到失效路径 → clips 为空 →
        **成片停留在旧版本却报告成功**。

        这里在载入时做一次收敛：凡是 shots 表里有记录的镜头，
        一律采用表里的数据。这样 (a) 成为唯一事实来源，
        分叉最多影响落盘的那一份副本，不会再影响运行期行为。
        """
        with self._lock:
            row = self._conn.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                return None
            project = Project.from_dict(json.loads(row["data"]))
            shot_rows = self._conn.execute(
                "SELECT shot_id, data FROM shots WHERE project_id=?", (project_id,)
            ).fetchall()

        if not shot_rows or project.script is None:
            return project

        def _authoritative_shot(raw: str) -> Shot | None:
            """单行坏数据不应让整个项目载入失败，返回 None 表示跳过该行。"""
            try:
                return Shot.from_dict(json.loads(raw))
            except Exception:
                return None

        authoritative: dict[str, Shot] = {}
        for sr in shot_rows:
            shot = _authoritative_shot(sr["data"])
            if shot is not None:
                authoritative[sr["shot_id"]] = shot

        if not authoritative:
            return project

        for scene in project.script.scenes:
            for idx, shot in enumerate(scene.shots):
                latest = authoritative.get(shot.id)
                if latest is not None:
                    scene.shots[idx] = latest
        return project

    def list_projects(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, data, created_at, updated_at FROM projects ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            data = json.loads(r["data"])
            out.append(
                {
                    "id": r["id"],
                    "status": data.get("status", ""),
                    "title": (data.get("script") or {}).get("title", ""),
                    "seed": (data.get("seed_text") or "")[:40],
                    "updated_at": r["updated_at"],
                }
            )
        return out

    def delete_project(self, project_id: str) -> None:
        """删除项目及其全部关联记录。

        表名写成字面量而不是拼进 SQL：一来更清晰，
        二来避免「字符串拼 SQL」这种模式被静态扫描标红，
        将来也不会有人往这里塞变量。
        """
        with self._lock:
            self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            for table in ("stages", "shots", "spans", "costs"):
                self._conn.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))  # noqa: S608
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 阶段 checkpoint
    # ------------------------------------------------------------------ #
    def stage_start(self, project_id: str, name: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO stages(project_id, name, status, attempts, started_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(project_id, name) DO UPDATE SET "
                "status=excluded.status, attempts=stages.attempts+1, started_at=excluded.started_at, "
                "finished_at=NULL, error=NULL",
                (project_id, name, STAGE_RUNNING, 1, now),
            )
            self._conn.commit()

    def stage_done(self, project_id: str, name: str, output: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE stages SET status=?, finished_at=?, error=NULL, output=? WHERE project_id=? AND name=?",
                (STAGE_DONE, time.time(), json.dumps(output or {}, ensure_ascii=False), project_id, name),
            )
            self._conn.commit()

    def stage_failed(self, project_id: str, name: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE stages SET status=?, finished_at=?, error=? WHERE project_id=? AND name=?",
                (STAGE_FAILED, time.time(), error[:2000], project_id, name),
            )
            self._conn.commit()

    def stage_get(self, project_id: str, name: str) -> StageRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM stages WHERE project_id=? AND name=?", (project_id, name)
            ).fetchone()
        if not row:
            return None
        return StageRecord(
            name=row["name"],
            status=row["status"],
            attempts=row["attempts"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            output=json.loads(row["output"]) if row["output"] else None,
        )

    def stages(self, project_id: str) -> list[StageRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM stages WHERE project_id=? ORDER BY started_at", (project_id,)
            ).fetchall()
        return [
            StageRecord(
                r["name"],
                r["status"],
                r["attempts"],
                r["started_at"],
                r["finished_at"],
                r["error"],
                json.loads(r["output"]) if r["output"] else None,
            )
            for r in rows
        ]

    def stage_is_done(self, project_id: str, name: str) -> bool:
        rec = self.stage_get(project_id, name)
        return bool(rec and rec.status == STAGE_DONE)

    def reset_stage(self, project_id: str, name: str) -> None:
        """把某阶段及其之后的状态清空，用于重跑。"""
        with self._lock:
            self._conn.execute("DELETE FROM stages WHERE project_id=? AND name=?", (project_id, name))
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # 镜头
    # ------------------------------------------------------------------ #
    def save_shot(self, project_id: str, shot: Shot) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO shots(project_id, shot_id, status, data, updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(project_id, shot_id) DO UPDATE SET "
                "status=excluded.status, data=excluded.data, updated_at=excluded.updated_at",
                (
                    project_id,
                    shot.id,
                    shot.status,
                    json.dumps(shot.to_dict(), ensure_ascii=False),
                    time.time(),
                ),
            )
            self._conn.commit()

    def load_shots(self, project_id: str) -> dict[str, Shot]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT shot_id, data FROM shots WHERE project_id=?", (project_id,)
            ).fetchall()
        return {r["shot_id"]: Shot.from_dict(json.loads(r["data"])) for r in rows}

    def pending_shots(self, project_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT shot_id FROM shots WHERE project_id=? AND status NOT IN (?,?)",
                (project_id, "clip_done", "audio_done"),
            ).fetchall()
        return [r["shot_id"] for r in rows]

    # ------------------------------------------------------------------ #
    # Span（可观测性）
    # ------------------------------------------------------------------ #
    def record_span(
        self,
        *,
        span_id: str,
        project_id: str,
        parent_id: str | None,
        name: str,
        kind: str,
        status: str,
        started_at: float,
        ended_at: float,
        attrs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO spans"
                "(span_id, project_id, parent_id, name, kind, status, started_at, ended_at, duration_ms, attrs, error) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    span_id,
                    project_id,
                    parent_id,
                    name,
                    kind,
                    status,
                    started_at,
                    ended_at,
                    (ended_at - started_at) * 1000,
                    json.dumps(attrs or {}, ensure_ascii=False),
                    error,
                ),
            )
            self._conn.commit()

    def spans(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spans WHERE project_id=? ORDER BY started_at", (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def span_stats(self, project_id: str) -> dict[str, dict[str, float]]:
        """按 span.name 聚合耗时，做性能归因。"""
        stats: dict[str, dict[str, float]] = {}
        for row in self.spans(project_id):
            bucket = stats.setdefault(row["name"], {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "failed": 0})
            bucket["count"] += 1
            bucket["total_ms"] += row["duration_ms"] or 0.0
            bucket["max_ms"] = max(bucket["max_ms"], row["duration_ms"] or 0.0)
            if row["status"] != "ok":
                bucket["failed"] += 1
        return stats

    # ------------------------------------------------------------------ #
    # 成本
    # ------------------------------------------------------------------ #
    def record_cost(
        self,
        *,
        project_id: str,
        category: str,
        provider: str = "",
        model: str = "",
        unit: str = "",
        quantity: float = 0.0,
        usd: float = 0.0,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO costs(project_id, category, provider, model, unit, quantity, usd, ts, meta) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    category,
                    provider,
                    model,
                    unit,
                    quantity,
                    usd,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def cost_summary(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT category, provider, model, unit, SUM(quantity) qty, SUM(usd) usd, COUNT(*) n "
                "FROM costs WHERE project_id=? GROUP BY category, provider, model, unit",
                (project_id,),
            ).fetchall()
            total_row = self._conn.execute(
                "SELECT COALESCE(SUM(usd),0) t FROM costs WHERE project_id=?", (project_id,)
            ).fetchone()
        by_category: dict[str, dict[str, Any]] = {}
        for r in rows:
            cat = by_category.setdefault(r["category"], {"usd": 0.0, "items": []})
            cat["usd"] += r["usd"] or 0.0
            cat["items"].append(
                {
                    "provider": r["provider"],
                    "model": r["model"],
                    "unit": r["unit"],
                    "quantity": round(r["qty"] or 0.0, 3),
                    "usd": round(r["usd"] or 0.0, 4),
                    "calls": r["n"],
                }
            )
        return {
            "total_usd": round(total_row["t"] or 0.0, 4),
            "by_category": {
                k: {"usd": round(v["usd"], 4), "items": v["items"]} for k, v in by_category.items()
            },
        }

    def list_costs(self, project_id: str) -> list[dict[str, Any]]:
        """返回**逐次调用**的原始成本记录（不做聚合）。

        与 `cost_summary` 的关键区别：后者按 (category, provider, model, unit)
        分组求和，会丢掉 `meta` 里的 `shot_id` —— 而「这笔钱花在哪个镜头上」
        恰恰是成本归因最需要的维度。聚合值适合给用户看总数，
        原始行才适合做按镜头、按供应商、按成功率的分析。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT category, provider, model, unit, quantity, usd, ts, meta "
                "FROM costs WHERE project_id=? ORDER BY id",
                (project_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                meta = json.loads(r["meta"] or "{}")
            except (TypeError, ValueError):
                meta = {}
            out.append(
                {
                    "category": r["category"],
                    "provider": r["provider"] or "",
                    "model": r["model"] or "",
                    "unit": r["unit"] or "",
                    "quantity": float(r["quantity"] or 0.0),
                    "usd": float(r["usd"] or 0.0),
                    "ts": float(r["ts"] or 0.0),
                    "meta": meta if isinstance(meta, dict) else {},
                }
            )
        return out

    def list_provider_spans(self, project_id: str) -> list[dict[str, Any]]:
        """返回代表**真实供应商调用**的 span（成功与失败都有）。

        为什么需要它：`costs` 表只记录**成功调用**的金额 —— 失败不产生成本行。
        于是「失败率」「延迟」「错误码」这三个维度在 costs 里根本不存在。
        而它们恰恰是运维分析最需要的（见 docs/OPERATIONS.md 的排障查询）。

        `kind='tool'` 是工具层调用，对应一次真实的供应商请求。
        其余 kind（stage/agent/retry/internal）是编排结构，不是调用本身。

        注意一个已知的信息缺口：**失败的 span 常常没有 `provider` / `model` 属性**
        （属性是在成功后由结果写入的）。这里如实返回空字符串，而不是拿
        当前配置去回填 —— 那会产生「看起来有归属、实际是猜的」数据。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, status, duration_ms, attrs, error FROM spans "
                "WHERE project_id=? AND kind='tool' ORDER BY rowid",
                (project_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                attrs = json.loads(r["attrs"] or "{}")
            except (TypeError, ValueError):
                attrs = {}
            if not isinstance(attrs, dict):
                attrs = {}
            out.append(
                {
                    "name": r["name"] or "",
                    "status": r["status"] or "",
                    "duration_ms": float(r["duration_ms"]) if r["duration_ms"] is not None else None,
                    "provider": str(attrs.get("provider") or ""),
                    "model": str(attrs.get("model") or ""),
                    "shot_id": str(attrs.get("shot_id") or ""),
                    "error": str(r["error"] or ""),
                }
            )
        return out

    # ------------------------------------------------------------------ #
    def summary(self, project_id: str) -> dict[str, Any]:
        stages = self.stages(project_id)
        shots = self.load_shots(project_id)
        return {
            "project_id": project_id,
            "stages": [
                {"name": s.name, "status": s.status, "attempts": s.attempts, "error": s.error} for s in stages
            ],
            "shots": {
                "total": len(shots),
                "done": sum(1 for s in shots.values() if s.is_finished),
                "failed": sum(1 for s in shots.values() if s.status == "failed"),
            },
            "cost": self.cost_summary(project_id),
            "spans": len(self.spans(project_id)),
        }
