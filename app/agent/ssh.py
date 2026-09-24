"""SSH session helper for AI agent (asyncssh)."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any

import asyncssh


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
        )

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
        if not self._conn:
            raise RuntimeError("SSH not connected")
        try:
            result = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return SSHResult(exit_code=124, stdout="", stderr=f"timeout after {timeout}s")
        return SSHResult(
            exit_code=int(result.exit_status or 0),
            stdout=(result.stdout or "")[-12_000:],
            stderr=(result.stderr or "")[-4_000:],
        )

    async def write_bytes(self, remote_path: str, data: bytes) -> None:
        if not self._conn:
            raise RuntimeError("SSH not connected")
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
        stamp = "$(date +%Y%m%d-%H%M%S)"
        dest = f"{path}.bak.{stamp}"
        # expand stamp on remote
        res = await self.run(
            f'if [ -e {q} ]; then cp -a {q} "{path}.bak.$(date +%Y%m%d-%H%M%S)" && '
            f'ls -1d {q}.bak.* 2>/dev/null | tail -1; else echo "missing:{path}"; fi'
        )
        return (res.stdout or dest).strip()


async def open_ssh(host: str, username: str, password: str, *, port: int = 22) -> SshSession:
    session = SshSession(host, username, password, port=port)
    await session.connect()
    return session


# Back-compat alias
ServerSSH = SshSession
