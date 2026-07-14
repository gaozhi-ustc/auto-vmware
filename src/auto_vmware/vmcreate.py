"""创建虚拟机：生成 vmdk 磁盘与 .vmx 配置文件。

.vmx 采用 ASCII 编码（VMware 兼容 UTF-16 但 ASCII 同样可读），NAT 网络，
双 CD-ROM（CD0 挂载 Ubuntu ISO，CD1 挂载 cidata 种子 ISO），BIOS 引导。
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from auto_vmware.config import FIXED_CORES_PER_SOCKET, FIXED_DISK_TYPE, FIXED_SOCKETS
from auto_vmware.log import get_logger

if TYPE_CHECKING:
    from auto_vmware.config import VmSpec

_log = get_logger("vmcreate")

VDISKMANAGER = "/usr/bin/vmware-vdiskmanager"


def create_vmdk(spec: VmSpec) -> str:
    """调用 vmware-vdiskmanager 创建空白 vmdk 磁盘。

    Args:
        spec: 虚拟机规格。

    Returns:
        vmdk 文件路径。
    """
    os.makedirs(spec.vm_dir, exist_ok=True)
    out = spec.vmdk_path
    if os.path.exists(out):
        _log.warning("目标 vmdk 已存在，将覆盖: %s", out)
        os.remove(out)

    size_mb = spec.disk_gb * 1024
    cmd = [
        VDISKMANAGER,
        "-c",
        "-s",
        f"{size_mb}MB",
        "-a",
        "lsilogic",
        "-t",
        str(FIXED_DISK_TYPE),  # 0 = 单一可增长虚拟磁盘（monolithic）
        out,
    ]
    _log.info("创建磁盘: %s (%sGB)", out, spec.disk_gb)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        _log.error("vdiskmanager 失败:\n%s", r.stderr)
        raise RuntimeError("创建 vmdk 失败")
    return out


def render_vmx(spec: VmSpec) -> str:
    """渲染 .vmx 配置文件文本。

    结构对齐本机已验证可用的 ubuntu-zero03.vmx，确保 VMware Workstation 能
    正确解析。关键点：
    - 首行必须是 shebang ``#!/usr/bin/vmware``（VMware 把 vmx 当脚本读）。
    - 硬件版本键名是 ``virtualHW.version``（点分），不是驼峰 ``virtualHWVersion``。
    - 需要 pciBridge0/4-7 基础设施项。
    - 主磁盘走 SCSI(lsilogic)；两个光驱走 SATA 控制器（sata0:1 ISO、sata0:2 cidata）。
    - 网络为 NAT，虚拟设备 e1000。
    - uuid / pciSlotNumber 等由 VMware 首次启动时自动生成，无需手写。

    Args:
        spec: 虚拟机规格。

    Returns:
        .vmx 文件全文（纯 ASCII）。
    """
    s = spec
    vmdk_name = os.path.basename(s.vmdk_path)
    cidata_name = os.path.basename(s.cidata_iso_path)
    iso_path = s.iso_path
    # NOTE: 所有注释保持 ASCII；首行 shebang 不可省略。
    return f"""#!/usr/bin/vmware
.encoding = "UTF-8"
config.version = "8"
virtualHW.version = "21"
mks.enable3d = "TRUE"
pciBridge0.present = "TRUE"
pciBridge4.present = "TRUE"
pciBridge4.virtualDev = "pcieRootPort"
pciBridge4.functions = "8"
pciBridge5.present = "TRUE"
pciBridge5.virtualDev = "pcieRootPort"
pciBridge5.functions = "8"
pciBridge6.present = "TRUE"
pciBridge6.virtualDev = "pcieRootPort"
pciBridge6.functions = "8"
pciBridge7.present = "TRUE"
pciBridge7.virtualDev = "pcieRootPort"
pciBridge7.functions = "8"
vmci0.present = "TRUE"
hpet0.present = "TRUE"
nvram = "{s.name}.nvram"
virtualHW.productCompatibility = "hosted"
powerType.powerOff = "soft"
powerType.powerOn = "soft"
powerType.suspend = "soft"
powerType.reset = "soft"
displayName = "{s.name}"
guestOS = "ubuntu-64"
tools.syncTime = "TRUE"
# --- CPU / memory (fixed: 4 sockets x 2 cores = 8 vCPU) ---
numvcpus = "{FIXED_SOCKETS}"
cpuid.coresPerSocket = "{FIXED_CORES_PER_SOCKET}"
memsize = "{s.mem_mb}"
# --- primary disk (SCSI lsilogic, monolithic) ---
scsi0.virtualDev = "lsilogic"
scsi0.present = "TRUE"
scsi0:0.fileName = "{vmdk_name}"
scsi0:0.present = "TRUE"
# --- SATA controller + two CD-ROMs (ISO + cidata seed) ---
sata0.present = "TRUE"
sata0:1.deviceType = "cdrom-image"
sata0:1.fileName = "{iso_path}"
sata0:1.present = "TRUE"
sata0:2.deviceType = "cdrom-image"
sata0:2.fileName = "{cidata_name}"
sata0:2.present = "TRUE"
# --- network (NAT / VMnet8) ---
ethernet0.connectionType = "nat"
ethernet0.addressType = "generated"
ethernet0.virtualDev = "e1000"
ethernet0.present = "TRUE"
# --- USB ---
usb.present = "TRUE"
# --- sound off ---
sound.present = "FALSE"
floppy0.present = "FALSE"
# --- extended ---
extendedConfigFile = "{s.name}.vmxf"
vmxstats.filename = "{s.name}.scoreboard"
# auto-answer dialog questions during headless start
msg.autoAnswer = "TRUE"
"""


def write_vmx(spec: VmSpec, vmx_text: str) -> str:
    """写入 .vmx 文件。

    Args:
        spec: 虚拟机规格。
        vmx_text: .vmx 全文。

    Returns:
        .vmx 文件路径。
    """
    out = spec.vmx_path
    os.makedirs(spec.vm_dir, exist_ok=True)
    if os.path.exists(out):
        _log.warning("目标 .vmx 已存在，将覆盖: %s", out)
    with open(out, "w", encoding="utf-8") as f:
        f.write(vmx_text)
    _log.info("写入 .vmx: %s", out)
    return out


def create_vm(spec: VmSpec, cidata_iso: str) -> str:
    """完整创建一台虚拟机（磁盘 + 配置）。

    Args:
        spec: 虚拟机规格。
        cidata_iso: 已生成好的 cidata 种子 ISO 路径。

    Returns:
        .vmx 文件路径。
    """
    if not os.path.exists(cidata_iso):
        raise FileNotFoundError(f"cidata ISO 不存在: {cidata_iso}")
    if not os.path.exists(spec.iso_path):
        raise FileNotFoundError(f"Ubuntu ISO 不存在: {spec.iso_path}")

    os.makedirs(spec.vm_dir, exist_ok=True)
    create_vmdk(spec)
    # cidata ISO 需在 .vmx 引用前就位于 vm_dir
    if os.path.abspath(cidata_iso) != os.path.abspath(spec.cidata_iso_path):
        import shutil

        _log.info("拷贝 cidata ISO 到 VM 目录")
        shutil.copy2(cidata_iso, spec.cidata_iso_path)

    vmx_text = render_vmx(spec)
    return write_vmx(spec, vmx_text)
