"""生成 ubiquity/preseed 自动应答文件（desktop ISO 无人值守方案）。

Ubuntu 22.04 desktop ISO 用 ubiquity 安装器（非 subiquity），不支持
autoinstall。本模块生成完整的 preseed 应答文件，配合 GRUB 内核参数
``file=/cdrom/preseed/auto.seed locale=en_US.UTF-8 keyboard-configuration/layoutcode=us
automatic-ubiquity -- ubiquity`` 实现无人值守安装。

ubiquity preseed 的关键 question 前缀为 ``d-i`` 或 ``ubiquity``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_vmware.config import VmSpec


def render_preseed(spec: VmSpec) -> str:
    """渲染 ubiquity preseed 应答文件。

    覆盖：语言、键盘、时区、网络（DHCP 由 ubiquity 阶段处理，静态 IP 留给
    装机后 provision 配置）、分区（整盘 LVM）、用户/密码、安装 ubuntu-desktop、
    安装后关机。

    Args:
        spec: 虚拟机规格。

    Returns:
        preseed 文件全文。
    """
    s = spec
    return f"""### === auto-vmware ubiquity preseed ===
### 内容由 auto_vmware.preseed.render_preseed 生成，勿手改。

# ---- 本地化 ----
d-i debian-installer/locale string en_US.UTF-8
d-i console-setup/ask_detect boolean false
d-i console-setup/layoutcode string us
d-i keyboard-configuration/layoutcode string us
d-i keyboard-configuration/variant select USA
d-i locale select en_US.UTF-8

# ---- 网络（ubiquity 阶段用 DHCP 即可，静态 IP 装机后配）----
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string {s.hostname}
d-i netcfg/get_domain string localdomain
d-i netcfg/hostname string {s.hostname}

# ---- 时区 ----
d-i clock-setup/utc boolean true
d-i time/zone string {s.timezone}
d-i clock-setup/ntp boolean false

# ---- 用户与密码 ----
# 跳过真实姓名/用户名的交互提问
d-i passwd/user-fullname string {s.username}
d-i passwd/username string {s.username}
d-i passwd/user-password password {s.password}
d-i passwd/user-password-again password {s.password}
d-i passwd/user-default-groups string adm cdrom sudo dip plugdev lpadmin sambashare
# 不加密 home
d-i user-setup/encrypt-home boolean false
# root 账户：禁用登录，但可 sudo
d-i passwd/root-login boolean false
d-i passwd/root-password-crypted password *

# ---- 安装源 ----
d-i mirror/country string manual
d-i mirror/http/hostname string archive.ubuntu.com
d-i mirror/http/directory string /ubuntu
d-i mirror/http/proxy string

# ---- 分区：整盘 + LVM，全自动，不确认 ----
# 使用第一块 SCSI 盘 /dev/sda
d-i partman-auto/disk string /dev/sda
d-i partman-auto/method string lvm
d-i partman-auto-lvm/guided_size string max
d-i partman-lvm/device_remove_lvm boolean true
d-i partman-lvm/confirm boolean true
d-i partman-lvm/confirm_nooverwrite boolean true
d-i partman-md/device_remove_md boolean true
d-i partman-md/confirm boolean true
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

# ---- 安装内容：Ubuntu 桌面 ----
tasksel tasksel/first multiselect ubuntu-desktop
d-i pkgsel/include string openssh-server curl open-vm-tools
d-i pkgsel/upgrade select full-upgrade
d-i pkgsel/update-policy select none

# ---- ubiquity 特定：自动安装，跳过所有确认 ----
ubiquity ubiquity/summary note
ubiquity ubiquity/reboot boolean true
ubiquity ubiquity/poweroff boolean false
# 关键：让 ubiquity 不提问、直接用 preseed 值安装
ubiquity ubiquity/install/types multiselect ubuntu-desktop

# ---- GRUB 引导 ----
d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string default
d-i grub-installer/with_other_os boolean true

# ---- 安装完成后：不弹出最终对话框，直接重启 ----
d-i finish-install/reboot_in_progress note
d-i debian-installer/exit/poweroff boolean false

# ---- 安装后首次启动命令（写入 netplan 静态 IP、开启 ssh 密码登录）----
d-i preseed/late_command string \\
  in-target sh -c 'echo "{s.username} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/{s.username} && chmod 440 /etc/sudoers.d/{s.username}'; \\
  in-target sh -c 'mkdir -p /etc/netplan && cat > /etc/netplan/01-static.yaml <<EOF
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
        addresses: [{", ".join(s.dns_servers)}]
EOF
'; \\
  in-target sh -c 'sed -i "s/^#\\\\?PermitRootLogin.*/PermitRootLogin yes/" /etc/ssh/sshd_config'; \\
  in-target sh -c 'sed -i "s/^#\\\\?PasswordAuthentication.*/PasswordAuthentication yes/" /etc/ssh/sshd_config'
"""
