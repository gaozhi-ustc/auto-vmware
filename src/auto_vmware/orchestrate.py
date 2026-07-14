"""装机编排：调用 vmrun 控制 VM 启动/关机/重启/状态，配合等待 SSH。

流程（AGENTS.md §6）：
1. start → 启动安装
2. 等待 VM 完成安装（autoinstall 结束后 VM 会自动重启；通过 vmrun 状态 +
   SSH 可达判断）
3. 第二阶段：VM 重启完成、SSH 可达
"""

from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING

from auto_vmware import sshutil
from auto_vmware.log import get_logger

if TYPE_CHECKING:
    from auto_vmware.config import VmSpec

_log = get_logger("orchestrate")

VMRUN = "/usr/bin/vmrun"
T_GUI = "gui"
T_NOGUI = "nogui"


def _vmrun(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """执行 vmrun 命令。

    Args:
        args: vmrun 参数列表（不含 vmrun 本身）。
        capture: 是否捕获输出。

    Returns:
        CompletedProcess。
    """
    cmd = [VMRUN] + args
    _log.debug("vmrun: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )


def start(vmx_path: str, gui: bool = False) -> None:
    """启动 VM。

    Args:
        vmx_path: .vmx 文件路径。
        gui: True 则以 GUI 模式启动，否则 nogui（后台）。
    """
    t = T_GUI if gui else T_NOGUI
    _log.info("启动 VM (%s): %s", t, vmx_path)
    r = _vmrun(["-T", "ws", "start", vmx_path, t])
    if r.returncode != 0:
        _log.error("启动失败:\n%s", r.stderr)
        raise RuntimeError("vmrun start 失败")


def stop(vmx_path: str, hard: bool = False, timeout: int = 120) -> None:
    """关闭 VM。

    Args:
        vmx_path: .vmx 文件路径。
        hard: True 用 poweroff 强制，否则 soft。
        timeout: soft 模式超时后强制关。
    """
    mode = "hard" if hard else "soft"
    _log.info("关闭 VM (%s): %s", mode, vmx_path)
    r = _vmrun(["-T", "ws", "stop", vmx_path, mode])
    if r.returncode != 0:
        _log.warning("soft 关机失败，尝试 hard: %s", r.stderr.strip()[:200])
        _vmrun(["-T", "ws", "stop", vmx_path, "hard"])


def reset(vmx_path: str, hard: bool = False) -> None:
    """重启 VM。"""
    mode = "hard" if hard else "soft"
    _log.info("重启 VM (%s): %s", mode, vmx_path)
    _vmrun(["-T", "ws", "reset", vmx_path, mode])


def is_running(vmx_path: str) -> bool:
    """检查 VM 是否在运行。"""
    r = _vmrun(["-T", "ws", "list"])
    if r.returncode != 0:
        return False
    return vmx_path in r.stdout.splitlines()


def list_vms() -> list[str]:
    """列出正在运行的 VM 的 vmx 路径。"""
    r = _vmrun(["-T", "ws", "list"])
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip().endswith(".vmx")]


def send_key(vmx_path: str, keycode_name: str) -> None:
    """向 VM 发送按键（用于 GRUB 菜单回车确认）。

    Args:
        vmx_path: .vmx 文件路径。
        keycode_name: vmrun 支持的键名，如 ENTER, SPACE, ESC。
    """
    _log.debug("send-key: %s", keycode_name)
    _vmrun(["-T", "ws", "sendKey", vmx_path, keycode_name], capture=False)


def send_keys_grub_enter(vmx_path: str, delay_seconds: int = 25) -> None:
    """在启动后等待 GRUB 菜单出现，然后发送回车选择"Try or Install Ubuntu"。

    Ubuntu 22.04.5 desktop ISO 默认 GRUB 项是 "Try or Install Ubuntu"，
    不按任何键也会自动进入。但 autoinstall 需要在某些场景确认。这里发送
    回车以加速进入安装项。
    """
    _log.info("等待 %ds 后发送 ENTER 进入安装菜单...", delay_seconds)
    time.sleep(delay_seconds)
    send_key(vmx_path, "ENTER")


def wait_for_install_and_reboot(
    spec: VmSpec,
    vmx_path: str,
    total_timeout: int = 2400,
    ssh_window: int = 600,
    interval: int = 10,
    gui: bool = False,
) -> None:
    """等待 autoinstall 完成、VM 重启、SSH 可达。

    autoinstall 完成后会自动重启。重启后系统首启，cloud-init 配置用户/网络
    生效，SSH 变可达。本函数等待这一完整链路。

    Args:
        spec: 虚拟机规格。
        vmx_path: .vmx 路径。
        total_timeout: 安装阶段总超时（秒）。
        ssh_window: 安装完成后等待 SSH 的窗口（秒）。
        interval: 轮询间隔。
        gui: 是否 GUI 模式。

    Raises:
        RuntimeError: 安装阶段超时未完成。
        sshutil.SSHError: SSH 等待超时。
    """
    start_deadline = time.time() + total_timeout
    _log.info("等待 autoinstall 完成（最长 %ds）...", total_timeout)

    # 安装过程中 VM 会运行；安装完成后会自动重启。我们观察 VM 状态变化，
    # 并辅以 SSH 探测判断首启完成。
    seen_running = False
    while time.time() < start_deadline:
        running = is_running(vmx_path)
        if running:
            seen_running = True
            _log.debug("VM 运行中... 安装进行")
        else:
            if seen_running:
                _log.info("检测到 VM 已关机（可能安装完成），重新启动以进入首启")
                # 安装后 VM 可能自动重启，也可能停在关机状态（视 subiquity 版本）
                # 这里主动启动，确保进入系统
                time.sleep(3)
                start(vmx_path, gui=gui)
                break
            else:
                _log.debug("VM 未运行，等待 autoinstall 启动")
        time.sleep(interval)

    # 探测 SSH
    _log.info("等待 SSH 可达: %s", spec.ip_address)
    sshutil.wait_for_ssh(
        host=spec.ip_address,
        username=spec.username,
        password=spec.password,
        timeout_total=ssh_window,
        interval=interval,
    )
    _log.info("SSH 已可达，首启完成")


def reboot_guest_and_wait(
    spec: VmSpec, vmx_path: str, wait_ssh: bool = True, ssh_window: int = 300
) -> None:
    """通过 VMware Tools 触发客户机重启，并等待 SSH 恢复。

    用于装机后配置需要重启的场景（如切换 lightdm）。

    Args:
        spec: 虚拟机规格。
        vmx_path: .vmx 路径。
        wait_ssh: 是否等待 SSH 恢复。
        ssh_window: 等待 SSH 的窗口。
    """
    _log.info("触发客户机重启: %s", spec.name)
    # 先尝试用 vmrun reset（依赖 VMware Tools）
    r = _vmrun(["-T", "ws", "reset", vmx_path, "soft"])
    if r.returncode != 0:
        _log.warning("vmrun reset 失败，尝试 SSH 内 reboot")
        try:
            sshutil.run(spec.ip_address, spec.username, spec.password, "sudo reboot", timeout=30)
        except Exception as e:  # noqa: BLE001
            _log.debug("SSH reboot 抛出（连接断开属正常）: %s", e)

    if wait_ssh:
        # 等 VM 停止再等恢复
        time.sleep(15)
        sshutil.wait_for_ssh(
            host=spec.ip_address,
            username=spec.username,
            password=spec.password,
            timeout_total=ssh_window,
        )
        _log.info("重启后 SSH 恢复")


def validate_host_env(spec: VmSpec) -> None:
    """校验宿主机环境：必备工具与文件是否就绪。

    Args:
        spec: 虚拟机规格。

    Raises:
        RuntimeError: 任一检查失败。
    """
    import os

    checks = {
        "vmrun": VMRUN,
        "vmware-vdiskmanager": "/usr/bin/vmware-vdiskmanager",
        "mkisofs": "/usr/lib/vmware/bin/mkisofs",
        "Ubuntu ISO": spec.iso_path,
        "FlClash deb": spec.flclash_deb,
        "Chrome deb": spec.chrome_deb,
        "Clash config": spec.clash_config,
    }
    missing = []
    for name, path in checks.items():
        if not os.path.exists(path):
            missing.append(f"{name} ({path})")
    if missing:
        raise RuntimeError("环境校验失败，缺失:\n  - " + "\n  - ".join(missing))
    _log.info("宿主机环境校验通过")


def confirm_or_abort(spec: VmSpec) -> None:
    """向用户展示部署摘要并请求确认。--yes 时跳过。"""
    if spec.yes:
        return
    summary = (
        f"\n即将部署虚拟机：\n"
        f"  名称      : {spec.name}\n"
        f"  用户名    : {spec.username}\n"
        f"  时区      : {spec.timezone}\n"
        f"  IP        : {spec.ip_address} (gw {spec.gateway}, mask {spec.netmask})\n"
        f"  DNS       : {', '.join(spec.dns_servers)}\n"
        f"  目录      : {spec.vm_dir}\n"
        f"  磁盘/内存/CPU: {spec.disk_gb}GB / {spec.mem_mb}MB / {spec.cpu} 核\n"
        f"  ISO       : {spec.iso_path}\n"
    )
    print(summary)
    try:
        ans = input("确认开始部署？ [y/N] ").strip().lower()
    except EOFError:
        ans = ""
    if ans not in ("y", "yes"):
        raise SystemExit("已取消")
