"""运行参数模型、默认常量与校验。

所有可被环境变量覆盖的默认路径集中在此。IP 尾段校验遵循 AGENTS.md §5。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ---- 默认路径常量（可被环境变量覆盖）---------------------------------------
DEFAULT_ISO_PATH = os.environ.get(
    "AUTO_VMWARE_ISO_PATH",
    "/DATA/downloads/ubuntu-22.04.5-live-server-amd64.iso",
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
DEFAULT_CLASH_CONFIG = os.environ.get("AUTO_VMWARE_CLASH_CONFIG", "/DATA/downloads/gaozhi_new.yaml")

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
POSTINSTALL_APT_PKGS: list[str] = [
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
        raise ConfigError(f"IP 尾段 {ip_last} 不可用（0=网络/1=宿主机/2=网关/255=广播）")
    if ip_last > IP_LAST_MAX_SAFE:
        # 允许，但运行时由调用方告警
        pass
    return ip_last


# IANA 未收录但常见的城市/旧名 → 标准 IANA 时区。subiquity 只认 IANA 名，
# 像 Beijing/Hangzhou 这类中国城市 IANA 并未收录（中国时区统一用 Asia/Shanghai），
# 旧殖民名 Bombay/Madras 也不在库中。此处兜底，避免静默回退 UTC。
TIMEZONE_ALIASES: dict[str, str] = {
    # 中国城市（IANA 仅收录 Asia/Shanghai、Asia/Urumqi）
    "Beijing": "Asia/Shanghai",
    "Guangzhou": "Asia/Shanghai",
    "Shenzhen": "Asia/Shanghai",
    "Chengdu": "Asia/Shanghai",
    "Hangzhou": "Asia/Shanghai",
    "Nanjing": "Asia/Shanghai",
    "Wuhan": "Asia/Shanghai",
    "Xian": "Asia/Shanghai",
    "Tianjin": "Asia/Shanghai",
    # 印度旧殖民名 → 现名
    "Bombay": "Asia/Kolkata",
    "Madras": "Asia/Kolkata",
    # 越南胡志明市旧名（IANA 已改用 Asia/Ho_Chi_Minh，Asia/Saigon 仅为兼容别名）
    "Saigon": "Asia/Ho_Chi_Minh",
    # 常见英文别名
    "NYC": "America/New_York",
    "LA": "America/Los_Angeles",
    "SF": "America/Los_Angeles",
    "UK": "Europe/London",
}


def normalize_timezone(tz: str) -> str:
    """把时区输入规范化为 IANA 时区名。

    subiquity 只认 IANA 时区（如 Africa/Lagos、Asia/Shanghai、Europe/London）。
    用户可能传裸城市名（Lagos、Beijing）、含空格的城市（New York）、
    别名（Bombay）或已合规的 IANA 名。本函数按以下顺序解析：

    1. 空格归一化为下划线（``New York`` → ``New_York``，对齐 IANA 命名）。
    2. 直接全名匹配（已是合法 IANA 名则原样返回）。
    3. 别名表兜底（``Beijing`` → ``Asia/Shanghai``）。
    4. 城市名后缀匹配（``Lagos`` → ``Africa/Lagos``）。

    解析失败（无匹配，或多匹配无法唯一确定）时**抛出 ConfigError**，
    而非静默回退 UTC —— 静默回退会让 subiquity 把 VM 装成错误时区且无报错，
    违反 AGENTS.md §6「每一步必须有明确失败提示」。

    Args:
        tz: 用户输入的时区（城市名、别名或 IANA 名）。

    Returns:
        IANA 时区名（如 Africa/Lagos、Asia/Shanghai）。

    Raises:
        ConfigError: 无法唯一映射到 IANA 时区时（无匹配或多匹配）。
    """
    from zoneinfo import available_timezones

    if not tz or not tz.strip():
        raise ConfigError("时区不能为空")

    # 1. 空格 → 下划线（IANA 区名用下划线，如 America/New_York）
    normalized = tz.strip().replace(" ", "_")

    avail = available_timezones()

    # 2. 直接全名匹配（已是合法 IANA 名）
    if normalized in avail:
        return normalized

    # 3. 别名表兜底（IANA 未收录的城市/旧名）
    if normalized in TIMEZONE_ALIASES:
        resolved = TIMEZONE_ALIASES[normalized]
        return resolved

    # 4. 城市名后缀匹配：在所有 IANA 名里找以 /城市 结尾的
    suffix = "/" + normalized
    matches = sorted(t for t in avail if t.endswith(suffix))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # 多匹配无法唯一确定 —— 让用户明确指定，而非猜一个
        # 例：Buenos_Aires → America/Argentina/Buenos_Aires 与 America/Buenos_Aires
        raise ConfigError(f"时区 {tz!r} 匹配到多个 IANA 时区，请明确指定其一: {', '.join(matches)}")

    # 5. 大小写不敏感重试（用户可能小写传 Lagos）
    matches_ci = sorted(t for t in avail if t.lower().endswith(suffix.lower()))
    if len(matches_ci) == 1:
        return matches_ci[0]
    if len(matches_ci) > 1:
        raise ConfigError(
            f"时区 {tz!r} 匹配到多个 IANA 时区，请明确指定其一: {', '.join(matches_ci)}"
        )

    # 6. 无匹配 —— 抛错而非静默回退 UTC
    hint = "（例如 Asia/Shanghai、America/New_York、Africa/Lagos）"
    raise ConfigError(f"无法将时区 {tz!r} 映射到 IANA 时区，请使用标准 IANA 名 {hint}")


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
        # 规范化时区为 IANA 格式（如 Lagos → Africa/Lagos）。
        # subiquity 只认 IANA 时区名，裸城市名会报 "Unrecognized time zone"。
        self.timezone = normalize_timezone(self.timezone)

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
    def dns_servers(self) -> list[str]:
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
