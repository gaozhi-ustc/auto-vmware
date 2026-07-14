"""provision 模块单测：VNC 密码截断等纯逻辑函数。"""

from __future__ import annotations

import pytest

from auto_vmware.provision import _vnc_password


class TestVncPassword:
    """VNC 密码截断逻辑。

    VNC 协议密码上限 8 字符。关键：不足 8 位时不能补零，
    否则密码内容被改变，客户端用原密码登录会失败。
    """

    def test_short_password_not_padded(self) -> None:
        """7 位密码保持原样，不补零（这正是 avm-lagos 的 fbi4587 用例）。"""
        assert _vnc_password("fbi4587") == "fbi4587"
        assert len(_vnc_password("fbi4587")) == 7

    def test_exact_8_chars_unchanged(self) -> None:
        """恰好 8 位原样返回。"""
        assert _vnc_password("12345678") == "12345678"

    def test_long_password_truncated(self) -> None:
        """超过 8 位截断到 8。"""
        assert _vnc_password("abcdefghij") == "abcdefgh"
        assert len(_vnc_password("abcdefghij")) == 8

    def test_empty_password(self) -> None:
        assert _vnc_password("") == ""

    @pytest.mark.parametrize(
        "pw,expected",
        [
            ("fbi4587", "fbi4587"),
            ("pass", "pass"),
            ("12345678", "12345678"),
            ("verylongpassword", "verylong"),
        ],
    )
    def test_various_lengths(self, pw: str, expected: str) -> None:
        assert _vnc_password(pw) == expected
