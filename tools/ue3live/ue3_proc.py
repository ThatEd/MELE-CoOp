"""Shared read-only process primitives for every UE3 tool in this directory.

These two helpers started life inside `le2_census.py`, which meant the generic
`ue3_*` tools had to import a module named after one game - confusing, and it made
the tooling look LE2-specific when it is not. They live here now; `le2_census`
re-exports them so the earlier scripts keep working unchanged.

Hard rule, and the reason this module opens the process the way it does: everything
here is READ-ONLY. `OpenProcess` asks for VM_READ and QUERY_INFORMATION only, so no
write, no allocation in the target and no remote thread is possible through these
handles even by accident. A wrong read costs nothing; a wrong write crashes the game.
"""
import ctypes
import struct

from ctypes import wintypes

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400


class Reader:
    """ReadProcessMemory wrapper over a read-only handle to a live process."""

    def __init__(self, pid):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.ReadProcessMemory.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k32.ReadProcessMemory.restype = wintypes.BOOL
        k32.VirtualQueryEx.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        k32.VirtualQueryEx.restype = ctypes.c_size_t
        self.k32 = k32
        self.handle = k32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")

    def read(self, address, size):
        """Bytes at address, or None. None means unreadable, never "zero"."""
        if not address:
            return None
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = self.k32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got))
        if not ok or got.value != size:
            return None
        return buf.raw

    def ptr(self, address):
        raw = self.read(address, 8)
        return struct.unpack("<Q", raw)[0] if raw else 0

    def u32(self, address):
        raw = self.read(address, 4)
        return struct.unpack("<I", raw)[0] if raw else 0

    def chunks(self, address, size, chunk=1 << 20):
        """Yield (offset, bytes) readable runs, zero-filling holes.

        Scanning a large region with one big ReadProcessMemory fails entirely if any
        page in it is unmapped, which silently loses the whole block. Chunking, and
        falling back to page-sized reads, keeps the readable parts.
        """
        done = 0
        while done < size:
            n = min(chunk, size - done)
            raw = self.read(address + done, n)
            if raw is None:
                step = 0x1000
                piece = bytearray()
                for off in range(0, n, step):
                    want = min(step, n - off)
                    sub = self.read(address + done + off, want)
                    piece.extend(sub if sub else b"\x00" * want)
                raw = bytes(piece)
            yield done, raw
            done += n


class ME(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
                ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
                ("szModule", wintypes.WCHAR * 256), ("szExePath", wintypes.WCHAR * 260)]


def module_base(pid, name):
    """Base address of a loaded module, or None.

    Module32First/Next must be the W variants with MODULEENTRY32W; the ANSI ones
    truncate paths and mis-read the name.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
    if snap == wintypes.HANDLE(-1).value:
        return None
    k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ME)]
    k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ME)]
    try:
        entry = ME()
        entry.dwSize = ctypes.sizeof(ME)
        ok = k32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szModule.lower() == name.lower():
                return int(entry.modBaseAddr)
            ok = k32.Module32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return None


def module_info(pid, name):
    """(base, size, path) for a loaded module, or None."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
    if snap == wintypes.HANDLE(-1).value:
        return None
    k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ME)]
    k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ME)]
    try:
        entry = ME()
        entry.dwSize = ctypes.sizeof(ME)
        ok = k32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szModule.lower() == name.lower():
                return int(entry.modBaseAddr), int(entry.modBaseSize), entry.szExePath
            ok = k32.Module32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return None
