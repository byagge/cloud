"""SSH session helper for AI agent (asyncssh)."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any

import asyncssh

from app.logging import get_logger

log = get_logger("agent.ssh")


@dataclass
class SSHResult:
    exit_code: int
    stdout: str
    stderr: str


class SshSession:
    """Alias used by agent loop."""

    def __init__(self, host: str, username: str, password: str, *, port: int = 22) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self._conn: asyncssh.SSHClientConnection | None = None

    async def connect(self) -> None:
        self._conn = await asyncssh.connect(
            self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            known_hosts=None,
            connect_timeout=30,
            login_timeout=30,
        )

    async def ensure(self) -> None:
        if self._conn is None:
            await self.connect()
            return
        try:
            # cheap keepalive
            await asyncio.wait_for(self._conn.run("true", check=False), timeout=10)
        except Exception:
            log.warning("ssh_reconnect", host=self.host)
            try:
                await self.close()
            except Exception:
                pass
            await self.connect()

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            try:
                await self._conn.wait_closed()
            except Exception:
                pass
            self._conn = None

    async def __aenter__(self) -> "SshSession":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def run(self, command: str, *, timeout: float = 120) -> SSHResult:
        await self.ensure()
        assert self._conn is not None
        try:
            result = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return SSHResult(exit_code=124, stdout="", stderr=f"timeout after {timeout}s")
        except Exception as e:
            # one reconnect retry
            try:
                await self.close()
                await self.connect()
                assert self._conn is not None
                result = await asyncio.wait_for(
                    self._conn.run(command, check=False),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return SSHResult(exit_code=124, stdout="", stderr=f"timeout after {timeout}s")
            except Exception as e2:
                return SSHResult(exit_code=255, stdout="", stderr=f"ssh error: {e2}")
        # exit_status is None when killed by signal or channel closed — never treat as 0
        status = result.exit_status
        stderr = result.stderr or ""
        if status is None:
            sig = getattr(result, "exit_signal", None)
            if sig and isinstance(sig, (tuple, list)) and sig[0]:
                code = -1
                stderr = (stderr + f"\n[terminated by signal {sig[0]}]").strip()
            else:
                code = -1
                if not stderr:
                    stderr = "command ended with no exit status (signal/channel closed)"
        else:
            code = int(status)
        return SSHResult(
            exit_code=code,
            stdout=(result.stdout or "")[-12_000:],
            stderr=stderr[-4_000:],
        )

    async def write_bytes(self, remote_path: str, data: bytes) -> None:
        await self.ensure()
        assert self._conn is not None
        parent = remote_path.rsplit("/", 1)[0]
        if parent:
            await self.run(f"mkdir -p {shlex.quote(parent)}")
        async with self._conn.start_sftp_client() as sftp:
            async with sftp.open(remote_path, "wb") as f:
                await f.write(data)

    async def write_text(self, remote_path: str, content: str) -> None:
        await self.write_bytes(remote_path, content.encode("utf-8", errors="replace"))

    async def read_text(self, remote_path: str, *, max_bytes: int = 50_000) -> str:
        q = shlex.quote(remote_path)
        res = await self.run(f"head -c {int(max_bytes)} {q} 2>/dev/null || true")
        return res.stdout

    async def backup(self, path: str) -> str:
        q = shlex.quote(path)
        res = await self.run(
            f"if [ -e {q} ]; then "
            f"dest={q}.bak.$(date +%Y%m%d-%H%M%S); cp -a {q} \"$dest\" && echo \"$dest\"; "
            f"else echo missing:{q}; fi"
        )
        return (res.stdout or "").strip() or f"{path}.bak"


async def open_ssh(host: str, username: str, password: str, *, port: int = 22) -> SshSession:
    session = SshSession(host, username, password, port=port)
    await session.connect()
    return session


# Back-compat alias
ServerSSH = SshSession
