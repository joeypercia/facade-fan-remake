"""Run Façade for automated tests: hidden, unelevated, then stopped by process ID.

    python engine/autopilot/launch.py --seconds 90 [--visible] [--script path]

- Runs on a private desktop ("FacadeTest"), so no window appears on yours (--visible to watch).
- __COMPAT_LAYER=RunAsInvoker skips the administrator prompt; the game's few writes (stageplays)
  go to Windows' per-user VirtualStore instead.
- FACADE_AUTOPILOT tells the bridge to drive the session from a script (see autopilot.txt).
- Afterwards the engine and its Java child are terminated by PID: nothing else is touched.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import time
from pathlib import Path

# A private copy of the game for tests (made with robocopy from the real install), so test builds
# of the bridge can be swapped in without administrator rights or touching the real install.
FACADE = Path(os.environ.get("FACADE_DIR", Path(os.environ["LOCALAPPDATA"]) / "FacadeFanRemake" / "facade-test"))
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


def children(pid):
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    e = PROCESSENTRY32()
    e.dwSize = ctypes.sizeof(e)
    out = []
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        if e.th32ParentProcessID == pid:
            out.append((e.th32ProcessID, e.szExeFile.decode(errors="replace")))
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def kill(pid):
    k32.OpenProcess.restype = wt.HANDLE
    h = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if h:
        k32.TerminateProcess(h, 0)
        k32.CloseHandle(h)
    return bool(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--visible", action="store_true", help="run on your desktop instead of a hidden one")
    ap.add_argument("--script", type=Path, help="autopilot script (default: engine/autopilot/autopilot.txt)")
    args = ap.parse_args()

    workdir = FACADE / "util" / "sources" / "facade"
    # Our own starter: the original is forced to run elevated (a UAC prompt per launch).
    exe = Path(__file__).with_name("out") / "facade_test_starter.exe"
    if not exe.exists():
        raise SystemExit("build the test starter first: engine/autopilot/build.bat")
    # Windows reads the compatibility layer from the *launching* process's environment.
    # Not WinXPSP3: that layer is what demands elevation (error 740).
    os.environ["__COMPAT_LAYER"] = "RunAsInvoker"
    env = dict(os.environ)
    script = args.script or Path(__file__).with_name("autopilot.txt")
    env["FACADE_AUTOPILOT"] = str(script.resolve())
    block = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"

    si = STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    if not args.visible:
        u32.CreateDesktopW.restype = wt.HANDLE
        desk = u32.CreateDesktopW("FacadeTest", None, None, 0, 0x10000000, None)  # GENERIC_ALL
        if not desk:
            raise SystemExit(f"CreateDesktop failed: {ctypes.get_last_error()}")
        si.lpDesktop = "FacadeTest"
    pi = PROCESS_INFORMATION()
    cmdline = ctypes.create_unicode_buffer(f'"{exe}" "{workdir}"')
    ok = k32.CreateProcessW(str(exe), cmdline, None, None, False, 0x00000400,  # CREATE_UNICODE_ENVIRONMENT
                            ctypes.create_unicode_buffer(block), str(workdir), ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise SystemExit(f"CreateProcess failed: {ctypes.get_last_error()}")
    print(f"started engine pid {pi.dwProcessId} ({'visible' if args.visible else 'hidden desktop'})", flush=True)
    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            if k32.WaitForSingleObject(pi.hProcess, 1000) == 0:
                code = wt.DWORD()
                k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
                print(f"engine exited on its own after {time.time() - t0:.0f}s (code {code.value:#x})")
                break
    finally:
        kids = children(pi.dwProcessId)
        for cpid, name in kids:
            print(f"stopping child {name} pid {cpid}: {kill(cpid)}")
        print(f"stopping engine pid {pi.dwProcessId}: {kill(pi.dwProcessId)}")


if __name__ == "__main__":
    main()
