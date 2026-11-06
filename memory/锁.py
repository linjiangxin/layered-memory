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
import io
import os
import random
import time

if os.name == "nt":                                  # pragma: no cover - 平台分支
    import msvcrt
else:                                                 # pragma: no cover
    import fcntl

__all__ = ["LockTimeout", "atomic_write", "locked", "rewriting", "lock_path",
           "DEFAULT_TIMEOUT", "TMP_PREFIX"]

DEFAULT_TIMEOUT = 5.0     # 秒
TMP_PREFIX = ".tmp-"
LOCK_SUFFIX = ".lock"
POLL_MIN, POLL_MAX = 0.002, 0.05


class LockTimeout(TimeoutError):
    """等锁超时。TimeoutError 的子类，`except TimeoutError` 也接得住。"""

    def __init__(self, path, timeout):
        self.path = path
        self.timeout = timeout
        super().__init__("等锁超时：%s（%.2f 秒）" % (path, timeout))


def lock_path(path):
    """锁文件跟目标同目录同名：`长期\\\\事实库.md` → `长期\\\\事实库.md.lock`。"""
    return os.path.abspath(os.fspath(path)) + LOCK_SUFFIX


def _tmp_name(directory):
    return os.path.join(directory, "%s%d-%08x" % (TMP_PREFIX, os.getpid(),
                                                  random.getrandbits(32)))


def atomic_write(path, data, encoding="utf-8"):
    """整篇原子替换。data 是 str。返回写入的字节数（按编码后算）。"""
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
        os.replace(tmp, path)                        # 同一分区内是原子的
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
