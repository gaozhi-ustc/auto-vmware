"""创建虚拟机：生成 vmdk 磁盘与 .vmx 配置文件。

.vmx 采用 ASCII 编码（VMware 兼容 UTF-16 但 ASCII 同样可读），NAT 网络，
双 CD-ROM（CD0 挂载 Ubuntu ISO，CD1 挂载 cidata 种子 ISO），BIOS 引导。
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from auto_vmware.log import get_logger

if TYPE_CHECKING:
    from auto_vmware.config import VmSpec

_log = get_logger("vmcreate")

VDISKMANAGER = "/usr/bin/vmware-vdiskmanager"


def create_vmdk(spec: "VmSpec") -> str:
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
        "-s", f"{size_mb}MB",
        "-a", "lsilogic",
        "-t", "0",  # 单一可增长虚拟磁盘
        out,
    ]
    _log.info("创建磁盘: %s (%sGB)", out, spec.disk_gb)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        _log.error("vdiskmanager 失败:\n%s", r.stderr)
        raise RuntimeError("创建 vmdk 失败")
    return out


def render_vmx(spec: "VmSpec") -> str:
    """渲染 .vmx 配置文件文本。

    配置要点：
    - guestOS = ubuntu-64
    - ethernet0.connectionType = nat（VMnet8）
    - 双 CD-ROM：CD0 挂 ISO，CD1 挂 cidata 种子
    - SCSI/LSI 控制器 + 主磁盘
    - 内存/CPU 按 spec

    Args:
        spec: 虚拟机规格。

    Returns:
        .vmx 文件全文（ASCII）。
    """
    s = spec
    # 注意：.vmx 的值用双引号；路径不转义反斜杠
    return f""".encoding = "UTF-8"
config.version = "8"
virtualHWVersion = "19"
guestOS = "ubuntu-64"
displayName = "{s.name}"
tools.syncTime = "TRUE"

# --- 电源/兼容 ---
powerType.powerOff = "soft"
powerType.powerOn = "soft"
powerType.suspend = "soft"
powerType.reset = "soft"
virtualHW.productCompatibility = "hosted"

# --- CPU/内存 ---
numvcpus = "{s.cpu}"
cpuid.coresPerSocket = "{s.cpu}"
memsize = "{s.mem_mb}"

# --- 主磁盘（SCSI）---
scsi0.present = "TRUE"
scsi0.virtualDev = "lsilogic"
scsi0:0.present = "TRUE"
scsi0:0.deviceType = "scsi-hardDisk"
scsi0:0.fileName = "{os.path.basename(s.vmdk_path)}"
scsi0:0.mode = "persistent"

# --- CD-ROM0: Ubuntu ISO ---
ide1:0.present = "TRUE"
ide1:0.deviceType = "cdrom-image"
ide1:0.fileName = "{s.iso_path}"
ide1:0.startConnected = "TRUE"

# --- CD-ROM1: cidata 种子 ISO（NoCloud）---
ide1:1.present = "TRUE"
ide1:1.deviceType = "cdrom-image"
ide1:1.fileName = "{os.path.basename(s.cidata_iso_path)}"
ide1:1.startConnected = "TRUE"

# --- 网络（NAT / VMnet8）---
ethernet0.present = "TRUE"
ethernet0.connectionType = "nat"
ethernet0.virtualDev = "e1000"
ethernet0.addressType = "generated"
ethernet0.generatedAddressOffset = "0"

# --- USB（便于桌面环境）---
usb.present = "TRUE"
ehci.present = "TRUE"

# --- 声卡 ---
sound.present = "FALSE"

# --- 引导 ---
bios.bootRetry.delay = "10000"
bios.hardDiskBootPriority = "1"

# --- 固件：BIOS（避免 UEFI 与 autoinstall storage 配置的兼容性问题）---
firmware = "bios"

# --- VMware Tools ---
toolScripts.afterPowerOn = "TRUE"

# --- 扩展 ---
msg.autoAnswer = "TRUE"
answer.msg.commandLineTooLong = "TRUE"
extendedConfigFile = "{s.name}.vmxf"
"""
# 末尾不要空行错位


def write_vmx(spec: "VmSpec", vmx_text: str) -> str:
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


def create_vm(spec: "VmSpec", cidata_iso: str) -> str:
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
