"""Confinement policy installed inside the sandbox worker process.

Threat model (SPEC N1): this is a **defence-in-depth barrier against
accidental and casual misuse**, not a hostile-code jail.  It runs in the
same interpreter as the user's code, so a determined attacker with
arbitrary Python can eventually get past any in-process check.  The
guarantees it does make, and that the containment suite enforces:

* filesystem writes are confined to the session temp directory;
* filesystem reads are confined to the session dir plus the Python
  installation (CadQuery reads its own data files);
* no network of any kind, including the C accelerator modules;
* no process creation: ``fork``, ``spawn*``, ``exec*``, ``system``,
  ``popen``, ``subprocess``;
* dangerous modules cannot be imported, including via ``importlib``.

Anything stronger requires OS-level confinement (seccomp/bwrap, a
container, or a Windows restricted token), tracked separately.  The
process-level limits in ``sandbox.py`` — memory, CPU, file size, process
count, wall clock — are enforced by the OS and *are* hard guarantees.
"""
from __future__ import annotations

import builtins
import contextlib
import importlib
import importlib.abc
import importlib.machinery
import io
import os
import site
import sys
import sysconfig
import threading
from collections.abc import Callable, Sequence
from typing import Any

# Modules that are never importable from inside the sandbox, whatever
# route is taken.  Includes the C accelerators that shadow the pure
# Python modules (`socket` is useless if `_socket` is reachable).
DENIED_MODULES = frozenset(
    {
        # process control
        "subprocess", "multiprocessing", "_posixsubprocess", "pty", "tty",
        # networking
        "socket", "_socket", "ssl", "_ssl", "asyncio", "selectors",
        "select", "socketserver", "http", "urllib", "urllib3", "requests",
        "httpx", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
        "xmlrpc", "webbrowser", "wsgiref", "email",
        # native code / memory escapes
        "ctypes", "_ctypes", "cffi", "mmap",
        # filesystem bulk operations
        "shutil", "tempfile", "glob", "fileinput",
        # code loading and serialisation
        "pickle", "_pickle", "marshal", "shelve", "dbm", "runpy",
        "compileall", "py_compile", "zipimport",
        # misc
        "signal", "resource", "pwd", "grp", "crypt", "getpass",
        "concurrent", "threading", "_thread",
    }
)

# What user code may import directly.  Library code is not restricted by
# the allowlist (CadQuery imports lazily), but the deny-list above still
# applies to everyone.
USER_ALLOWED_MODULES = frozenset(
    {"cadquery", "cq", "math", "numpy", "OCP", "random", "statistics",
     "itertools", "functools", "operator", "collections", "decimal",
     "fractions", "json", "re", "copy", "typing", "dataclasses", "enum",
     "time", "string", "bisect", "heapq", "textwrap", "warnings"}
)

USER_CODE_FILENAME = "<cad>"


class SandboxViolation(PermissionError):
    """Raised when user code attempts something the sandbox forbids."""


# ------------------------------------------------------------------
# Path confinement
# ------------------------------------------------------------------


class _PathPolicy:
    def __init__(self, tmpdir: str) -> None:
        # Thread-local so that resolving a path cannot re-enter the check
        # that is resolving it. See the long comment in `check`.
        self._resolving = threading.local()
        self.tmpdir = os.path.realpath(tmpdir)
        roots = {
            sys.prefix,
            sys.base_prefix,
            sysconfig.get_paths().get("stdlib", ""),
            sysconfig.get_paths().get("purelib", ""),
            sysconfig.get_paths().get("platlib", ""),
            os.path.dirname(os.__file__),
        }
        with contextlib.suppress(Exception):
            roots.update(site.getsitepackages())
        with_user = site.getusersitepackages()
        if isinstance(with_user, str):
            roots.add(with_user)
        self.read_roots = (
            *(os.path.realpath(r) for r in roots if r),
            self.tmpdir,
        )

    def _resolve(self, path: Any) -> str | None:
        """Best-effort absolute path; None when not a filesystem path."""
        if isinstance(path, int):  # already-open fd
            return None
        try:
            raw = os.fspath(path)
        except TypeError:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        resolved: str = os.path.realpath(os.path.abspath(raw))
        return resolved

    @staticmethod
    def _within(path: str, root: str) -> bool:
        return path == root or path.startswith(root + os.sep)

    def check(self, path: Any, *, write: bool) -> None:
        # `_resolve` calls `os.path.realpath`, which on POSIX is pure
        # Python that walks the path calling `os.lstat` and `os.readlink`
        # -- both of which this module wraps, so each one re-enters
        # `check`, which resolves again, until the interpreter dies with
        # RecursionError. Every sandboxed execution on Linux and macOS
        # failed this way, surfacing as whatever confusing error the
        # library in the middle happened to raise (CadQuery's selector
        # grammar reported an arity mismatch). It never appeared on the
        # development machine because Windows `realpath` is one C call
        # into `nt._getfinalpathname` and touches neither wrapper.
        #
        # Skipping the check while we are inside one is safe: between the
        # flag going up and coming down nothing runs but `os.path`
        # arithmetic and stat calls on the very path the caller handed
        # us. No user code can execute in that window, so nothing can use
        # it to reach a file it would otherwise be denied.
        if getattr(self._resolving, "active", False):
            return
        self._resolving.active = True
        try:
            self._check(path, write=write)
        finally:
            self._resolving.active = False

    def _check(self, path: Any, *, write: bool) -> None:
        resolved = self._resolve(path)
        if resolved is None:
            return
        if write:
            if not self._within(resolved, self.tmpdir):
                msg = (
                    f"Sandbox denied write outside the session directory: "
                    f"{resolved}. Write files under the working directory "
                    f"only."
                )
                raise SandboxViolation(msg)
            return
        if not any(self._within(resolved, r) for r in self.read_roots):
            msg = (
                f"Sandbox denied read outside the session directory: "
                f"{resolved}."
            )
            raise SandboxViolation(msg)


_WRITE_MODE_CHARS = frozenset("wxa+")


def _mode_is_write(mode: str) -> bool:
    return any(ch in _WRITE_MODE_CHARS for ch in mode)


def install_path_guard(tmpdir: str) -> None:
    """Confine filesystem access to the session directory."""
    policy = _PathPolicy(tmpdir)
    real_open = builtins.open
    real_io_open = io.open
    real_os_open = os.open

    def guarded_open(
        file: Any, mode: str = "r", *args: Any, **kwargs: Any
    ) -> Any:
        policy.check(file, write=_mode_is_write(mode))
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(
        file: Any, mode: str = "r", *args: Any, **kwargs: Any
    ) -> Any:
        policy.check(file, write=_mode_is_write(mode))
        return real_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(
        path: Any, flags: int, *args: Any, **kwargs: Any
    ) -> Any:
        write = bool(
            flags
            & (
                getattr(os, "O_WRONLY", 0)
                | getattr(os, "O_RDWR", 0)
                | getattr(os, "O_CREAT", 0)
                | getattr(os, "O_APPEND", 0)
                | getattr(os, "O_TRUNC", 0)
            )
        )
        policy.check(path, write=write)
        return real_os_open(path, flags, *args, **kwargs)

    builtins.open = guarded_open
    io.open = guarded_io_open
    os.open = guarded_os_open

    def wrap(
        name: str, *, write: bool, positions: Sequence[int] = (0,)
    ) -> None:
        original = getattr(os, name, None)
        if original is None:
            return

        def guarded(*args: Any, **kwargs: Any) -> Any:
            for pos in positions:
                if len(args) > pos:
                    policy.check(args[pos], write=write)
            return original(*args, **kwargs)

        setattr(os, name, guarded)

    for fn in ("remove", "unlink", "rmdir", "mkdir", "makedirs", "truncate",
               "chmod", "chown", "utime", "removedirs", "mknod", "link"):
        wrap(fn, write=True)
    for fn in ("rename", "replace", "renames", "symlink"):
        wrap(fn, write=True, positions=(0, 1))
    for fn in ("listdir", "scandir", "walk", "stat", "lstat", "readlink"):
        wrap(fn, write=False)


# ------------------------------------------------------------------
# Process creation
# ------------------------------------------------------------------

_PROCESS_FUNCTIONS = (
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp",
    "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
    "spawnve", "spawnvp", "spawnvpe", "startfile", "kill", "killpg",
    "abort", "_exit",
)


def install_process_guard() -> None:
    """Block every route from ``os`` to a new or signalled process."""

    def denied(name: str) -> Callable[..., Any]:
        def raiser(*_a: Any, **_kw: Any) -> Any:
            msg = f"os.{name}() is blocked in the CAD sandbox."
            raise SandboxViolation(msg)

        return raiser

    for name in _PROCESS_FUNCTIONS:
        if hasattr(os, name):
            setattr(os, name, denied(name))


# ------------------------------------------------------------------
# Network
# ------------------------------------------------------------------


def install_network_guard() -> None:
    """Neutralise any socket implementation already resident in memory.

    Patching ``socket.socket`` alone is not enough: the C accelerator
    ``_socket`` is what actually opens a file descriptor, and importing
    it directly used to bypass the guard entirely.
    """

    def blocked_factory(label: str) -> Callable[..., Any]:
        def blocked(*_a: Any, **_kw: Any) -> Any:
            msg = f"Network access is disabled in the CAD sandbox ({label})."
            raise SandboxViolation(msg)

        return blocked

    for mod_name in ("socket", "_socket"):
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr in (
            "socket", "socketpair", "create_connection", "create_server",
            "getaddrinfo", "gethostbyname", "gethostbyname_ex",
            "dup", "fromfd",
        ):
            if hasattr(mod, attr):
                with contextlib.suppress(AttributeError, TypeError):
                    setattr(mod, attr, blocked_factory(f"{mod_name}.{attr}"))


# ------------------------------------------------------------------
# Imports
# ------------------------------------------------------------------


def _top_level(name: str) -> str:
    return name.split(".")[0]


def _called_from_user_code() -> bool:
    """True when the immediate caller is the user's own module.

    CadQuery imports lazily while executing user calls, so the allowlist
    must apply to the user's own ``import`` statements only.  The
    deny-list applies to everyone and is enforced separately.
    """
    frame = sys._getframe(2)
    return bool(frame and frame.f_code.co_filename == USER_CODE_FILENAME)


class _DenyFinder(importlib.abc.MetaPathFinder):
    """Blocks denied modules however the import is routed.

    ``importlib.import_module`` does not go through ``builtins.__import__``,
    so patching that alone left a hole.  A meta-path finder sits on the
    one path every import mechanism shares.
    """

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if _top_level(fullname) in DENIED_MODULES:
            msg = (
                f"Import of '{fullname}' is not allowed in the CAD sandbox."
            )
            raise ImportError(msg)
        return None


def install_import_guard() -> None:
    """Install the deny-list finder and the user-code allowlist."""
    sys.meta_path.insert(0, _DenyFinder())

    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        top = _top_level(name)
        if top in DENIED_MODULES:
            msg = f"Import of '{name}' is not allowed in the CAD sandbox."
            raise ImportError(msg)
        if (
            level == 0
            and top not in USER_ALLOWED_MODULES
            and _called_from_user_code()
        ):
            msg = (
                f"Import of '{name}' is not allowed in the CAD sandbox. "
                f"Available: cadquery (as cq), math, numpy."
            )
            raise ImportError(msg)
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import  # type: ignore[assignment]

    # `importlib.import_module` bypasses `builtins.__import__` entirely.
    real_import_module = importlib.import_module

    def guarded_import_module(name: str, package: str | None = None) -> Any:
        if _top_level(name.lstrip(".")) in DENIED_MODULES:
            msg = f"Import of '{name}' is not allowed in the CAD sandbox."
            raise ImportError(msg)
        return real_import_module(name, package)

    importlib.import_module = guarded_import_module

    # Drop resident denied modules so a cached entry cannot be fetched
    # straight out of sys.modules. Existing references inside already
    # imported libraries keep working; the guards above cover those.
    for name in list(sys.modules):
        if _top_level(name) in DENIED_MODULES:
            sys.modules.pop(name, None)


def install_all(tmpdir: str) -> None:
    """Install every guard, in the order they must be applied."""
    install_network_guard()
    install_process_guard()
    install_path_guard(tmpdir)
    install_import_guard()
