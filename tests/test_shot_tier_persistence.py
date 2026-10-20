"""镜头级字段从引擎到界面的持久化链路测试。

**这个项目里反复出现同一类缺陷：一条数据链路有好几层，其中一层没接上，
而失败是静默的。**

已经踩过的坑：
  - 智谱把 `request_id` 当成 task_id（上游字段没映射对）
  - 片段存成 `.gif` 却是 MP4 内容（后缀没跟随供应商）
  - 云端 `build_engine_settings` 只查系统 ffmpeg（配置解析两处不一致）
  - `force_tier` 在流水线里被读、却从没有任何地方写（能力整条断开）
  - `shots.tier` 列和模板都写好了，但引擎不产出、桥接不映射

共同点：**接口存在、类型正确、测试全绿，但数据永远是空的。**
所以这里专门针对「最后一公里有没有接上」写断言。
"""

from __future__ import annotations

import dataclasses

import pytest

from reelagent.schema import Shot
from reelagent_cloud.db.models import Shot as CloudShot
from reelagent_cloud.services.projects import _apply_shot_data


class TestShotTierFieldExists:
    """引擎侧必须真的产出 tier。"""

    def test_shot_has_tier_field(self) -> None:
        names = {f.name for f in dataclasses.fields(Shot)}
        assert "tier" in names, "Shot 缺少 tier 字段，云端 shots.tier 列将永远是空的"

    def test_tier_defaults_to_empty(self) -> None:
        """默认空串：旧数据与未路由的镜头不应报错。"""
        shot = Shot(id="sc01_sh01", scene_id="sc01", index=0)
        assert shot.tier == ""

    def test_tier_is_assignable(self) -> None:
        shot = Shot(id="sc01_sh01", scene_id="sc01", index=0)
        shot.tier = "premium"
        assert shot.tier == "premium"


class TestCloudBridgeMapsTier:
    """桥接必须把 tier 写进数据库列 —— 这是最容易漏掉的一层。"""

    def _row(self) -> CloudShot:
        return CloudShot(
            id="shot_01",
            project_id="proj_01",
            org_id="org_01",
            shot_key="sc01_sh01",
            order_index=0,
        )

    def test_tier_is_mapped(self) -> None:
        row = self._row()
        _apply_shot_data(row, {"id": "sc01_sh01", "tier": "draft"}, 0)
        assert row.tier == "draft"

    def test_missing_tier_does_not_crash(self) -> None:
        """引擎旧版本或 mock 路径可能不带 tier，不能让整条流水线崩掉。"""
        row = self._row()
        _apply_shot_data(row, {"id": "sc01_sh01"}, 0)
        assert row.tier == ""

    def test_none_tier_becomes_empty(self) -> None:
        row = self._row()
        _apply_shot_data(row, {"id": "sc01_sh01", "tier": None}, 0)
        assert row.tier == ""

    @pytest.mark.parametrize("tier", ["draft", "standard", "premium"])
    def test_all_known_tiers_round_trip(self, tier: str) -> None:
        row = self._row()
        _apply_shot_data(row, {"id": "sc01_sh01", "tier": tier}, 0)
        assert row.tier == tier

    def test_other_fields_still_mapped(self) -> None:
        """确认新增映射没有挤掉既有字段的赋值顺序。"""
        row = self._row()
        _apply_shot_data(
            row,
            {
                "id": "sc01_sh01",
                "tier": "standard",
                "status": "audio_done",
                "provider_used": "dashscope",
                "model_used": "wan3.0-video",
                "cost_usd": 0.42,
                "attempts": 2,
            },
            0,
        )
        assert row.status == "audio_done"
        assert row.provider == "dashscope"
        assert row.model == "wan3.0-video"
        assert row.cost_usd == 0.42
        assert row.attempts == 2
        assert row.tier == "standard"


class TestUIHasSomethingToShow:
    """模板里确实有展示档位的位置，否则填了数据也看不见。"""

    def test_studio_template_renders_tier(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        tpl = (root / "reelagent_cloud/web/templates/console/studio.html").read_text(encoding="utf-8")
        assert "shot.tier" in tpl
