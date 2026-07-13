"""运行参数模型、默认常量与校验。

所有可被环境变量覆盖的默认路径集中在此。IP 尾段校验遵循 AGENTS.md §5。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

# ---- 默认路径常量（可被环境变量覆盖）---------------------------------------
DEFAULT_ISO_PATH = os.environ.get(
    "AUTO_VMWARE_ISO_PATH",
    "/DATA/downloads/ubuntu-22.04.5-desktop-amd64.iso",
)
DEFAULT_VM_BASE_DIR = os.environ.get("AUTO_VMWARE_VM_BASE_DIR", "/DATA/vmware")
DEFAULT_FLCLASH_DEB = os.environ.get(
    "AUTO_VMWARE_FLCLASH_DEB",
    "/DATA/downloads/FlClash-0.8.93-linux-amd64.deb",
)
DEFAULT_CHROME_DEB = os.environ.get(
    "AUTO_VMWARE_CHROME_DEB",
    "/DATA/downloads/google-chrome-stable_current_amd64.deb",
)
DEFAULT_CLASH_CONFIG = os.environ.get(
    "AUTO_VMWARE_CLASH_CONFIG", "/DATA/downloads/gaozhi_new.yaml"
)

# ---- NAT 网络常量（AGENTS.md §5）-------------------------------------------
NAT_GATEWAY = "192.168.167.2"
NAT_NETMASK = "255.255.255.0"
NAT_DNS = ["223.5.5.5", "223.6.6.6"]
NAT_PREFIX = "192.168.167"
# VMware NAT DHCP 分配范围 .128–.254；安全静态段为 3–127
IP_LAST_MIN_SAFE = 3
IP_LAST_MAX_SAFE = 127
# 绝对禁止值
IP_LAST_FORBIDDEN = {0, 1, 2, 255}

# ---- VM 硬件固定参数（AGENTS.md §5；脚本内固定，不接受运行时覆盖）----------
# CPU：4 sockets × 2 cores/socket = 8 vCPU
FIXED_SOCKETS = 4
FIXED_CORES_PER_SOCKET = 2
FIXED_TOTAL_VCPU = FIXED_SOCKETS * FIXED_CORES_PER_SOCKET  # 8
DEFAULT_CPU = FIXED_TOTAL_VCPU
DEFAULT_MEM_MB = 8192
DEFAULT_DISK_GB = 100
# 磁盘：单一可增长虚拟磁盘（monolithic），对应 vdiskmanager -t 0
FIXED_DISK_TYPE = 0

# ---- 装机后 apt 包列表（AGENTS.md 步骤3）-----------------------------------
POSTINSTALL_APT_PKGS: List[str] = [
    "gnome-session",
    "gnome-terminal",
    "ubuntu-desktop",
    "dbus-x11",
    "tigervnc-standalone-server",
    "vim",
    "openssh-server",
    "lightdm",
]


class ConfigError(ValueError):
    """参数校验错误。"""


def validate_ip_last(ip_last: int) -> int:
    """校验 IP 尾段是否合法。

    规则（AGENTS.md §5）：
    - 不得为 0/1/2/255（网络地址、宿主机、网关、广播）。
    - 推荐范围 3–127（避开 DHCP 128–254）。
    - 128–254 允许但给出告警（可能与 DHCP 冲突）。

    Args:
        ip_last: 0–255 之间的整数。

    Returns:
        校验通过的 ip_last。

    Raises:
        ConfigError: 当值为禁止值或越界。
    """
    if not isinstance(ip_last, int) or ip_last < 0 or ip_last > 255:
        raise ConfigError(f"IP 尾段必须是 0–255 的整数，收到: {ip_last!r}")
    if ip_last in IP_LAST_FORBIDDEN:
        raise ConfigError(
            f"IP 尾段 {ip_last} 不可用（0=网络/1=宿主机/2=网关/255=广播）"
        )
    if ip_last > IP_LAST_MAX_SAFE:
        # 允许，但运行时由调用方告警
        pass
    return ip_last


@dataclass
class VmSpec:
    """一台虚拟机的完整部署规格。"""

    name: str
    username: str
    password: str
    timezone: str
    ip_last: int

    # 可选覆盖
    iso_path: str = DEFAULT_ISO_PATH
    vm_base_dir: str = DEFAULT_VM_BASE_DIR
    flclash_deb: str = DEFAULT_FLCLASH_DEB
    chrome_deb: str = DEFAULT_CHROME_DEB
    clash_config: str = DEFAULT_CLASH_CONFIG

    cpu: int = DEFAULT_CPU
    mem_mb: int = DEFAULT_MEM_MB
    disk_gb: int = DEFAULT_DISK_GB

    # 运行控制
    yes: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        validate_ip_last(self.ip_last)
        if not self.name:
            raise ConfigError("虚拟机名称不能为空")
        if not self.username:
            raise ConfigError("用户名不能为空")
        if not self.password:
            raise ConfigError("密码不能为空")
        if not self.timezone:
            raise ConfigError("时区不能为空")

    # ---- 派生属性 -----------------------------------------------------------
    @property
    def ip_address(self) -> str:
        """完整 IPv4 地址，例如 192.168.167.10。"""
        return f"{NAT_PREFIX}.{self.ip_last}"

    @property
    def gateway(self) -> str:
        return NAT_GATEWAY

    @property
    def netmask(self) -> str:
        return NAT_NETMASK

    @property
    def dns_servers(self) -> List[str]:
        return list(NAT_DNS)

    @property
    def vm_dir(self) -> str:
        """该虚拟机的工作目录。"""
        return os.path.join(self.vm_base_dir, self.name)

    @property
    def vmdk_path(self) -> str:
        return os.path.join(self.vm_dir, f"{self.name}.vmdk")

    @property
    def vmx_path(self) -> str:
        return os.path.join(self.vm_dir, f"{self.name}.vmx")

    @property
    def cidata_iso_path(self) -> str:
        return os.path.join(self.vm_dir, f"{self.name}-cidata.iso")

    @property
    def hostname(self) -> str:
        """VM 内的 hostname，默认等于 name。"""
        return self.name
