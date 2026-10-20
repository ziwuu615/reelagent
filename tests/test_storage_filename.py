"""存储键清洗函数的测试。

`sanitize_filename` 是个**安全边界**函数：它清洗的名字会直接当作文件路径和
S3 key 使用。它同时承担两个相互拉扯的目标：

    安全  —— 必须杜绝路径穿越、Windows 保留设备名、结尾点号等落盘陷阱
    可用  —— 必须保留中文等 Unicode 字母，否则用户下载时文件名毫无意义

早期版本为了「安全」只允许 `[A-Za-z0-9._-]`，结果整段中文被压成一个「-」：
「便利店霓虹灯」变成 `clue_-_211f.png`。这说明**过度清洗同样是缺陷** ——
它没有报错，只是悄悄丢掉了信息。

因此这组测试从两侧同时施压：既要拦住攻击，也要放过正常的中文名。
"""

from __future__ import annotations

import pytest

from reelagent_cloud.services.storage import (
    _MAX_NAME_BYTES,
    build_key,
    key_belongs_to_org,
    sanitize_filename,
)


class TestPreservesReadableNames:
    """中文必须原样保留 —— 这是本次修复的核心目标。"""

    @pytest.mark.parametrize(
        "name",
        [
            "clue_便利店霓虹灯_6cd5.png",
            "clue_女孩_5d97.png",
            "clue_雨夜积水路面_5590.png",
            "红伞.png",
            "收银老人.keyframe.png",
        ],
    )
    def test_cjk_survives(self, name: str) -> None:
        assert sanitize_filename(name) == name

    def test_ascii_is_untouched(self) -> None:
        assert sanitize_filename("final.mp4") == "final.mp4"
        assert sanitize_filename("sc01_sh01.mp4") == "sc01_sh01.mp4"

    def test_mixed_cjk_and_ascii(self) -> None:
        """中英混排是最常见的形态，不能因为含中文就把英文也吃掉。"""
        assert sanitize_filename("storyboard_分镜_v2.png") == "storyboard_分镜_v2.png"

    def test_other_scripts(self) -> None:
        """不只中文：日文、韩文、西里尔字母同样应当保留。"""
        assert sanitize_filename("シーン_01.png") == "シーン_01.png"
        assert sanitize_filename("장면_01.png") == "장면_01.png"


class TestBlocksPathTraversal:
    """安全底线：这些必须全部被拦住。"""

    @pytest.mark.parametrize(
        ("hostile", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("/abs/path/y.png", "y.png"),
            ("a/b/c.png", "c.png"),
            ("./x.png", "x.png"),
        ],
    )
    def test_posix_separators(self, hostile: str, expected: str) -> None:
        assert sanitize_filename(hostile) == expected

    @pytest.mark.parametrize(
        "hostile",
        [
            r"..\..\windows\system32\x.dll",
            r"a\b\c.png",
            r"\\server\share\evil.exe",
        ],
    )
    def test_windows_separators(self, hostile: str) -> None:
        """反斜杠必须被处理。

        这条容易漏：POSIX 下的 `Path(name).name` 不认反斜杠，
        会把整个 `..\\..\\etc\\passwd` 当成一个普通文件名保留下来。
        """
        cleaned = sanitize_filename(hostile)
        assert "\\" not in cleaned
        assert "/" not in cleaned
        assert ".." not in cleaned

    def test_no_result_ever_escapes(self) -> None:
        """总括断言：输出永远不能带有路径语义。

        判定标准是「不含任何路径分隔符」且「本身不是 `.` 或 `..`」。
        不要求结果里完全不出现 `..` —— 形如 `2f..-2fetc` 这种名字中间带点
        并没有穿越能力，因为已经没有分隔符可以配合它了。
        """
        hostile_inputs = [
            "../../../root/.ssh/id_rsa",
            "....//....//etc/passwd",
            "a\u0000b.png",
            "..%2f..%2fetc",
            "／fullwidth／slash",
            "..",
            ".",
            "..\\",
        ]
        for raw in hostile_inputs:
            cleaned = sanitize_filename(raw)
            assert "/" not in cleaned, raw
            assert "\\" not in cleaned, raw
            assert "\x00" not in cleaned, raw
            assert cleaned not in {".", ".."}, raw

    def test_fullwidth_separator_is_treated_as_separator(self) -> None:
        """全角斜杠经 NFKC 会变成真斜杠，必须在拆分阶段就被当作分隔符。"""
        assert sanitize_filename("dir／file.png") == "file.png"


class TestCrossPlatformSafety:
    def test_windows_reserved_device_names(self) -> None:
        """CON/NUL/COM1 等在 Windows 上无法创建，必须加前缀规避。"""
        reserved_set = {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10)),
        }
        for reserved in ("CON", "con", "NUL", "nul.txt", "COM1", "LPT9", "aux.log"):
            cleaned = sanitize_filename(reserved)
            # 清洗后的主名不能**恰好**是保留名（加了下划线前缀即视为安全）
            assert cleaned.split(".", 1)[0].lower() not in reserved_set, f"{reserved} 未被规避：{cleaned}"

    @pytest.mark.parametrize("raw", ["trailing.", "trailing...", "trailing ", "trailing . "])
    def test_strips_trailing_dot_and_space(self, raw: str) -> None:
        """Windows 不允许文件名以点或空格结尾。"""
        cleaned = sanitize_filename(raw)
        assert not cleaned.endswith(".")
        assert not cleaned.endswith(" ")

    def test_byte_length_limit(self) -> None:
        """限长按**字节**：中文一个字符占 3 字节，按字符限制会实际超限。"""
        long_cjk = "很长的线索名称" * 40 + ".png"
        cleaned = sanitize_filename(long_cjk)
        assert len(cleaned.encode("utf-8")) <= _MAX_NAME_BYTES

    def test_truncation_keeps_extension(self) -> None:
        """截断不能吃掉扩展名 —— 丢掉 .png/.mp4 会让下游无法判断类型。"""
        long_cjk = "很长的线索名称" * 40 + ".png"
        assert sanitize_filename(long_cjk).endswith(".png")

    def test_truncation_does_not_split_multibyte_char(self) -> None:
        """按字节截断时必须退到字符边界，不能产生半个字符。"""
        long_cjk = "中" * 200 + ".png"
        cleaned = sanitize_filename(long_cjk)
        cleaned.encode("utf-8").decode("utf-8")  # 不抛异常即说明未截断多字节字符
        assert "�" not in cleaned


class TestFallbackBehaviour:
    @pytest.mark.parametrize("raw", ["", "   ", "...", "   .  ", "\x00", "///"])
    def test_empty_input_falls_back(self, raw: str) -> None:
        assert sanitize_filename(raw) == "file"

    def test_custom_fallback(self) -> None:
        assert sanitize_filename("", fallback="unknown") == "unknown"


class TestBuildKeyIntegration:
    """build_key 组合后的键必须既合法又保留可读性。"""

    def test_key_contains_readable_cjk(self) -> None:
        key = build_key(
            org_id="org_01ABC",
            project_id="proj_01XYZ",
            category="assets",
            filename="clue_便利店霓虹灯_6cd5.png",
        )
        assert key == "org/org_01ABC/projects/proj_01XYZ/assets/clue_便利店霓虹灯_6cd5.png"

    def test_key_cannot_escape_org_prefix(self) -> None:
        """即使 filename 试图穿越，最终 key 仍必须落在本组织前缀下。"""
        key = build_key(
            org_id="org_01ABC",
            project_id="proj_01XYZ",
            category="assets",
            filename="../../../../other_org/secret.png",
        )
        assert key.startswith("org/org_01ABC/")
        assert ".." not in key

    def test_key_belongs_to_org_still_works(self) -> None:
        key = build_key(org_id="org_01ABC", project_id=None, category="final", filename="final.mp4")
        assert key_belongs_to_org(key, "org_01ABC") is True
        assert key_belongs_to_org(key, "org_OTHER") is False
