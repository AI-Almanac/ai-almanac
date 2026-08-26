"""Detect network filesystems under a given path.

Used by `ai-almanac serve` to warn (or refuse) when the data dir is on a
network FS: SQLite on NFS/CIFS is prone to corruption under concurrent writers;
a network data dir with Postgres is merely slow.

The detector is best-effort — any exception (missing /proc, unexpected OS, etc.)
returns None so startup is never blocked by the detector itself.
"""

from __future__ import annotations

from pathlib import Path

_NETWORK_FS_TYPES: frozenset[str] = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb2",
        "9p",
        "fuse.sshfs",
        "glusterfs",
        "lustre",
        "ceph",
        "afpfs",
        "webdav",
        "davfs",
    }
)


def _match_mounts(mounts_text: str, path: Path) -> str | None:
    """Parse Linux /proc/self/mounts; return fstype of the longest matching mount.

    Factored out for unit testing with fixture mount text.
    """
    resolved = path.resolve()
    best_len = -1
    best_fstype: str | None = None
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point_raw, fstype = parts[1], parts[2]
        try:
            mp = Path(mount_point_raw)
            if resolved == mp or resolved.is_relative_to(mp):
                mp_len = len(str(mp))
                if mp_len > best_len:
                    best_len = mp_len
                    best_fstype = fstype
        except Exception:  # noqa: BLE001
            continue
    return best_fstype


def network_fs_type(path: Path) -> str | None:
    """Return the network filesystem type under `path`, or None if local/unknown.

    None means "not detected as network FS" — either it is local, the platform
    is unsupported, or the detector hit an exception.
    """
    try:
        import platform

        system = platform.system()
        if system == "Linux":
            mounts_text = Path("/proc/self/mounts").read_text()
            fstype = _match_mounts(mounts_text, path)
            if fstype and (fstype in _NETWORK_FS_TYPES or fstype.startswith("nfs")):
                return fstype
            return None
        if system == "Darwin":
            return _network_fs_type_darwin(path)
    except Exception:  # noqa: BLE001
        pass
    return None


def _network_fs_type_darwin(path: Path) -> str | None:
    """macOS: use ctypes statfs(2) to read f_fstypename."""
    try:
        import ctypes
        import ctypes.util

        libc_name = ctypes.util.find_library("c")
        if not libc_name:
            return None
        libc = ctypes.CDLL(libc_name, use_errno=True)

        # struct statfs on macOS (Darwin) — f_fstypename is at offset 0,
        # 16 bytes. We only need the first field; declare a minimal struct.
        class _Statfs(ctypes.Structure):
            _fields_ = [
                ("f_bsize", ctypes.c_uint32),
                ("f_iosize", ctypes.c_int32),
                ("f_blocks", ctypes.c_uint64),
                ("f_bfree", ctypes.c_uint64),
                ("f_bavail", ctypes.c_uint64),
                ("f_files", ctypes.c_uint64),
                ("f_ffree", ctypes.c_uint64),
                ("f_fsid_val", ctypes.c_int32 * 2),
                ("f_owner", ctypes.c_uint32),
                ("f_type", ctypes.c_uint32),
                ("f_flags", ctypes.c_uint64),
                ("f_fssubtype", ctypes.c_uint64),
                ("f_fstypename", ctypes.c_char * 16),
                ("f_mntonname", ctypes.c_char * 1024),
                ("f_mntfromname", ctypes.c_char * 1024),
                ("f_reserved", ctypes.c_uint32 * 8),
            ]

        buf = _Statfs()
        ret = libc.statfs(str(path.resolve()).encode(), ctypes.byref(buf))
        if ret != 0:
            return None
        fstype = buf.f_fstypename.decode("ascii", errors="replace").rstrip("\x00")
        if fstype in _NETWORK_FS_TYPES or fstype.startswith("nfs"):
            return fstype
    except Exception:  # noqa: BLE001
        pass
    return None
