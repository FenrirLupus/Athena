"""Windows backend — INTERFACE STUB (simplification 2026-08-11).

The host runs Linux only; the active backend is always LinuxBackend.
The Windows backend is kept as a documented interface so the platform
abstraction (oslayer) stays complete, but the 400-line implementation
was dead weight — never imported, never exercised, unverifiable.

If Athena is ever deployed on Windows, reimplement each method using
the Linux backend (oslayer/linux.py) as the behavior reference. Every
method raises NotImplementedError with a clear message so a Windows
deploy fails loudly at the first call instead of silently misbehaving.

The WRAPPER_NAMES contract (oslayer/__init__.py) is preserved: every
wrapper name the registry can dispatch still exists here.
"""


class WindowsBackend:
    name = "windows"

    def _stub(self, op: str) -> None:
        raise NotImplementedError(
            f"WindowsBackend.{op} is a stub — Athena currently runs on "
            "Linux only. Reimplement this method from oslayer/linux.py "
            "before deploying on Windows.")

    def quote(self, path: str) -> str:
        self._stub("quote")

    def build_command(self, name: str, args: dict) -> str:
        self._stub("build_command")

    def run_command(self, command: str, stdin: str = "", timeout: float = 60.0) -> str:
        self._stub("run_command")

    def read(self, path: str) -> str:
        self._stub("read")

    def write(self, path: str, content: str) -> str:
        self._stub("write")

    def append(self, path: str, content: str) -> str:
        self._stub("append")

    def replace(self, path: str, old: str, new: str, replace_all: bool = False) -> str:
        self._stub("replace")

    def patch(self, path: str, hunks: list[dict]) -> str:
        self._stub("patch")

    def delete(self, path: str) -> str:
        self._stub("delete")

    def copy(self, src: str, dst: str) -> str:
        self._stub("copy")

    def move(self, src: str, dst: str) -> str:
        self._stub("move")

    def rename(self, path: str, new_name: str) -> str:
        self._stub("rename")

    def mkdir(self, path: str, recursive: bool = True) -> str:
        self._stub("mkdir")

    def exists(self, path: str) -> str:
        self._stub("exists")

    def list(self, path: str = "") -> str:
        self._stub("list")

    def tree(self, path: str = "", max_depth: int = 3) -> str:
        self._stub("tree")

    def find(self, path: str = "", pattern: str = "*", file_type: str = "") -> str:
        self._stub("find")

    def search(self, pattern: str, path: str = "", file_glob: str = "*.py") -> str:
        self._stub("search")

    def stat(self, path: str) -> str:
        self._stub("stat")

    def hash(self, path: str, algo: str = "sha256") -> str:
        self._stub("hash")

    def execute(self, command: str, timeout: float = 60.0) -> str:
        self._stub("execute")

    def terminal(self, command: str, timeout: float = 60.0) -> str:
        self._stub("terminal")

    def process(self, name: str = "") -> str:
        self._stub("process")

    def kill(self, pid: int, force: bool = False) -> str:
        self._stub("kill")

    def download(self, url: str, dest: str, timeout: float = 30.0) -> str:
        self._stub("download")

    def upload(self, path: str, url: str, timeout: float = 30.0) -> str:
        self._stub("upload")

    def compress(self, path: str, dest: str = "") -> str:
        self._stub("compress")

    def extract(self, path: str, dest: str = "") -> str:
        self._stub("extract")
