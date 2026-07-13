"""构建 autoinst.iso：基于原 Ubuntu desktop ISO，注入 autoinstall 引导参数与 cidata。

为什么需要这个模块：
    原始 Ubuntu 22.04.5 desktop ISO 的 GRUB 启动项内核命令行里没有
    ``autoinstall`` 参数，subiquity 因此在无头模式下停在确认对话框。
    本模块通过重新打包 ISO，给 GRUB 启动项加上
    ``autoinstall ds=nocloud`` 参数，并将 user-data/meta-data 作为 cidata
    内嵌进 ISO，使 subiquity 真正进入无人值守安装。

实现方式（两段式，按可用工具择优）：
    A. 首选 pycdlib 的"以原 ISO 为基础，替换 grub.cfg + 增加 cidata 文件，
       重新写出"。这只需改动少量文件，性能最好。
    B. 若 A 不兼容（部分 hybrid/UDF ISO 不支持），退回到"解压全部内容到
       临时目录，改文件后用 mkisofs 重新打包"。

引导约定（NoCloud over ISO）：
    - ISO 卷标必须为 CIDATA（NoCloud 数据源通过卷标识别）。
    - 根目录放置 user-data 与 meta-data。
    - 同时修改 GRUB 加 autoinstall 参数（双保险）。

注意：本模块构建的 ISO 兼具"系统安装盘"与"NoCloud 种子盘"双重身份，
因此 .vmx 中只需挂载这一个光驱即可，不再需要单独的 cidata ISO。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

from auto_vmware.log import get_logger

if TYPE_CHECKING:
    from auto_vmware.config import VmSpec

_log = get_logger("autobuild")

MKISOFS = "/usr/lib/vmware/bin/mkisofs"

# NoCloud 卷标
CIDATA_LABEL = "CIDATA"


def _patched_grub_cfg(original: str, mode: str = "autoinstall") -> str:
    """在 GRUB 配置里为 linux 启动行注入自动安装参数。

    - mode="autoinstall"（默认，server ISO）：注入 ``autoinstall ds=nocloud``
      让 subiquity 读取本 ISO 根目录的 user-data/meta-data（NoCloud over ISO），
      无需单独 cidata 盘。需配合 build 时卷标设为 CIDATA。
    - mode="preseed"（desktop ISO）：注入 preseed 指向 + automatic-ubiquity。

    同时把 GRUB 超时从 30s 缩到 3s 加速。

    Args:
        original: 原始 grub.cfg 文本。
        mode: "autoinstall" 或 "preseed"。

    Returns:
        修改后的 grub.cfg 文本。
    """
    lines = original.splitlines()
    out = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("linux") and "/casper/" in line and "vmlinuz" in line:
            if mode == "autoinstall":
                inject = " autoinstall ds=nocloud"
            else:  # preseed
                line = line.replace(
                    "file=/cdrom/preseed/ubuntu.seed",
                    "file=/cdrom/preseed/auto.seed",
                )
                line = line.replace("maybe-ubiquity", "automatic-ubiquity")
                inject = (
                    " debian-installer/locale=en_US.UTF-8 "
                    "keyboard-configuration/layoutcode=us "
                    "netcfg/get_hostname=auto netcfg/get_domain=localdomain"
                )
            if " --- " in line:
                line = line.replace(" --- ", inject + " --- ", 1)
            elif line.rstrip().endswith("---"):
                line = line.rstrip()[:-3].rstrip() + inject + " ---"
            else:
                line = line.rstrip() + inject
        out.append(line)
    text = "\n".join(out)
    text = text.replace("set timeout=30", "set timeout=3")
    if not text.endswith("\n"):
        text += "\n"
    return text


def build_autoinst_iso_full_unpack(
    spec: "VmSpec",
    *,
    mode: str = "autoinstall",
    user_data: str = "",
    meta_data: str = "",
    preseed_text: str = "",
) -> str:
    """方式 B：解压原 ISO 全部内容，改 grub.cfg + 注入自动安装数据，重新打包。

    mode="autoinstall"（server ISO，推荐）：写入 user-data/meta-data 到 ISO 根目录，
    卷标设为 CIDATA，subiquity 通过 NoCloud 读取。
    mode="preseed"（desktop ISO）：写入 preseed/auto.seed。

    Args:
        spec: 虚拟机规格。
        mode: "autoinstall" 或 "preseed"。
        user_data: autoinstall 模式的 user-data 文本。
        meta_data: autoinstall 模式的 meta-data 文本。
        preseed_text: preseed 模式的应答文件文本。

    Returns:
        生成的 autoinst.iso 路径。
    """
    _log.info("构建 autoinst.iso（mode=%s）", mode)
    if not os.path.exists(MKISOFS):
        raise FileNotFoundError(f"未找到 mkisofs: {MKISOFS}")

    import pycdlib

    work = tempfile.mkdtemp(prefix="autoinst-unpack-")
    try:
        iso_out = spec.cidata_iso_path.replace("-cidata.iso", "-autoinst.iso")
        # 输出目录必须存在，否则 mkisofs 写不出文件（报 "Unable to open disc image file"）
        os.makedirs(os.path.dirname(iso_out), exist_ok=True)
        src_iso = spec.iso_path

        _log.info("解压原 ISO 到临时目录（这可能需要几分钟）...")
        iso = pycdlib.PyCdlib()
        iso.open(src_iso)

        # 用 list_dir 递归遍历（walk 会漏掉 i386-pc 等目录，list_dir 可靠）。
        # ISO9660 路径大写；本地保存为小写（GRUB 引导路径 /casper/vmlinuz 需小写）。
        ok = 0
        skipped = 0

        def _extract(iso_dir: str) -> None:
            nonlocal ok, skipped
            rel = iso_dir.lstrip("/").lower()
            local_dir = os.path.join(work, rel) if rel else work
            os.makedirs(local_dir, exist_ok=True)
            for child in iso.list_dir(iso_path=iso_dir):
                name = child.file_identifier().decode("utf-8", "replace")
                if name in (".", ".."):
                    continue
                child_iso_path = (iso_dir.rstrip("/") + "/" + name) if iso_dir != "/" else "/" + name
                if child.is_dir():
                    _extract(child_iso_path)
                elif child.is_file():
                    clean = name.split(";")[0].rstrip(".").lower()
                    local_path = os.path.join(local_dir, clean)
                    try:
                        iso.get_file_from_iso(local_path=local_path, iso_path=child_iso_path)
                        ok += 1
                        if ok % 300 == 0:
                            _log.debug("已解压 %d 个文件...", ok)
                    except Exception as e:  # noqa: BLE001
                        # 符号链接等无数据条目，跳过
                        skipped += 1
                        _log.debug("跳过 %s: %s", child_iso_path, str(e)[:60])

        _extract("/")
        iso.close()
        _log.info("解压完成：成功 %d 个，跳过 %d 个", ok, skipped)

        # 校验关键文件（装机必需）。注意原 ISO 的 GRUB 目录名是 i386_pc（下划线），
        # 与 GRUB 社区惯例 i386-pc（连字符）不同；按 ISO 实际命名。
        # 校验关键文件。squashfs 文件名因 ISO 而异：
        # desktop = filesystem.squashfs，server = ubuntu-server-minimal.squashfs
        casper_dir = os.path.join(work, "casper")
        squashfs_files = [
            f for f in os.listdir(casper_dir) if f.endswith(".squashfs")
        ] if os.path.isdir(casper_dir) else []
        must_exist = [
            os.path.join(casper_dir, "vmlinuz"),
            os.path.join(casper_dir, "initrd"),
            os.path.join(work, "boot", "grub", "i386_pc", "eltorito.img"),
        ]
        if not squashfs_files:
            _log.error("casper 下未找到 squashfs 文件")
            raise RuntimeError("解压不完整：缺少 squashfs")
        for p in must_exist:
            if not os.path.isfile(p):
                _log.error("关键文件解压缺失: %s", p)
                d = os.path.dirname(p)
                if os.path.isdir(d):
                    _log.error("目录 %s 实际内容: %s", d, sorted(os.listdir(d))[:20])
                raise RuntimeError(f"解压不完整，缺失: {p}")
        _log.info(
            "关键文件校验通过（squashfs=%s, eltorito=%d bytes, grub模块数=%d）",
            ", ".join(squashfs_files),
            os.path.getsize(must_exist[2]),
            len(os.listdir(os.path.join(work, "boot", "grub", "i386_pc"))),
        )

        # 修改 grub.cfg 与 loopback.cfg（注入自动安装参数）
        grub_path = os.path.join(work, "boot", "grub", "grub.cfg")
        loop_path = os.path.join(work, "boot", "grub", "loopback.cfg")
        for p in [grub_path, loop_path]:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    orig = f.read()
                patched = _patched_grub_cfg(orig, mode=mode)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(patched)
                _log.info("已注入 %s 参数: %s", mode, p)

        # 写入自动安装数据
        if mode == "autoinstall":
            # NoCloud over ISO：根目录放 user-data / meta-data，ISO 卷标为 CIDATA
            with open(os.path.join(work, "user-data"), "w", encoding="utf-8") as f:
                f.write(user_data)
            with open(os.path.join(work, "meta-data"), "w", encoding="utf-8") as f:
                f.write(meta_data)
            _log.info(
                "已写入 cidata: user-data (%d bytes), meta-data",
                len(user_data),
            )
        else:  # preseed
            preseed_dir = os.path.join(work, "preseed")
            os.makedirs(preseed_dir, exist_ok=True)
            with open(os.path.join(preseed_dir, "auto.seed"), "w", encoding="utf-8") as f:
                f.write(preseed_text)
            _log.info("已写入 preseed: preseed/auto.seed (%d bytes)", len(preseed_text))

        # 用 mkisofs 重新打包。需保留可引导（El Torito boot）。
        # 关键：mkisofs 的 -b 引导镜像路径相对于源目录解析；当源目录用绝对路径时
        # 引导路径解析会失败（"Unable to open disc image file"），因此必须把
        # cwd 切到 work，源目录传 "."。
        # 引导镜像路径按 ISO 实际命名 i386_pc（下划线）。
        _log.info("重新打包 ISO（mkisofs）...")
        cmd = [
            MKISOFS,
            "-output", iso_out,
            "-volid", CIDATA_LABEL,
            "-joliet", "-rock",
            "-full-iso9660-filenames",
            "-b", "boot/grub/i386_pc/eltorito.img",
            "-c", "boot.catalog",
            "-no-emul-boot",
            "-boot-load-size", "4",
            "-boot-info-table",
            ".",
        ]
        _log.debug("mkisofs cwd=%s cmd=%s", work, " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
        if r.returncode != 0 or not os.path.isfile(iso_out):
            _log.error("mkisofs 失败:\n%s", r.stderr[-2000:])
            raise RuntimeError("重新打包 ISO 失败")
        _log.info("autoinst.iso 生成: %s (%d bytes)", iso_out, os.path.getsize(iso_out))
        return iso_out
    finally:
        shutil.rmtree(work, ignore_errors=True)
