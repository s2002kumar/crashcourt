"""Real worker processes for the trial engine: spawn, control socket, and waitpid.

Each worker is a child process in its own session (a Ctrl-C at the terminal reaches the
orchestrator, which ends the workers itself). It gets a Unix socket in a private directory,
listening for exactly one connection, and the kernel's peer credentials must name the spawned
pid. A thread waits for the child, so `worker.exit` carries the status waitpid returned.
Linux only: the runner refuses other hosts before anything here runs.
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, JsonValue

from crashcourt.faults import signals
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.orchestrator.dispatcher import (
    Disconnected,
    Exited,
    Note,
    Received,
    StartError,
)
from crashcourt.orchestrator.topology import Route
from crashcourt.spec.protocol import (
    MAX_LINE,
    Closed,
    Hello,
    ProtocolError,
    encode,
    parse_from_worker,
    read_line,
)
from crashcourt.worker.main import DSN_ENV

#: libpq reads these when a DSN leaves them out; a worker's DSN must be the whole story.
LIBPQ_ADDRESS_VARIABLES = frozenset(
    {"PGHOST", "PGHOSTADDR", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGSYSCONFDIR"}
)


def worker_environment(environ: Mapping[str, str], dsn: str) -> dict[str, str]:
    """The parent's environment without libpq's address variables, plus the worker's DSN."""
    env = {k: v for k, v in environ.items() if k not in LIBPQ_ADDRESS_VARIABLES}
    env[DSN_ENV] = dsn
    return env


def _listen(path: Path) -> socket.socket:
    """A Unix socket at `path` that accepts one connection."""
    if sys.platform == "win32":
        raise OSError("workers need a POSIX host: Unix sockets and signals")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        server.listen(1)
        server.setblocking(False)
    except BaseException:
        server.close()
        raise
    return server


class Channel:
    """One control connection: read as a stream, written through a duplicate of the socket.

    An asyncio stream that fails a write closes its transport, which drops whatever the peer
    sent and the orchestrator has not read yet: a result sent just before the worker died could
    be lost and its op recorded as info. Writing through a separate descriptor keeps the read
    side intact, so the reader always drains every complete line before the end of the stream.
    """

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, out: socket.socket
    ) -> None:
        self.reader = reader
        self._writer = writer  # the read side's transport; never written to
        self._out = out
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, conn: socket.socket) -> Channel:
        if sys.platform == "win32":
            raise OSError("workers need a POSIX host: Unix sockets and signals")
        out = conn.dup()
        try:
            out.setblocking(False)
            reader, writer = await asyncio.open_unix_connection(sock=conn, limit=MAX_LINE)
        except BaseException:
            out.close()
            conn.close()
            raise
        return cls(reader, writer, out)

    async def send(self, message: BaseModel) -> None:
        """ConnectionError (or another OSError) if the peer is gone; reading is unaffected."""
        async with self._lock:
            if self._out.fileno() < 0:
                raise ConnectionError("the control connection is closed")
            await asyncio.get_running_loop().sock_sendall(self._out, encode(message))

    def close(self) -> None:
        self._out.close()
        self._writer.close()


#: Unix socket paths are limited to about 107 bytes; a long TMPDIR would not fit.
_MAX_SOCKET_DIR = 64


def socket_directory() -> Path:
    """A private (0700) directory for the trial's sockets, with a path short enough to bind."""
    base = tempfile.gettempdir()
    return Path(
        tempfile.mkdtemp(prefix="cc-", dir=base if len(base) <= _MAX_SOCKET_DIR else "/tmp")
    )


def peer_pid(conn: socket.socket) -> int:
    """The pid of the process at the other end of a Unix socket, from the kernel."""
    if sys.platform != "linux":
        raise OSError("peer credentials need Linux")
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, _uid, _gid = struct.unpack("3i", creds)
    return int(pid)


class ProcessWorker:
    def __init__(
        self,
        *,
        name: str,
        route: Route,
        run: RunIdentity,
        workload: str,
        sink_url: str,
        trial_dir: Path,
        socket_dir: Path,
        history: HistoryWriter,
        notes: asyncio.Queue[Note],
        cwd: Path,
        saver_mutant: str | None = None,
    ) -> None:
        self.name = name
        self._history = history
        self._notes = notes
        self._channel: Channel | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._socket_path = socket_dir / f"{name}.sock"
        # Until Popen succeeds, a failed open/spawn must not strand the log or socket.
        with ExitStack() as unstarted:
            self._server = _listen(self._socket_path)
            unstarted.callback(self._server.close)
            unstarted.callback(self._socket_path.unlink, missing_ok=True)
            self._log: IO[bytes] = (trial_dir / f"{name}.log").open("wb")
            unstarted.callback(self._log.close)
            argv = [
                sys.executable,
                "-m",
                "crashcourt.worker",
                "--name",
                name,
                "--socket",
                str(self._socket_path),
                "--history",
                str(trial_dir / f"{name}.jsonl"),
                "--batch",
                run.batch,
                "--cell",
                run.cell,
                "--trial",
                str(run.trial),
                "--seed",
                run.seed,
                "--workload",
                workload,
                "--sink-url",
                sink_url,
                *(("--saver-mutant", saver_mutant) if saver_mutant is not None else ()),
            ]
            self.popen: subprocess.Popen[Any] = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                env=worker_environment(os.environ, route.dsn),
                cwd=cwd,
                start_new_session=True,
            )
            unstarted.pop_all()
        self.pid = self.popen.pid
        data: dict[str, JsonValue] = {
            "proc": name,
            "pid": self.pid,
            "proxy": route.proxy,
            "port": route.port,
        }
        if saver_mutant is not None:
            data["saver_mutant"] = saver_mutant  # a test-only defect: never evidence
        history.emit("worker.start", data=data)
        loop = asyncio.get_running_loop()
        self._exited: asyncio.Future[int] = loop.create_future()
        self._waiter = asyncio.ensure_future(asyncio.to_thread(self.popen.wait))
        self._waiter.add_done_callback(self._on_exit)

    def _on_exit(self, waiter: asyncio.Future[int]) -> None:
        code = waiter.result() if not waiter.cancelled() and waiter.exception() is None else None
        returncode = code if code is not None else (self.popen.returncode or 0)
        if not self._exited.done():
            self._exited.set_result(returncode)
        self._notes.put_nowait(Exited(self.name, returncode))

    async def _first(self, work: asyncio.Future[Any], deadline: float, what: str) -> Any:
        """`work`'s result, unless the worker exits or the deadline passes first."""
        loop = asyncio.get_running_loop()
        remaining = max(0.0, deadline - loop.time())
        done, _ = await asyncio.wait(
            {work, self._exited}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if work in done:
            return work.result()
        work.cancel()
        if self._exited.done():
            raise StartError(f"exited with status {self._exited.result()} before {what}")
        raise StartError(f"no {what} within the hello timeout")

    async def hello(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            accept = asyncio.ensure_future(loop.sock_accept(self._server))
            conn, _ = await self._first(accept, deadline, "connecting")
        finally:
            self._server.close()
            self._socket_path.unlink(missing_ok=True)
        if (pid := peer_pid(conn)) != self.pid:
            conn.close()
            raise StartError(f"the connection came from pid {pid}, not the worker {self.pid}")
        self._channel = channel = await Channel.open(conn)
        reader = channel.reader
        try:
            # Not raced against the exit: a worker that dies leaves its lines, then end of stream.
            line = await asyncio.wait_for(read_line(reader), max(0.0, deadline - loop.time()))
            message = parse_from_worker(line)
        except TimeoutError as exc:
            raise StartError("no hello within the hello timeout") from exc
        except (Closed, ProtocolError) as exc:
            raise StartError(f"no valid hello: {exc}") from exc
        if not isinstance(message, Hello) or (message.worker, message.pid) != (self.name, self.pid):
            raise StartError(f"expected hello from {self.name} pid {self.pid}, got {message!r}")
        self._reader_task = asyncio.create_task(self._read(reader))

    async def _read(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                message = parse_from_worker(await read_line(reader))
                self._notes.put_nowait(Received(self.name, message))
        except Closed as exc:
            self._notes.put_nowait(Disconnected(self.name, partial=exc.partial))
        except ProtocolError as exc:
            self._notes.put_nowait(Disconnected(self.name, violation=str(exc)))
        except (ConnectionError, OSError):
            self._notes.put_nowait(Disconnected(self.name))

    async def send(self, message: BaseModel) -> None:
        if self._channel is None:
            raise ConnectionError(f"no control connection to {self.name}")
        await self._channel.send(message)

    async def kill(self, fault_id: str) -> None:
        await asyncio.to_thread(signals.kill, self._history, self.popen, fault_id)

    def force_kill(self) -> None:
        self.popen.kill()  # SIGKILL, unless the child has already been reaped

    @property
    def reaped(self) -> bool:
        """Whether the process has exited and been waited for."""
        return self._waiter.done()

    async def close(self) -> None:
        """Release the socket and log once the worker has exited."""
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
        if self._channel is not None:
            self._channel.close()
        self._server.close()
        self._socket_path.unlink(missing_ok=True)
        # Bounded: a worker that even SIGKILL did not end (the engine gave up on it) keeps its
        # waiter thread; the trial has already said so.
        await asyncio.wait({self._waiter}, timeout=10)
        self._log.close()
