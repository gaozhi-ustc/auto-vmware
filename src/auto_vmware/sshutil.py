"""SSH/SCP 工具封装。

装机完成后通过 SSH 远程执行命令、SCP 传输文件。使用 paramiko 作为客户端，
首次连接自动接受主机密钥（装机环境，安全风险可接受）。
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from auto_vmware.log import get_logger

_log = get_logger("ssh")

# SSH 连接默认参数
SSH_PORT = 22
DEFAULT_CONNECT_TIMEOUT = 15  # 单次连接超时（秒）
DEFAULT_EXEC_TIMEOUT = 600  # 单条命令执行超时（秒）


class SSHError(RuntimeError):
    """SSH 操作失败。"""


@dataclass
class SSHResult:
    """命令执行结果。"""

    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def wait_for_ssh(
    host: str,
    username: str,
    password: str,
    port: int = SSH_PORT,
    timeout_total: int = 600,
    interval: int = 5,
) -> None:
    """轮询等待 SSH 可达且能成功认证。

    Args:
        host: 目标主机 IP。
        username: 用户名。
        password: 密码。
        port: SSH 端口。
        timeout_total: 总等待时长（秒）。
        interval: 轮询间隔（秒）。

    Raises:
        SSHError: 超时仍不可达。
    """
    import paramiko

    deadline = time.time() + timeout_total
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=DEFAULT_CONNECT_TIMEOUT,
                banner_timeout=DEFAULT_CONNECT_TIMEOUT,
                auth_timeout=DEFAULT_CONNECT_TIMEOUT,
                allow_agent=False,
                look_for_keys=False,
            )
            client.close()
            _log.info("SSH 可达（尝试 %d 次）: %s@%s", attempt, username, host)
            return
        except Exception as e:  # noqa: BLE001
            _log.debug("第 %d 次 SSH 尝试失败: %s", attempt, e)
            time.sleep(interval)
    raise SSHError(f"等待 SSH 超时（{timeout_total}s）: {host}")


def run(
    host: str,
    username: str,
    password: str,
    command: str,
    port: int = SSH_PORT,
    timeout: int = DEFAULT_EXEC_TIMEOUT,
    sudo: bool = False,
) -> SSHResult:
    """在远程主机执行单条命令。

    Args:
        host: 目标主机 IP。
        username: 用户名。
        password: 密码。
        command: 要执行的 shell 命令。
        port: SSH 端口。
        timeout: 命令超时（秒）。
        sudo: 是否用 sudo 包裹（密码通过 -S 从 stdin 注入）。

    Returns:
        SSHResult。

    Raises:
        SSHError: 连接失败或命令超时。
    """
    import paramiko

    if sudo:
        # -S: 从 stdin 读密码；-p '' 不打印提示符
        full = f"echo {password!r} | sudo -S -p '' bash -lc {shell_quote(command)!s}"
        # 上面 sudo 会被 bash -lc 解析，改用更直接的方式：
        full = f"sudo -S -p '' -- bash -c {shell_quote(command)}"
        # 密码通过 paramiko exec 的 stdin 传入更安全
        client = _connect(host, username, password, port)
        try:
            chan = client.get_transport().open_session()
            chan.settimeout(timeout)
            chan.get_pty()
            chan.exec_command(full)
            chan.sendall(password + "\n")
            out = b""
            err = b""
            while True:
                if chan.recv_ready():
                    out += chan.recv(65536)
                if chan.recv_stderr_ready():
                    err += chan.recv_stderr(65536)
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                time.sleep(0.05)
            rc = chan.recv_exit_status()
            return SSHResult(rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))
        finally:
            client.close()

    client = _connect(host, username, password, port)
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        rc = stdout.channel.recv_exit_status()
        return SSHResult(
            rc,
            stdout.read().decode("utf-8", "replace"),
            stderr.read().decode("utf-8", "replace"),
        )
    finally:
        client.close()


def run_many(
    host: str,
    username: str,
    password: str,
    commands: List[Tuple[str, dict]],
    port: int = SSH_PORT,
) -> List[SSHResult]:
    """顺序执行多条命令。每条形如 (command, opts)，opts 支持 sudo/timeout。

    Args:
        host: 目标主机 IP。
        username/password: 认证。
        commands: [(command, {"sudo": bool, "timeout": int}), ...]
        port: SSH 端口。

    Returns:
        每条命令的 SSHResult 列表。
    """
    results = []
    for cmd, opts in commands:
        _log.info("执行: %s", cmd)
        r = run(
            host,
            username,
            password,
            cmd,
            port=port,
            sudo=opts.get("sudo", False),
            timeout=opts.get("timeout", DEFAULT_EXEC_TIMEOUT),
        )
        if r.stdout.strip():
            _log.debug("[stdout] %s", r.stdout.strip()[-2000:])
        if not r.ok:
            _log.warning("[rc=%d][stderr] %s", r.rc, r.stderr.strip()[-2000:])
        results.append(r)
    return results


def scp_upload(
    host: str,
    username: str,
    password: str,
    local_path: str,
    remote_path: str,
    port: int = SSH_PORT,
    timeout: int = 600,
) -> None:
    """上传单个文件到远程主机。

    Args:
        host: 目标主机 IP。
        username/password: 认证。
        local_path: 本地文件路径。
        remote_path: 远程目标路径。
        port: SSH 端口。
        timeout: 超时（秒）。
    """
    import paramiko

    if not os.path.isfile(local_path):
        raise SSHError(f"本地文件不存在: {local_path}")
    client = _connect(host, username, password, port)
    try:
        sftp = client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
        _log.info("上传 %s -> %s@%s:%s", os.path.basename(local_path), username, host, remote_path)
    finally:
        client.close()


def _connect(host: str, username: str, password: str, port: int):
    """建立 paramiko 连接。"""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=DEFAULT_CONNECT_TIMEOUT,
            banner_timeout=DEFAULT_CONNECT_TIMEOUT,
            auth_timeout=DEFAULT_CONNECT_TIMEOUT,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as e:  # noqa: BLE001
        raise SSHError(f"SSH 连接失败 {username}@{host}:{port}: {e}") from e
    return client


def shell_quote(s: str) -> str:
    """对字符串做 POSIX shell 安全引用。"""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def wait_for_port(
    host: str, port: int, timeout_total: int = 600, interval: int = 5
) -> bool:
    """等待 TCP 端口可达。

    Returns:
        True 表示可达，False 表示超时。
    """
    deadline = time.time() + timeout_total
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                return True
        except OSError:
            time.sleep(interval)
    return False
