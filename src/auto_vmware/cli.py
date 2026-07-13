"""CLI 入口：参数解析与一键部署编排。

用法：
    python -m auto_vmware deploy \\
        --name ubuntu-test \\
        --username gaozhi \\
        --password 'secret' \\
        --timezone Asia/Shanghai \\
        --ip-last 50 \\
        [--yes] [--gui]
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from auto_vmware import cidata, orchestrate, provision, vmcreate
from auto_vmware.config import (
    DEFAULT_CHROME_DEB,
    DEFAULT_CLASH_CONFIG,
    DEFAULT_FLCLASH_DEB,
    DEFAULT_ISO_PATH,
    DEFAULT_VM_BASE_DIR,
    VmSpec,
)
from auto_vmware.log import get_logger, setup_logger

_log = get_logger("cli")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", required=True, help="虚拟机名称（同时作为 hostname）")
    p.add_argument("--username", required=True, help="VM 内的用户名")
    p.add_argument(
        "--password",
        required=True,
        help="VM 用户密码（同时作为 VNC 密码、sudo 密码）",
    )
    p.add_argument("--timezone", required=True, help="时区，如 Asia/Shanghai")
    p.add_argument(
        "--ip-last",
        type=int,
        required=True,
        help="IP 尾段（192.168.167.<n>），推荐 3-127",
    )
    p.add_argument("--cpu", type=int, default=4, help="CPU 核数，默认 4")
    p.add_argument("--mem-mb", type=int, default=8192, help="内存 MB，默认 8192")
    p.add_argument("--disk-gb", type=int, default=60, help="磁盘 GB，默认 60")
    p.add_argument("--iso", default=DEFAULT_ISO_PATH, help="Ubuntu ISO 路径")
    p.add_argument("--vm-base", default=DEFAULT_VM_BASE_DIR, help="虚拟机父目录")
    p.add_argument("--flclash-deb", default=DEFAULT_FLCLASH_DEB, help="FlClash deb 路径")
    p.add_argument("--chrome-deb", default=DEFAULT_CHROME_DEB, help="Chrome deb 路径")
    p.add_argument("--clash-config", default=DEFAULT_CLASH_CONFIG, help="Clash 配置 yaml")
    p.add_argument("--yes", action="store_true", help="跳过确认提示")
    p.add_argument("--gui", action="store_true", help="以 GUI 模式启动 VM（默认后台）")
    p.add_argument("--verbose", action="store_true", help="DEBUG 日志")


def build_spec(args: argparse.Namespace) -> VmSpec:
    """从解析后的参数构造 VmSpec。"""
    return VmSpec(
        name=args.name,
        username=args.username,
        password=args.password,
        timezone=args.timezone,
        ip_last=args.ip_last,
        iso_path=args.iso,
        vm_base_dir=args.vm_base,
        flclash_deb=args.flclash_deb,
        chrome_deb=args.chrome_deb,
        clash_config=args.clash_config,
        cpu=args.cpu,
        mem_mb=args.mem_mb,
        disk_gb=args.disk_gb,
        yes=args.yes,
        verbose=args.verbose,
    )


def cmd_deploy(args: argparse.Namespace) -> int:
    """一键部署：创建 → 装机 → 配置（步骤3-5）。"""
    spec = build_spec(args)
    setup_logger(verbose=spec.verbose)

    _log.info("一键部署开始：%s", spec.name)
    t0 = time.time()

    # 0. 环境校验 + 确认
    orchestrate.validate_host_env(spec)
    orchestrate.confirm_or_abort(spec)

    # 1. 生成 cidata 种子
    ud = cidata.render_user_data_v2(spec)
    md = cidata.render_meta_data(spec)
    cidata_iso = cidata.build_cidata_iso(spec, ud, md)

    # 2. 创建 VM（磁盘 + .vmx）
    vmx_path = vmcreate.create_vm(spec, cidata_iso)

    # 3. 启动 + 等待 autoinstall 完成 + 等待 SSH
    orchestrate.start(vmx_path, gui=args.gui)
    orchestrate.wait_for_install_and_reboot(
        spec, vmx_path, total_timeout=2400, ssh_window=600, gui=args.gui
    )

    # 4/5/6. 装机后配置
    provision.provision_all(spec)

    dt = int(time.time() - t0)
    print(
        "\n✅ 部署完成（用时约 %d 分钟）\n"
        "  名称      : %s\n"
        "  SSH       : ssh %s@%s\n"
        "  VNC       : %s:5901（密码同用户密码）\n"
        "  目录      : %s\n",
        dt // 60,
        spec.name,
        spec.username,
        spec.ip_address,
        spec.ip_address,
        spec.vm_dir,
    )
    return 0


def cmd_provision_only(args: argparse.Namespace) -> int:
    """只执行装机后配置（假设 VM 已装好且 SSH 可达）。"""
    spec = build_spec(args)
    setup_logger(verbose=spec.verbose)
    orchestrate.confirm_or_abort(spec)
    provision.provision_all(spec)
    print("\n✅ 装机后配置完成")
    return 0


def cmd_create_only(args: argparse.Namespace) -> int:
    """只创建 VM（不启动装机与配置）。"""
    spec = build_spec(args)
    setup_logger(verbose=spec.verbose)
    orchestrate.confirm_or_abort(spec)
    ud = cidata.render_user_data_v2(spec)
    md = cidata.render_meta_data(spec)
    cidata_iso = cidata.build_cidata_iso(spec, ud, md)
    vmx_path = vmcreate.create_vm(spec, cidata_iso)
    print(f"\n✅ VM 已创建: {vmx_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器。"""
    parser = argparse.ArgumentParser(
        prog="auto-vmware",
        description="一键自动化 Ubuntu 虚拟机部署/配置（VMware Workstation）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_deploy = sub.add_parser("deploy", help="一键部署：创建+装机+配置")
    _add_common(p_deploy)
    p_deploy.set_defaults(func=cmd_deploy)

    p_create = sub.add_parser("create", help="仅创建 VM（不启动装机）")
    _add_common(p_create)
    p_create.set_defaults(func=cmd_create_only)

    p_prov = sub.add_parser("provision", help="仅执行装机后配置（VM 已存在）")
    _add_common(p_prov)
    p_prov.set_defaults(func=cmd_provision_only)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        _log.error("部署失败: %s", e)
        if getattr(args, "verbose", False):
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
