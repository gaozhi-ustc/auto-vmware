# auto-vmware

> 一键自动化 Ubuntu 虚拟机部署/配置工具（面向本地 VMware Workstation）。

通过一个命令，从 Ubuntu ISO 无人值守创建虚拟机，自动完成静态网络配置、用户创建、桌面环境安装、VNC、FlClash 与 Chrome 部署。

---

## 功能

- ✅ 从 ISO 无人值守安装 Ubuntu 22.04（autoinstall + NoCloud 种子）
- ✅ 运行时指定：虚拟机名、用户名、密码、时区、IP 尾段
- ✅ NAT 网络（VMnet8）：网关 `192.168.167.2`，DNS `223.5.5.5 / 223.6.6.6`
- ✅ 自动安装桌面环境（gnome/ubuntu-desktop）+ VNC（tigervnc + lightdm）
- ✅ 自动安装 FlClash、Google Chrome（缺失依赖 `apt --fix-broken` 兜底重试）
- ✅ 在 `DISPLAY=:1` 启动 FlClash 并导入配置

---

## 环境要求（宿主机）

| 项 | 要求 |
|---|---|
| 系统 | Linux（VMware Workstation for Linux） |
| VMware | VMware Workstation（含 `vmrun`、`vmware-vdiskmanager`） |
| mkisofs | `/usr/lib/vmware/bin/mkisofs`（VMware 自带） |
| Python | 3.10+ |
| Python 包 | `paramiko`（SSH/SCP） |
| NAT 网络 | VMnet8，网段 `192.168.167.0/24`，网关 `.2` |

### 必备文件（默认路径，可覆盖）

| 用途 | 默认路径 | 环境变量 |
|---|---|---|
| Ubuntu ISO | `/DATA/downloads/ubuntu-22.04.5-desktop-amd64.iso` | `AUTO_VMWARE_ISO_PATH` |
| FlClash deb | `/DATA/downloads/FlClash-0.8.93-linux-amd64.deb` | `AUTO_VMWARE_FLCLASH_DEB` |
| Chrome deb | `/DATA/downloads/google-chrome-stable_current_amd64.deb` | `AUTO_VMWARE_CHROME_DEB` |
| Clash 配置 | `/DATA/downloads/gaozhi_lagos.yaml` | `AUTO_VMWARE_CLASH_CONFIG` |
| VM 父目录 | `/DATA/vmware` | `AUTO_VMWARE_VM_BASE_DIR` |

---

## 安装

```bash
cd /home/gaozhi/ZCodeProject/auto-vmware
pip install -e .            # 安装本项目（含 auto-vmware 命令）
# 或安装开发依赖（pytest/ruff）
pip install -e '.[dev]'
```

---

## 使用

### 一键部署（推荐）

```bash
python -m auto_vmware deploy \
    --name ubuntu-test \
    --username gaozhi \
    --password 'your_password' \
    --timezone Asia/Shanghai \
    --ip-last 50 \
    --yes
```

参数说明：

| 参数 | 必填 | 说明 |
|---|---|---|
| `--name` | 是 | 虚拟机名称（同时作为 hostname 与目录名） |
| `--username` | 是 | VM 内用户名 |
| `--password` | 是 | 用户密码（同时用于 sudo、VNC） |
| `--timezone` | 是 | 时区，如 `Asia/Shanghai` |
| `--ip-last` | 是 | IP 尾段，最终 IP = `192.168.167.<ip-last>`，推荐 `3–127` |
| `--cpu/--mem-mb/--disk-gb` | 否 | 硬件规格，默认 4 核 / 8192MB / 60GB |
| `--iso/--vm-base/...` | 否 | 覆盖默认路径 |
| `--yes` | 否 | 跳过确认提示 |
| `--gui` | 否 | 以 GUI 模式启动 VM（默认后台 `nogui`） |
| `--verbose` | 否 | DEBUG 日志 |

### 子命令

```bash
# 仅创建 VM（不启动装机）
python -m auto_vmware create --name x --username u --password p --timezone Asia/Shanghai --ip-last 50

# 仅执行装机后配置（VM 已装好且 SSH 可达）
python -m auto_vmware provision --name x --username u --password p --timezone Asia/Shanghai --ip-last 50
```

---

## 部署流程

1. 校验宿主机环境（工具、ISO、deb、yaml 齐备）
2. 生成 NoCloud 种子 ISO（`autoinstall` user-data + meta-data）
3. 创建 vmdk 磁盘 + .vmx 配置（NAT、双 CD-ROM：ISO + 种子）
4. 启动 VM，autoinstall 无人值守安装
5. 等待安装完成、VM 重启、SSH 可达
6. **步骤3**：`apt install` 桌面/VNC/lightdm → 切 lightdm → 重启 → 启动 `vncserver :1`
7. **步骤4**：安装 FlClash、Chrome（`--fix-broken` 兜底）
8. **步骤5**：`DISPLAY=:1` 启动 FlClash 并导入配置

---

## 部署后访问

- **SSH**：`ssh <username>@192.168.167.<ip-last>`
- **VNC**：`<宿主机可访问的IP>:5901`，密码同用户密码

---

## 项目结构

```
auto-vmware/
├── AGENTS.md             # 项目约束（最高优先级）
├── README.md
├── pyproject.toml
├── src/auto_vmware/
│   ├── cli.py            # CLI 入口与子命令
│   ├── config.py         # 参数模型、默认值、校验
│   ├── cidata.py         # autoinstall user-data + 种子 ISO
│   ├── vmcreate.py       # vmdk + .vmx 生成
│   ├── orchestrate.py    # vmrun 编排（启动/等待/重启）
│   ├── provision.py      # 步骤3/4/5 装机后配置
│   ├── sshutil.py        # SSH/SCP 封装
│   └── log.py
└── tests/
```

---

## 网络约定

- NAT 网段 `192.168.167.0/24`，网关 `192.168.167.2`
- DNS：`223.5.5.5`、`223.6.6.6`
- IP 尾段推荐 `3–127`（避开 VMware NAT DHCP 范围 `.128–.254`）
- 禁止值：`0/1/2/255`

---

## 许可证

MIT
