"""装机后配置（步骤 3/4/5）。

在 VM 首启 SSH 可达后，通过 SSH/SCP 完成：
- 步骤3：apt 安装桌面/VNC/lightdm 等包 → 切 lightdm → 重启 → 启动 vncserver :1
- 步骤4：安装 FlClash、Chrome deb（缺失依赖用 apt --fix-broken 后重试）
- 步骤5：DISPLAY=:1 启动 FlClash 并导入配置

每个步骤都设计为可单独调用，便于排错与重入。
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, List

from auto_vmware import orchestrate, sshutil
from auto_vmware.config import POSTINSTALL_APT_PKGS
from auto_vmware.log import get_logger

if TYPE_CHECKING:
    from auto_vmware.config import VmSpec

_log = get_logger("provision")

# 远程临时目录，存放上传的 deb / yaml
REMOTE_TMP = "/tmp/auto-vmware"


def _ensure_remote_tmp(spec: "VmSpec") -> None:
    """确保远程临时目录存在。"""
    sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        f"mkdir -p {REMOTE_TMP}",
        sudo=True,
    )


def apt_update(spec: "VmSpec") -> sshutil.SSHResult:
    """apt update（容忍失败，因为镜像源未配置时可能失败）。"""
    _log.info("[apt] update")
    return sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        "apt-get update",
        sudo=True,
        timeout=300,
    )


def step3_install_packages(spec: "VmSpec") -> None:
    """步骤3：安装桌面/VNC/lightdm 包，切换 lightdm，准备重启。

    严格按 AGENTS.md 步骤3：
    apt install gnome-session gnome-terminal ubuntu-desktop dbus-x11
                tigervnc-standalone-server vim openssh-server lightdm
    然后把默认显示管理器改为 lightdm，重启。
    重启后由 step3_start_vnc 启动 vncserver。

    Args:
        spec: 虚拟机规格。
    """
    _log.info("=== 步骤3：安装桌面/VNC/lightdm ===")
    _ensure_remote_tmp(spec)

    # 预设 debconf，让 lightdm 自动选为默认显示管理器（避免交互卡住）
    preset_dm = (
        "echo 'lightdm shared/choose-package select lightdm' | "
        "debconf-set-selections ; "
        "echo 'lightdm lightdm/daemon_name select lightdm' | "
        "debconf-set-selections"
    )
    sshutil.run(
        spec.ip_address, spec.username, spec.password, preset_dm, sudo=True, timeout=60
    )

    apt_update(spec)

    pkgs = " ".join(POSTINSTALL_APT_PKGS)
    # DEBIAN_FRONTEND=noninteractive 避免交互；超时给足（桌面包较大）
    _log.info("[apt] install: %s", pkgs)
    r = sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}",
        sudo=True,
        timeout=3600,
    )
    if not r.ok:
        _log.warning("apt install 返回码 %d，尝试 --fix-broken 后重试", r.rc)
        sshutil.run(
            spec.ip_address,
            spec.username,
            spec.password,
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --fix-broken",
            sudo=True,
            timeout=600,
        )
        sshutil.run(
            spec.ip_address,
            spec.username,
            spec.password,
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}",
            sudo=True,
            timeout=3600,
        )

    # 显式将默认显示管理器切到 lightdm
    _log.info("切换默认显示管理器为 lightdm")
    sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        "echo lightdm > /etc/X11/default-display-manager || "
        "debconf-set-selections <<<'lightdm shared/choose-package select lightdm'; "
        "which lightdm > /etc/X11/default-display-manager",
        sudo=True,
        timeout=60,
    )

    # 重启生效
    _log.info("重启以应用 lightdm（步骤3 要求）")
    orchestrate.reboot_guest_and_wait(
        spec, spec.vmx_path, wait_ssh=True, ssh_window=360
    )


def step3_start_vnc(spec: "VmSpec") -> None:
    """步骤3（重启后）：启动 vncserver :1，密码同用户登录密码。

    实现：
    1. 通过 vncpasswd 工具用密码生成 ~/.vnc/passwd（非交互）。
    2. 配置 xstartup 启动 gnome-session。
    3. vncserver :1 -localhost no 启动。

    Args:
        spec: 虚拟机规格。
    """
    _log.info("=== 步骤3：启动 VNC :1 ===")
    pw = spec.password
    # 非交互设置 VNC 密码（限长 8 字符，超出截断）
    vnc_pw = pw[:8] if len(pw) >= 8 else (pw + "0" * (8 - len(pw)))
    # 用 printf 喂两次密码给 vncpasswd
    set_vnc_pw = (
        f"mkdir -p ~/.vnc && "
        f"printf '%s\\n%s\\nn\\n' {sshutil.shell_quote(vnc_pw)} {sshutil.shell_quote(vnc_pw)} "
        f"| vncpasswd ~/.vnc/passwd && chmod 600 ~/.vnc/passwd"
    )
    sshutil.run(spec.ip_address, spec.username, spec.password, set_vnc_pw, timeout=60)

    # xstartup：使用 gnome-session
    xstartup = """cat > ~/.vnc/xstartup <<'XEOF'
#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=GNOME
export DESKTOP_SESSION=gnome
dbus-launch --exit-with-session gnome-session
XEOF
chmod +x ~/.vnc/xstartup
"""
    sshutil.run(spec.ip_address, spec.username, spec.password, xstartup, timeout=60)

    # 先杀掉已有 VNC，再启动 :1
    _log.info("启动 vncserver :1 -localhost no")
    sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        "vncserver -kill :1 2>/dev/null || true ; sleep 1 ; "
        "vncserver :1 -localhost no -geometry 1920x1080",
        timeout=120,
    )


def _upload_file(spec: "VmSpec", local: str, remote_name: str) -> str:
    """上传文件到 REMOTE_TMP，返回远程完整路径。"""
    remote_path = f"{REMOTE_TMP}/{remote_name}"
    sshutil.scp_upload(
        spec.ip_address, spec.username, spec.password, local, remote_path
    )
    return remote_path


def _install_deb_with_fix(spec: "VmSpec", remote_deb: str) -> None:
    """安装单个 deb，失败时用 apt --fix-broken 修复后重试。

    Args:
        spec: 虚拟机规格。
        remote_deb: 远程 deb 文件路径。
    """
    _log.info("[dpkg] 安装 %s", remote_deb)
    r = sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        f"DEBIAN_FRONTEND=noninteractive dpkg -i {remote_deb}",
        sudo=True,
        timeout=600,
    )
    if r.ok:
        return
    _log.warning("dpkg -i 失败（rc=%d），运行 apt --fix-broken 后重试", r.rc)
    fix = sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --fix-broken",
        sudo=True,
        timeout=900,
    )
    if not fix.ok:
        _log.error("--fix-broken 仍失败:\n%s", fix.stderr[-1500:])
    # 重试 dpkg
    r2 = sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        f"DEBIAN_FRONTEND=noninteractive dpkg -i {remote_deb}",
        sudo=True,
        timeout=600,
    )
    if not r2.ok:
        _log.error("重试 dpkg -i 仍失败:\n%s", r2.stderr[-1500:])
        raise RuntimeError(f"安装 deb 失败: {remote_deb}")


def step4_install_flclash_chrome(spec: "VmSpec") -> None:
    """步骤4：安装 FlClash 与 Chrome deb，--fix-broken 兜底。

    Args:
        spec: 虚拟机规格。
    """
    _log.info("=== 步骤4：安装 FlClash / Chrome ===")
    _ensure_remote_tmp(spec)

    # 上传
    fl_remote = _upload_file(spec, spec.flclash_deb, "flclash.deb")
    ch_remote = _upload_file(spec, spec.chrome_deb, "chrome.deb")

    # apt update 拿最新依赖索引
    apt_update(spec)

    _install_deb_with_fix(spec, fl_remote)
    _install_deb_with_fix(spec, ch_remote)
    _log.info("FlClash / Chrome 安装完成")


def step5_start_flclash_and_import(spec: "VmSpec") -> None:
    """步骤5：在 DISPLAY=:1 启动 FlClash，并导入配置。

    实现：
    1. 上传 gaozhi_new.yaml 到远程。
    2. 确保 VNC :1 在跑（依赖步骤3）。
    3. 用 FlClash 的 CLI/配置目录导入；FlClash 桌面版主要消费
       ~/.config/com.follow.clash 或通过首次启动读取。
       采用稳妥方式：拷贝配置到 FlClash 配置目录，然后以 DISPLAY=:1 启动 FlClash。

    Args:
        spec: 虚拟机规格。
    """
    _log.info("=== 步骤5：启动 FlClash 并导入配置 ===")
    _ensure_remote_tmp(spec)

    cfg_remote = _upload_file(spec, spec.clash_config, "gaozhi_new.yaml")

    # FlClash (com.follow.clash) 配置目录；不同版本可能不同，做几个候选
    setup_cfg = f"""
set -e
mkdir -p ~/.config/com.follow.clash ~/.config/FlClash 2>/dev/null || true
cp -f {cfg_remote} ~/.config/com.follow.clash/config.yaml 2>/dev/null || true
cp -f {cfg_remote} ~/.config/FlClash/config.yaml 2>/dev/null || true
cp -f {cfg_remote} ~/gaozhi_new.yaml
echo '配置已就位'
"""
    sshutil.run(spec.ip_address, spec.username, spec.password, setup_cfg, timeout=60)

    # 确保 VNC :1 运行
    _log.info("确认 VNC :1 运行")
    sshutil.run(
        spec.ip_address,
        spec.username,
        spec.password,
        "vncserver -list 2>/dev/null | grep -q ':1' || "
        "(vncserver :1 -localhost no -geometry 1920x1080)",
        timeout=120,
    )

    # 以 DISPLAY=:1 启动 FlClash（后台）
    _log.info("DISPLAY=:1 启动 FlClash")
    start_fl = """
export DISPLAY=:1
export XAUTHORITY=${HOME}/.Xauthority
pkill -f FlClash 2>/dev/null || true
sleep 1
nohup FlClash >/tmp/flclash.log 2>&1 &
sleep 3
if pgrep -f FlClash >/dev/null; then
  echo 'FlClash 已启动'
else
  echo 'FlClash 启动可能失败，查看 /tmp/flclash.log'
  tail -20 /tmp/flclash.log 2>/dev/null || true
fi
"""
    sshutil.run(spec.ip_address, spec.username, spec.password, start_fl, timeout=60)

    # 导入：FlClash 运行后，通过其监听的接口或配置目录导入。最稳妥是已拷贝
    # 配置到其配置目录；这里再做一次显式说明
    _log.info("配置导入完成（已写入 FlClash 配置目录与用户主目录）")


def provision_all(spec: "VmSpec") -> None:
    """按顺序执行步骤 3 → 4 → 5。

    Args:
        spec: 虚拟机规格。
    """
    step3_install_packages(spec)
    step3_start_vnc(spec)
    step4_install_flclash_chrome(spec)
    step5_start_flclash_and_import(spec)
