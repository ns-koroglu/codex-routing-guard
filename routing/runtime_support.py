"""Small standard-library OS boundary shared by installer, ledger and recovery."""
import base64
import contextlib
import errno
import os
from pathlib import Path
import shlex
import stat
import tempfile
import time


def is_link(path):
    """Reject Windows reparse points too, including Python versions before 3.12."""
    path = Path(path)
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
    except FileNotFoundError:
        return False


def private_permissions(path, mode=0o600):
    """POSIX modes or a protected Windows DACL for owner, SYSTEM and admins."""
    if os.name != 'nt':
        os.chmod(path, mode)
        return
    flags = 'OICI' if Path(path).is_dir() else ''
    sddl = 'D:P' + ''.join('(A;' + flags + ';FA;;;' + sid + ')' for sid in ('OW', 'SY', 'BA'))
    windows_dacl(path, sddl)


def windows_dacl(path, value=None):
    """Read or restore a Windows DACL for installation/rollback comparisons."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    api.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    api.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    api.SetNamedSecurityInfoW.restype = wintypes.DWORD
    api.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p)]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if value is None:
        code = api.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        rendered = ctypes.c_void_p()
        try:
            if not api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(rendered), None):
                raise ctypes.WinError(ctypes.get_last_error())
            text = ctypes.wstring_at(rendered)
            header, separator, entries = text.partition('(')
            # Windows may add the auto-inherited bookkeeping flag on restore;
            # compare protection and actual ACEs, not this OS-generated marker.
            return header.replace('AI', '') + separator + entries
        finally:
            kernel.LocalFree(rendered)
            kernel.LocalFree(descriptor)
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(value, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        protected = 0x80000000 if value.startswith('D:P') else 0x20000000
        code = api.SetNamedSecurityInfoW(str(path), 1, protected | 4, None, None, acl, None)
        if code:
            raise ctypes.WinError(code)
    finally:
        kernel.LocalFree(descriptor)


@contextlib.contextmanager
def file_lock(path, timeout=30):
    """Exclusive process lock, released by the OS on exit (including a crash)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        private_permissions(path)
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
            def acquire():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(stream, fcntl.LOCK_UN)
        end = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= end:
                    raise TimeoutError('Lock is held by another process: ' + str(path)) from error
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def atomic_write(path, data, mode=0o600):
    """Private, durable temporary file; never leave a partial JSON document."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            name = stream.name
            private_permissions(name, mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Antivirus/indexers can briefly hold a destination on Windows.
        for attempt in range(6):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if os.name != 'nt' or attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def shell_command(arguments, for_display=False):
    """Render local hook/checkpoint commands without interpolating path contents."""
    arguments = [str(value) for value in arguments]
    if os.name != 'nt':
        return shlex.join(arguments)
    script = '& ' + ' '.join("'" + value.replace("'", "''") + "'" for value in arguments)
    if for_display:
        return script  # PowerShell, so operator flags can be appended visibly.
    script += '; exit $LASTEXITCODE'
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ' + encoded
