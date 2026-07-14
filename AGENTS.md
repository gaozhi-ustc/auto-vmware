# AGENTS.md — auto-vmware 项目约束

本文件是 auto-vmware 项目的持久化目标与约束。Agent（包括 ZCode 及其他兼容工具）进入本目录时会自动读取，作为项目级的强制约定，覆盖默认行为。新增或修改代码、依赖、文档时，必须先符合本文件，再考虑具体实现。

---

## 1. 项目目标

auto-vmware 是一个面向 **VMware Workstation（本地宿主机）** 的 Ubuntu 虚拟机**一键自动化部署/配置**工具集，用 Python 实现。

核心交付能力：
- 一键从 ISO 创建虚拟机：指定名称/用户名/密码/时区/IP，自动完成无人值守安装。
- 安装完成后自动配置：桌面环境（gnome/ubuntu-desktop）、VNC（tiger-vnc + lightdm）、SSH。
- 自动安装 FlClash、Google Chrome（带 `--fix-broken` 依赖修复重试）。
- 自动安装最新版 Node.js（系统级，所有用户可用）。
- 自动启动 FlClash 并导入指定配置。

不做的事（明确边界）：
- 不面向 vCenter / vSphere / ESXi（当前只面向本地 VMware Workstation）。
- 不做多宿主机并发管理。
- 不做监控/告警/性能采集。

---

## 2. 技术栈（强制）

| 项 | 约定 |
|---|---|
| 语言 | Python 3.10+ |
| 宿主机交互 | VMware Workstation CLI：`vmrun`、`vmware-vdiskmanager` |
| ISO 种子构建 | `/usr/lib/vmware/bin/mkisofs`（宿主机自带，避免外部依赖） |
| 无人值守安装 | Ubuntu `autoinstall`（subiquity）+ NoCloud 种子 ISO（cidata） |
| 远程执行/文件传输 | SSH / SCP（基于 autoinstall 创建的用户） |
| 交付形态 | 一组 CLI 脚本，入口统一为 `python -m auto_vmware <子命令>` |
| 依赖/打包 | `pyproject.toml`（PEP 621 标准） |
| 配置注入 | 环境变量 + `.env` 文件 + 命令行参数，**禁止硬编码任何凭据** |
| 代码风格 | `ruff`（lint + format） |

禁止改用与上述冲突的替代方案（如改用 Ansible / Terraform / vCenter SDK）。

---

## 3. 目录结构

```
auto-vmware/
├── AGENTS.md                # 本文件
├── README.md
├── LICENSE
├── pyproject.toml           # 项目元数据 + 依赖 + CLI 入口
├── .gitignore
├── .env.example             # 配置项示例（不含真实值，可提交）
├── src/
│   └── auto_vmware/
│       ├── __init__.py
│       ├── __main__.py      # CLI 总入口：python -m auto_vmware
│       ├── cli.py           # 子命令分发与参数解析
│       ├── config.py        # 运行参数模型（dataclass）
│       ├── vmcreate.py      # vmdk + .vmx 生成
│       ├── cidata.py        # autoinstall user-data + cidata ISO 生成
│       ├── orchestrate.py   # 装机编排：启动/等待/重启/连通性
│       ├── provision.py     # 装机后配置：apt/VNC/lightdm/FlClash/Chrome
│       ├── sshutil.py       # SSH/SCP 封装（基于 paramiko 或系统 ssh）
│       └── log.py           # 日志
└── tests/
    └── ...                  # pytest 单测
```

分层约束：VMware 交互（vmcreate/orchestrate）、种子生成（cidata）、远程配置（provision）必须分开，不得跨层耦合。

---

## 4. 配置与凭据（安全约束）

- 路径类配置（ISO、deb、yaml、安装目录）通过命令行参数或环境变量指定。
- `.env.example` 可提交，列明所需变量名和示例占位值。
- **`.env`（含真实值）必须被 `.gitignore` 忽略，永远不得提交。**
- VM 用户密码在运行时由参数提供，不得落盘到仓库；编排过程中的临时文件用完即删。
- 凭据不得出现在日志输出、错误信息、测试用例或提交历史中。

默认路径常量（可被环境变量覆盖，写入 `config.py`）：
```
ISO_PATH       = /DATA/downloads/ubuntu-22.04.5-desktop-amd64.iso
VM_BASE_DIR    = /DATA/vmware
FLCLASH_DEB    = /DATA/downloads/FlClash-0.8.93-linux-amd64.deb
CHROME_DEB     = /DATA/downloads/google-chrome-stable_current_amd64.deb
CLASH_CONFIG   = /DATA/downloads/gaozhi_lagos.yaml
NODE_TARBALL   = /DATA/downloads/node-v24.18.0-linux-x64.tar.xz
NAT_GATEWAY    = 192.168.167.2
NAT_NETMASK    = 255.255.255.0
NAT_DNS        = 223.5.5.5 223.6.6.6
```

---

## 5. 硬件与网络约定（强制）

### 硬件（脚本内固定，不接受运行时覆盖）

| 项 | 值 |
|---|---|
| CPU 拓扑 | 4 sockets × 2 cores/socket = 8 vCPU |
| 内存 | 8192 MB |
| 磁盘 | 100 GB，单一可增长虚拟磁盘（monolithic，vdiskmanager `-t 0`） |

以上为固定参数，CLI 不暴露覆盖开关。

### 网络

- 虚拟机网络连接方式固定为 **NAT**（VMnet8）。
- 网关 `192.168.167.2`，子网掩码 `255.255.255.0`，DNS `223.5.5.5` 与 `223.6.6.6`。
- 虚拟机 IP 形如 `192.168.167.<n>`，`<n>` 由运行时参数指定。
- `<n>` 推荐范围 `3–127`，避开 VMware NAT DHCP 范围 `128–254`，避免地址冲突。
- 脚本在运行前需校验：`<n>` 不为 1（宿主机）、2（网关）、0（网络地址）、255（广播）。

---

## 6. 装机流程编排（强制时序）

1. 校验宿主机环境（vmrun/vdiskmanager/mkisofs/ISO/deb/yaml 可用）。
2. 在 `VM_BASE_DIR/<vmname>/` 下生成 vmdk 与 .vmx（NAT、双 CD-ROM：ISO + cidata）。
3. 启动 VM，注入 NoCloud 种子触发 autoinstall。
4. 轮询等待：autoinstall 完成（VM 自动关机或重启）。
5. 二次启动 VM，等待 SSH 可达（静态 IP）。
6. 通过 SSH 执行装机后配置（apt 桌面/VNC/lightdm → 重启 → 启动 vncserver）。
7. 通过 SSH/SCP 安装 FlClash、Chrome（缺失依赖用 `apt install --fix-broken` 后重试）。
8. 通过 SSH/SCP 上传 Node.js 预编译包，以 root 解压到 `/usr/local`，软链接到 `/usr/local/bin`（所有用户可用）。
9. 在 `DISPLAY=:1` 启动 FlClash 并导入配置。
10. 输出最终状态（VM 名称、IP、VNC 端口、SSH 命令）。

每一步必须有超时与明确失败提示，不得无限等待。

---

## 7. 代码质量

- 所有新增功能需附带 `pytest` 单测（至少覆盖纯逻辑函数：IP 校验、.vmx 文本生成、user-data 模板渲染）。
- 提交前必须通过 `ruff check` 与 `ruff format --check`。
- 公共函数必须有 docstring，标注参数与返回值。
- 类型注解必填。

---

## 8. 提交规范

- 分支：`main` 为主线，功能开发用 `feat/<名称>`，修复用 `fix/<名称>`。
- Commit message 约定（Conventional Commits 风格）：
  - `feat: 新增一键装机 CLI`
  - `fix: 修复 NAT IP 校验遗漏`
  - `docs: 补充 README`
  - `refactor: 抽离 cidata 生成逻辑`
  - `test: 增加 IP 校验单测`
  - `chore: 升级依赖`
- 小步提交、频繁推送。

---

## 9. 安全与操作准则

- 对 VMware 与 VM 的写操作在 CLI 层默认带**确认提示**，提供 `--yes` 跳过。
- 破坏性操作（删除 VM、覆盖磁盘）必须显式二次确认，且日志记录操作者、目标、时间。
- 长时间运行的装机流程需输出进度（当前阶段、已耗时、是否在等待），避免用户误以为卡死。

---

## 10. 与本文件的关系

- 本文件是项目最高约束。当用户临时指令与本文件冲突时，Agent 应**先指出冲突并确认**，不得擅自违背。
- 本文件随项目演进更新；更新本身应作为一次独立 commit（`docs: 更新 AGENTS.md 约束`）。
