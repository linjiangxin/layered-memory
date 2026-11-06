# -*- coding: utf-8 -*-
"""锁.py —— 原子写 + 系统级文件锁。零第三方依赖，只用标准库。

两个原语，一层封装：

  atomic_write(path, data, encoding="utf-8")
      写同目录临时文件 `.tmp-<pid>-<随机>` → flush → os.fsync → os.replace。
      中途炸了就删临时文件，目标文件要么是旧的、要么是新的，不会半截。

  locked(path, timeout=5.0)  (上下文管理器)
      把 path 当锁文件（真正的锁文件是 `<path>.lock`），用**系统级锁**：
        Windows → msvcrt.locking(fd, LK_NBLCK, 1) 轮询
        POSIX   → fcntl.flock(fd, LOCK_EX | LOCK_NB) 轮询
      拿不到就一直轮询，超过 timeout 抛 LockTimeout（TimeoutError 的子类）。
      finally 一定解锁 + 关 fd —— 进程崩了由内核回收，锁不会烂在那儿。

  rewriting(path, timeout=5.0)  (上下文管理器)
      把「读 — 改 — 写」整段包起来：持锁 → 读全文 → yield 一个 doc → 原子写回。
      doc.text 是正文；只有你给它赋过值才会写盘（纯读不落盘、不产临时文件）。

  read_bytes(path, timeout=5.0) / read_json(path, default=None, timeout=5.0)
      读侧同样带退避：写的人正在 os.replace 那一瞬，Windows 会把目标短暂锁成
      EACCES。read_json 读到坏 JSON 只打一条 WARN 到 stderr 并返回 default，不抛。

用法：
    import 锁
    锁.atomic_write("a.md", "整篇新内容\\n")

    with 锁.rewriting("a.md") as doc:
        doc.text = doc.text + "- 新条目\\n"

    with 锁.locked("a.md", timeout=2.0):
        ...                       # 这段里别人拿不到 a.md.lock

几条纪律（README 里也写了）：
  * 只锁写，不锁读 —— 读靠 os.replace 的原子性保证看不到半截文件。
  * 持锁期间别调子进程 / 网络 / 递归拿同一把锁（锁不重入，会自己等自己）。
  * 网络盘（SMB/NFS）上的 flock 语义不保证，别指望它跨机器。
  * __pycache__ 之类不锁：那是解释器自己写，锁了也没用。
"""
import contextlib
import errno
import io
import json
import os
import random
import sys
import time

if os.name == "nt":                                  # pragma: no cover - 平台分支
    import msvcrt
else:                                                 # pragma: no cover
    import fcntl

__all__ = ["LockTimeout", "ReadTimeout", "atomic_write", "locked", "rewriting",
           "lock_path", "read_bytes", "read_json",
           "DEFAULT_TIMEOUT", "REPLACE_TIMEOUT", "TMP_PREFIX"]

DEFAULT_TIMEOUT = 5.0     # 秒
REPLACE_TIMEOUT = 5.0     # os.replace / 读文件撞上「被占用」时的退避总预算（秒）
TMP_PREFIX = ".tmp-"
LOCK_SUFFIX = ".lock"
POLL_MIN, POLL_MAX = 0.002, 0.05

# os.replace 撞上「目标正被另一个进程 open 着」时，Windows 报的就是这几个码。
# WinError 5 = 拒绝访问（ERROR_ACCESS_DENIED），32/33 = 共享冲突/锁冲突；
# 落到 errno 上是 EACCES / EPERM。别的错误码一概不重试。
RETRY_WINERRORS = (5, 32, 33)
RETRY_ERRNOS = (errno.EACCES, errno.EPERM)
BACKOFF_MIN, BACKOFF_MAX = 0.01, 0.05


class LockTimeout(TimeoutError):
    """等锁超时。TimeoutError 的子类，`except TimeoutError` 也接得住。"""

    def __init__(self, path, timeout):
        self.path = path
        self.timeout = timeout
        super().__init__("等锁超时：%s（%.2f 秒）" % (path, timeout))


class ReadTimeout(LockTimeout):
    """读超时：目标文件一直被别人的 os.replace 占着，退避预算用尽。

    故意挂在 LockTimeout 下面 —— 读写两条路超时，`except 锁.LockTimeout` 一次接住；
    但它不是「等锁」，所以文案单独写。
    """

    def __init__(self, path, timeout):
        TimeoutError.__init__(self, "读超时：%s（%.2f 秒）" % (path, timeout))
        self.path = path
        self.timeout = timeout


def _retryable(exc):
    """这个 OSError 是不是「文件正被别人占着」——只有这几种才退避重试。

    IsADirectoryError（EISDIR）、ENOENT、ENOSPC 之类都是真错误，立刻抛。
    """
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) in RETRY_WINERRORS:
        return True
    return getattr(exc, "errno", None) in RETRY_ERRNOS


def lock_path(path):
    """锁文件跟目标同目录同名：`长期\\\\事实库.md` → `长期\\\\事实库.md.lock`。"""
    return os.path.abspath(os.fspath(path)) + LOCK_SUFFIX


def _tmp_name(directory):
    return os.path.join(directory, "%s%d-%08x" % (TMP_PREFIX, os.getpid(),
                                                  random.getrandbits(32)))


def _replace(tmp, path, timeout=REPLACE_TIMEOUT):
    """os.replace 的 Windows 加固版：目标被占用时短退避重试到 timeout。

    Windows 上目标文件只要被另一个进程以「不带 FILE_SHARE_DELETE」的方式开着
    （Python 的 open() / json.load() / 记事本都是），MoveFileEx 会直接
    `PermissionError: [WinError 5] 拒绝访问`。读的人通常只开一瞬，退避一下就好；
    POSIX 上 os.replace 本来就原子且不怕并发读，第一次就过。
    非「被占用」类错误立刻抛，不吞。
    """
    deadline = time.monotonic() + float(timeout)
    wait = BACKOFF_MIN
    while True:
        try:
            os.replace(tmp, path)
            return
        except OSError as e:
            if not _retryable(e):
                raise
            if os.path.isdir(path):                  # 目标是目录：等多久都变不成文件
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(wait)                         # 不忙等，让持有人把句柄关掉
            wait = min(BACKOFF_MAX, wait * 1.5)


def atomic_write(path, data, encoding="utf-8", timeout=REPLACE_TIMEOUT):
    """整篇原子替换。data 是 str。返回写入的字节数（按编码后算）。

    timeout 是 os.replace 撞上「目标被占用」时的退避总预算（默认 REPLACE_TIMEOUT）。
    """
    path = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = _tmp_name(directory or ".")
    fd = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)          # Windows 别做换行翻译
        fd = os.open(tmp, flags, 0o644)
        with io.open(fd, "w", encoding=encoding, newline="") as f:
            fd = None                                # 交给 file 对象关
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path, timeout=timeout)         # 同一分区内是原子的（带退避）
        tmp = None
    finally:
        if fd is not None:                           # io.open 之前就炸了
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None and os.path.exists(tmp):  # 半成品清掉，别留垃圾
            try:
                os.remove(tmp)
            except OSError:
                pass


def _open_lock(path):
    """打开（必要时造）锁文件。Windows 下必须至少 1 字节才锁得住。"""
    lp = lock_path(path)
    parent = os.path.dirname(lp)
    if parent:
        os.makedirs(parent, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(lp, flags, 0o644)
    if os.name == "nt" and os.fstat(fd).st_size == 0:
        os.write(fd, b"\n")                          # 占位一个字节，不是「占位锁」
        os.fsync(fd)
    return lp, fd


def _try_lock(fd):
    """试一次非阻塞加锁。拿到 True，拿不到 False。

    Windows 用 LK_NBLCK 而不是 LK_LOCK：LK_LOCK 自带「1 秒 × 10 次」的阻塞重试，
    会把 timeout 顶穿（timeout=0.3 也要等满 10 秒）。轮询留给我们自己做，
    锁本身还是 msvcrt 的系统级字节锁。
    """
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)             # 锁当前文件位置起的 1 字节
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd):
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def locked(path, timeout=DEFAULT_TIMEOUT):
    """持锁。with 块结束（含异常）解锁并关 fd。超时抛 LockTimeout。"""
    lp, fd = _open_lock(path)
    try:
        deadline = time.monotonic() + float(timeout)
        wait = POLL_MIN
        while True:
            if _try_lock(fd):
                break
            if time.monotonic() >= deadline:
                raise LockTimeout(lp, timeout)
            time.sleep(wait)
            wait = min(POLL_MAX, wait * 1.6)
        try:
            yield lp
        finally:
            _unlock(fd)
    finally:
        try:
            os.close(fd)                             # 进程崩了内核也会关，锁随之释放
        except OSError:
            pass


class _Doc(object):
    """rewriting 交给调用方的那块纸。只认 .text，赋过值才算改过。"""

    __slots__ = ("_text", "dirty")

    def __init__(self, text=""):
        self._text = text
        self.dirty = False

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = str(value)
        self.dirty = True

    def lines(self):
        """按行切（保留行尾），跟 io.open(newline="") 的 readlines() 一个形状。"""
        return self._text.splitlines(True)

    def __len__(self):
        return len(self._text)


def read_bytes(path, timeout=DEFAULT_TIMEOUT, encoding="utf-8", errors=None):
    """读全文（带退避重试），返回解码后的 str。

    写的人正好在 os.replace 那一瞬，Windows 会把目标短暂锁成 EACCES ——
    那不是半截数据，是「正在换」，`time.sleep` 一下再读就好。超过 timeout 抛
    ReadTimeout；文件不存在抛 FileNotFoundError（缺文件由调用方自己决定怎么算）。
    """
    path = os.path.abspath(os.fspath(path))
    deadline = time.monotonic() + float(timeout)
    wait = BACKOFF_MIN
    while True:
        try:
            with io.open(path, "r", encoding=encoding, errors=errors,
                         newline="") as f:
                return f.read()
        except OSError as e:
            if not _retryable(e):
                raise                                # 不存在 / 是目录 / 权限真不对
            if time.monotonic() >= deadline:
                raise ReadTimeout(path, timeout)
            time.sleep(wait)
            wait = min(BACKOFF_MAX, wait * 1.5)


def read_json(path, default=None, timeout=DEFAULT_TIMEOUT, encoding="utf-8"):
    """读一份会被并发替换的 JSON：不存在 → default；坏 JSON → default + WARN。

    坏 JSON 不抛 —— 记忆权重 / 账本这类文件，读到半截就抛会把整条检索链带崩；
    按插件层的风格打一条 WARN 到 stderr，调用方拿默认值继续跑。
    """
    try:
        text = read_bytes(path, timeout=timeout, encoding=encoding, errors="replace")
    except FileNotFoundError:
        return default
    except (OSError, TimeoutError) as e:             # 超时 / 真读不动
        print("[WARN] %s: 读不出来（%s），按默认值处理" % (path, e), file=sys.stderr)
        return default
    if not text.strip():
        return default
    try:
        return json.loads(text.replace("\ufeff", ""))
    except ValueError as e:
        print("[WARN] %s: JSON 坏了（%s），按默认值处理" % (path, e), file=sys.stderr)
        return default


def _read_text(path, encoding):
    try:
        with io.open(path, "r", encoding=encoding, newline="") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


@contextlib.contextmanager
def rewriting(path, timeout=DEFAULT_TIMEOUT, encoding="utf-8"):
    """持锁 → 读全文 → yield doc → 如改过则原子写回。

        with 锁.rewriting(p) as doc:
            doc.text = doc.text + "- 新的一条\\n"
    """
    path = os.path.abspath(os.fspath(path))
    with locked(path, timeout=timeout):
        doc = _Doc(_read_text(path, encoding))
        yield doc
        if doc.dirty:
            atomic_write(path, doc.text, encoding=encoding)
