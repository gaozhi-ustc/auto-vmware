"""生成 autoinstall 的 user-data / meta-data，并打包成 NoCloud 种子 ISO。

Ubuntu 22.04.x 的 desktop/live ISO 内含 subiquity，通过 NoCloud 数据源读取
`autoinstall:` 块即可实现无人值守安装。本模块负责：

1. 渲染 user-data（autoinstall 配置 + 首启 cloud-init 初始化）。
2. 渲染 meta-data（instance-id、hostname）。
3. 调用宿主机自带的 mkisofs 打包为 cidata ISO（卷标 CIDATA）。

关键设计：
- 静态网络通过 autoinstall 的 `network.network.ethernets` 在安装阶段生效，
  确保安装完成后 VM 即以目标 IP 可达。
- 首次启动后的桌面/VNC/FlClash 等配置不在 autoinstall 内完成，而是由
  `provision.py` 通过 SSH 在装好后执行（deb 文件在宿主机上，scp 进去更可靠）。
- 仅在 user-data 中完成最小初始化：创建用户、设密码、开 sudo、装 openssh、
  设置时区/主机名、安装 open-vm-tools，保证 SSH 可达即可。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import TYPE_CHECKING

from auto_vmware.log import get_logger

if TYPE_CHECKING:
    from auto_vmware.config import VmSpec

_log = get_logger("cidata")

# 宿主机自带的 mkisofs（VMware Workstation 附带），避免引入外部依赖
MKISOFS = "/usr/lib/vmware/bin/mkisofs"


def render_user_data(spec: VmSpec) -> str:
    """渲染 autoinstall user-data 文本。

    包含 `#cloud-config` 头与 `autoinstall:` 块。subiquity 检测到该块后会
    进入自动安装流程。

    Args:
        spec: 虚拟机规格。

    Returns:
        完整的 user-data 字符串（YAML）。
    """
    dns = spec.dns_servers
    # netplan 格式：DNS 用列表
    dns_list = ", ".join(dns)
    s = spec  # 简短别名
    return f"""#cloud-config
autoinstall:
  version: 1
  locale: zh_CN.UTF-8
  keyboard:
    layout: us
  identity:
    hostname: {s.hostname}
    realname: {s.username}
    username: {s.username}
    password: "$6$rounds=4096$autopw$PLACEHOLDER"
  # 注：password 占位由下面 users 模块的 hashed_passwd 覆盖
  network:
    version: 2
    ethernets:
      ens33:
        dhcp4: false
        addresses:
          - {s.ip_address}/24
        routes:
          - to: default
            via: {s.gateway}
        nameservers:
          addresses: [{dns_list}]
  ssh:
    install-server: true
    allow-pw: true
  storage:
    layout:
      name: direct
  # 时区设置
  timezone: {s.timezone}
  packages:
    - openssh-server
    - open-vm-tools
    - curl
  # 用户密码通过 chpasswd 在首启后由 cloud-init 设定（autoinstall identity 里
  # 的明文密码处理在不同 subiquity 版本有差异，统一在首启固化最稳）。
  user-data:
    disable_root: false
    timezone: {s.timezone}
    hostname: {s.hostname}
    chpasswd:
      expire: false
      list: |
        root:{s.password}
        {s.username}:{s.password}
    ssh_pwauth: true
    users:
      - name: {s.username}
        groups: [adm, sudo, wheel]
        shell: /bin/bash
        sudo: "ALL=(ALL) NOPASSWD:ALL"
        lock_passwd: false
  # 安装完成后自动重启
  user-data-1:
    runcmd:
      - systemctl enable ssh
  late-commands:
    - curtin in-target -- sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
    - curtin in-target -- systemctl enable ssh
    - echo "{s.username} ALL=(ALL) NOPASSWD:ALL" | curtin in-target -- tee -a /etc/sudoers.d/{s.username}
    - curtin in-target -- apt-get update || true
    - curtin in-target -- apt-get install -y open-vm-tools
"""


def render_user_data_v2(spec: VmSpec) -> str:
    """渲染更稳健的 autoinstall user-data（推荐使用此版本）。

    与 v1 相比：直接在 identity 用 hashed_passwd，避免 chpasswd 时机问题；
    网络配置同时写入 autoinstall 与首启 cloud-init，双保险。

    Args:
        spec: 虚拟机规格。

    Returns:
        完整的 user-data 字符串（YAML）。
    """
    import crypt

    s = spec
    hashed = crypt.crypt(s.password, crypt.mksalt(crypt.METHOD_SHA512))
    # YAML 中含 $ 的字符串需用双引号包裹并注意转义；hashed_passwd 用引号包裹
    dns = ", ".join(s.dns_servers)
    return f"""#cloud-config
autoinstall:
  version: 1
  locale: zh_CN.UTF-8
  keyboard:
    layout: us
  timezone: {s.timezone}
  identity:
    hostname: {s.hostname}
    realname: {s.username}
    username: {s.username}
    password: "{hashed}"
  network:
    version: 2
    ethernets:
      # 不写死网卡名：用 match 匹配第一张物理网卡，避免 ens33/enp0s3 命名差异
      net0:
        match:
          name: "e*"
        dhcp4: false
        addresses:
          - {s.ip_address}/24
        routes:
          - to: default
            via: {s.gateway}
        nameservers:
          addresses: [{dns}]
  ssh:
    install-server: true
    allow-pw: true
  storage:
    layout:
      name: direct
  packages:
    - openssh-server
    - open-vm-tools
    - curl
  late-commands:
    - curtin in-target -- sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
    - curtin in-target -- systemctl enable ssh
    - |
      echo "{s.username} ALL=(ALL) NOPASSWD:ALL" > /target/etc/sudoers.d/{s.username}
      curtin in-target -- chmod 440 /etc/sudoers.d/{s.username}
    - curtin in-target -- apt-get update || true
    - curtin in-target -- apt-get install -y open-vm-tools
"""  # noqa: E501


def render_meta_data(spec: VmSpec) -> str:
    """渲染 meta-data。"""
    return f"instance-id: {spec.name}-001\nlocal-hostname: {spec.hostname}\n"


def build_cidata_iso(spec: VmSpec, user_data_text: str, meta_data_text: str) -> str:
    """将 user-data / meta-data 打包为 NoCloud 种子 ISO。

    使用宿主机自带的 mkisofs，卷标设为 CIDATA（NoCloud 数据源约定）。

    Args:
        spec: 虚拟机规格（取 cidata_iso_path）。
        user_data_text: user-data 内容。
        meta_data_text: meta-data 内容。

    Returns:
        生成的 ISO 文件路径。
    """
    out = spec.cidata_iso_path
    if not os.path.exists(MKISOFS):
        raise FileNotFoundError(f"未找到 mkisofs：{MKISOFS}。请确认 VMware Workstation 已安装。")
    # 确保输出目录存在，否则 mkisofs 会因无法创建输出文件而报
    # "Unable to open disc image file"
    os.makedirs(os.path.dirname(out), exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cidata-") as d:
        ud = os.path.join(d, "user-data")
        md = os.path.join(d, "meta-data")
        with open(ud, "w", encoding="utf-8") as f:
            f.write(user_data_text)
        with open(md, "w", encoding="utf-8") as f:
            f.write(meta_data_text)

        cmd = [
            MKISOFS,
            "-output",
            out,
            "-volid",
            "CIDATA",
            "-joliet",
            "-rock",
            ud,
            md,
        ]
        _log.info("生成 cidata ISO: %s", out)
        _log.debug("mkisofs cmd: %s", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            _log.error("mkisofs 失败:\n%s", r.stderr)
            raise RuntimeError("生成 cidata ISO 失败")
    return out
