"""config 模块单测：IP 尾段校验与时区规范化。

覆盖 AGENTS.md §7 要求的纯逻辑函数：IP 校验、时区映射。
"""

from __future__ import annotations

import pytest

from auto_vmware.config import (
    IP_LAST_FORBIDDEN,
    IP_LAST_MAX_SAFE,
    ConfigError,
    VmSpec,
    normalize_timezone,
    validate_ip_last,
)


# ---------------------------------------------------------------------------
# validate_ip_last
# ---------------------------------------------------------------------------
class TestValidateIpLast:
    """IP 尾段校验。"""

    @pytest.mark.parametrize("n", [3, 10, 50, IP_LAST_MAX_SAFE])
    def test_safe_range_ok(self, n: int) -> None:
        assert validate_ip_last(n) == n

    @pytest.mark.parametrize("forbidden", sorted(IP_LAST_FORBIDDEN))
    def test_forbidden_values_raise(self, forbidden: int) -> None:
        with pytest.raises(ConfigError, match="不可用"):
            validate_ip_last(forbidden)

    @pytest.mark.parametrize("bad", [-1, 256, 1000])
    def test_out_of_range_raises(self, bad: int) -> None:
        with pytest.raises(ConfigError, match="0–255"):
            validate_ip_last(bad)

    def test_dhcp_range_allowed_but_warns(self) -> None:
        """128–254 与 DHCP 冲突，但允许（告警由调用方处理）。"""
        assert validate_ip_last(200) == 200

    def test_non_int_raises(self) -> None:
        with pytest.raises(ConfigError):
            validate_ip_last("50")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# normalize_timezone: 正常解析
# ---------------------------------------------------------------------------
class TestNormalizeTimezoneValid:
    """城市名/IANA 名/别名 → IANA 时区。"""

    @pytest.mark.parametrize(
        "city,expected",
        [
            # 裸城市名后缀匹配（Lagos 这次装机的实际用例）
            ("Lagos", "Africa/Lagos"),
            ("Shanghai", "Asia/Shanghai"),
            ("Tokyo", "Asia/Tokyo"),
            ("London", "Europe/London"),
            ("Paris", "Europe/Paris"),
            ("Dubai", "Asia/Dubai"),
            ("Singapore", "Singapore"),
        ],
    )
    def test_plain_city(self, city: str, expected: str) -> None:
        assert normalize_timezone(city) == expected

    def test_case_insensitive_city(self) -> None:
        """用户小写传城市名也应能解析。"""
        assert normalize_timezone("lagos") == "Africa/Lagos"
        assert normalize_timezone("TOKYO") == "Asia/Tokyo"

    @pytest.mark.parametrize(
        "tz,expected",
        [
            # 空格归一化为下划线（IANA 区名用下划线）
            ("New York", "America/New_York"),
            ("Los Angeles", "America/Los_Angeles"),
            ("Mexico City", "America/Mexico_City"),
            # 下划线本身也支持
            ("New_York", "America/New_York"),
        ],
    )
    def test_space_to_underscore(self, tz: str, expected: str) -> None:
        assert normalize_timezone(tz) == expected

    def test_already_iana(self) -> None:
        """已是合法 IANA 名则原样返回。"""
        assert normalize_timezone("Asia/Shanghai") == "Asia/Shanghai"
        assert normalize_timezone("Africa/Lagos") == "Africa/Lagos"
        assert normalize_timezone("America/New_York") == "America/New_York"

    @pytest.mark.parametrize(
        "alias,expected",
        [
            # IANA 未收录的中国城市 → Asia/Shanghai
            ("Beijing", "Asia/Shanghai"),
            ("Hangzhou", "Asia/Shanghai"),
            ("Shenzhen", "Asia/Shanghai"),
            # 印度旧殖民名
            ("Bombay", "Asia/Kolkata"),
            ("Madras", "Asia/Kolkata"),
            # 越南旧名
            ("Saigon", "Asia/Ho_Chi_Minh"),
            # 英文简称别名
            ("NYC", "America/New_York"),
        ],
    )
    def test_aliases(self, alias: str, expected: str) -> None:
        assert normalize_timezone(alias) == expected

    def test_validates_result_is_loadable(self) -> None:
        """返回值必须是 zoneinfo 可加载的真实时区。"""
        from zoneinfo import ZoneInfo

        for tz in ["Lagos", "Beijing", "New York", "Asia/Shanghai"]:
            result = normalize_timezone(tz)
            ZoneInfo(result)  # 不抛即通过


# ---------------------------------------------------------------------------
# normalize_timezone: 错误处理
# ---------------------------------------------------------------------------
class TestNormalizeTimezoneErrors:
    """无匹配/多匹配/空输入 → 抛 ConfigError（不静默回退 UTC）。"""

    def test_empty_raises(self) -> None:
        with pytest.raises(ConfigError, match="不能为空"):
            normalize_timezone("")
        with pytest.raises(ConfigError, match="不能为空"):
            normalize_timezone("   ")

    def test_no_match_raises_not_utc_fallback(self) -> None:
        """关键：无匹配必须报错，绝不能静默退成 UTC。"""
        with pytest.raises(ConfigError, match="无法将时区"):
            normalize_timezone("NotARealCity12345")

    def test_multiple_match_raises(self) -> None:
        """多匹配（如 Buenos_Aires）应报错让用户明确指定，不猜。"""
        with pytest.raises(ConfigError, match="多个"):
            normalize_timezone("Buenos_Aires")

    def test_multiple_match_lists_options(self) -> None:
        """错误信息应列出候选，方便用户选择。"""
        with pytest.raises(ConfigError) as exc_info:
            normalize_timezone("Istanbul")
        msg = str(exc_info.value)
        assert "Europe/Istanbul" in msg
        assert "Asia/Istanbul" in msg

    def test_invalid_iana_with_slash_raises(self) -> None:
        """带 / 但不是合法 IANA 名也应报错。"""
        with pytest.raises(ConfigError):
            normalize_timezone("Asia/NotARealCity")


# ---------------------------------------------------------------------------
# VmSpec 集成：__post_init__ 调用 normalize_timezone
# ---------------------------------------------------------------------------
class TestVmSpecTimezone:
    """VmSpec 构造时自动规范化时区。"""

    def _make(self, timezone: str) -> VmSpec:
        return VmSpec(
            name="test-vm",
            username="user",
            password="pass",
            timezone=timezone,
            ip_last=10,
        )

    def test_normalizes_city_to_iana(self) -> None:
        spec = self._make("Lagos")
        assert spec.timezone == "Africa/Lagos"

    def test_normalizes_beijing(self) -> None:
        spec = self._make("Beijing")
        assert spec.timezone == "Asia/Shanghai"

    def test_normalizes_space_name(self) -> None:
        spec = self._make("New York")
        assert spec.timezone == "America/New_York"

    def test_invalid_timezone_raises_in_spec(self) -> None:
        with pytest.raises(ConfigError):
            self._make("NotARealCity12345")
