"""
Cross-platform resource guardrails for the pipeline.

Mirrors the GitHub free-tier runner envelope (RAM / ephemeral storage / runtime)
so the SAME limits are enforced when you run the script locally as on CI. Peak
RSS includes DuckDB because DuckDB runs in-process.

Thresholds (all overridable via environment variables; defaults target the
public-repo free-tier runner: 4 vCPU / 16 GB RAM / 14 GB SSD):

  RUNNER_RAM_MB        target RAM envelope to enforce        (default 16384)
  RAM_HEADROOM_MB      RAM left for OS + Python outside it    (default 1024)
  DISK_HEADROOM_GB     never let free disk drop below this    (default 2)
  MIN_START_DISK_GB    fail fast if the volume can't hold it  (default 40)
  MAX_RUNTIME_MIN      soft wall-clock ceiling                (default 300)
  PIPELINE_GUARDRAILS  set to off/0/false to measure-only     (default on)

The effective RAM ceiling is min(target, actual machine RAM) - headroom. For a
private 7 GB runner instead, set RUNNER_RAM_MB=7168 (and lower the DuckDB budget
via PIPELINE_MEMORY_LIMIT / PIPELINE_THREADS in main.py to match).

psutil is used when available (required for RAM measurement on Windows); on
Linux/macOS the stdlib `resource` module supplies the true peak as a fallback.
"""

import os
import time
import shutil
import platform
import threading
import logging

logger = logging.getLogger("pipeline_engine")

try:
    import psutil  # cross-platform; needed for RAM stats on Windows
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False

_GIB = 1024 ** 3
_MIB = 1024 ** 2


class GuardrailError(RuntimeError):
    """Raised when a resource limit is breached and enforcement is enabled."""


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def total_ram_mb():
    """Physical RAM of this machine in MB, or 0 if it can't be determined."""
    if _HAS_PSUTIL:
        try:
            return int(psutil.virtual_memory().total / _MIB)
        except Exception:
            pass
    try:  # POSIX fallback
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / _MIB)
    except (ValueError, AttributeError, OSError):
        return 0


def _current_rss_bytes(proc):
    if proc is not None:
        try:
            return proc.memory_info().rss
        except Exception:
            return 0
    return 0


def _os_peak_rss_bytes():
    """True peak RSS as reported by the OS, where available (sampling-independent)."""
    if platform.system() == "Windows":
        if _HAS_PSUTIL:
            try:
                return int(getattr(psutil.Process().memory_info(), "peak_wset", 0) or 0)
            except Exception:
                return 0
        return 0
    try:  # Unix: ru_maxrss is the peak (KB on Linux, bytes on macOS)
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return ru if platform.system() == "Darwin" else ru * 1024
    except Exception:
        return 0


def ram_measurable():
    """True if we can measure peak RSS on this platform/install."""
    return _HAS_PSUTIL or _os_peak_rss_bytes() > 0


class GuardrailConfig:
    def __init__(self):
        self.runner_ram_mb = _env_int("RUNNER_RAM_MB", 16384)
        # Headroom covers the OS, the CI runner agent, Python/DuckDB overhead, and
        # the kernel page cache for the on-disk engine file. 4 GB on a 16 GB box:
        # a ~12.5 GB peak starved the runner ("lost communication"), so the safe
        # process-RSS ceiling is ~12 GB, not 15 GB.
        self.ram_headroom_mb = _env_int("RAM_HEADROOM_MB", 4096)
        # Watchdog: self-terminate if RSS climbs to within this margin of PHYSICAL
        # RAM, so the process dies cleanly (exit 137 + a log line) instead of
        # starving the host and showing as "runner lost communication".
        self.abort_headroom_mb = _env_int("RAM_ABORT_HEADROOM_MB", 2048)
        self.disk_headroom_gb = _env_int("DISK_HEADROOM_GB", 2)
        self.min_start_disk_gb = _env_int("MIN_START_DISK_GB", 40)
        self.max_runtime_min = _env_int("MAX_RUNTIME_MIN", 300)
        self.enforce = os.environ.get("PIPELINE_GUARDRAILS", "on").strip().lower() \
            not in ("0", "off", "false", "no")

    def max_rss_mb(self):
        envelope = self.runner_ram_mb
        actual = total_ram_mb()
        if actual and actual < envelope:
            envelope = actual
        return max(256, envelope - self.ram_headroom_mb)

    def hard_abort_mb(self):
        """RSS at which the watchdog self-terminates (physical RAM - abort headroom).

        Returns 0 (disabled) if physical RAM can't be determined. This is based on
        ACTUAL machine RAM, not the target envelope, so it only fires when the real
        host is about to be starved -- never on a roomy dev machine.
        """
        actual = total_ram_mb()
        if not actual:
            return 0
        return max(0, actual - self.abort_headroom_mb)


class GuardrailMonitor:
    """Background sampler tracking peak RSS and minimum free disk.

    If hard_abort_mb is set, self-terminates (os._exit 137) the moment RSS exceeds
    it -- a best-effort backstop that turns host starvation into a clean, logged
    failure instead of a silent "runner lost communication".
    """

    def __init__(self, work_dir=None, interval=3.0, hard_abort_mb=0):
        self.work_dir = work_dir or os.getcwd()
        self.interval = interval
        self.hard_abort_mb = hard_abort_mb or 0
        self._proc = psutil.Process() if _HAS_PSUTIL else None
        self.peak_rss = 0
        self.min_free = None
        self.samples = 0
        self._stop = threading.Event()
        self._thread = None

    def _sample(self):
        rss = _current_rss_bytes(self._proc)
        if rss > self.peak_rss:
            self.peak_rss = rss
        if self.hard_abort_mb and rss and (rss / _MIB) > self.hard_abort_mb:
            logger.critical(
                f"[GUARDRAIL] HARD ABORT: process RSS {int(rss / _MIB)}MB exceeded "
                f"{self.hard_abort_mb}MB (physical RAM minus safety margin). "
                f"Self-terminating to avoid starving the host (which would otherwise "
                f"appear as 'runner lost communication'). Lower PIPELINE_MEMORY_LIMIT "
                f"or PIPELINE_THREADS."
            )
            os._exit(137)
        try:
            free = shutil.disk_usage(self.work_dir).free
            if self.min_free is None or free < self.min_free:
                self.min_free = free
        except Exception:
            pass
        self.samples += 1

    def _run(self):
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval)

    def start(self):
        self._sample()  # baseline
        self._thread = threading.Thread(target=self._run, name="guardrail-monitor", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 1.0)
        # Fold in the OS-reported true peak (independent of sample timing).
        os_peak = _os_peak_rss_bytes()
        if os_peak > self.peak_rss:
            self.peak_rss = os_peak
        self._sample()


def preflight_disk_check(cfg, work_dir=None):
    """Fail fast (before the ~10-min ingest) if the volume can't hold the dataset."""
    work_dir = work_dir or os.getcwd()
    free_gb = shutil.disk_usage(work_dir).free / _GIB
    logger.info(f"[GUARDRAIL] work volume free at start: {free_gb:.1f} GB "
                f"(need >= {cfg.min_start_disk_gb} GB)")
    if free_gb < cfg.min_start_disk_gb:
        msg = (f"Only {free_gb:.1f} GB free on '{work_dir}'; the dataset peak needs "
               f">= {cfg.min_start_disk_gb} GB. Free space or lower MIN_START_DISK_GB.")
        if cfg.enforce:
            raise GuardrailError(msg)
        logger.warning(f"[GUARDRAIL] {msg}")


def report_and_enforce(cfg, monitor, total_secs, enforce=True):
    """Log the guardrail report; raise GuardrailError on breach when enforcing."""
    peak_rss_mb = int(monitor.peak_rss / _MIB)
    max_rss_mb = cfg.max_rss_mb()
    min_free_gb = (monitor.min_free / _GIB) if monitor.min_free is not None else None
    total_min = total_secs / 60.0
    actual_ram = total_ram_mb()

    logger.info("=" * 60)
    logger.info("GUARDRAIL REPORT (free-tier runner envelope)")
    logger.info("-" * 60)
    logger.info(f"  detected RAM        {actual_ram or '?'} MB")
    if peak_rss_mb > 0:
        logger.info(f"  peak RSS            {peak_rss_mb} MB   (ceiling {max_rss_mb} MB)")
    else:
        logger.info(f"  peak RSS            unavailable (install psutil)   (ceiling {max_rss_mb} MB)")
    if min_free_gb is not None:
        logger.info(f"  min free disk       {min_free_gb:.2f} GB  (floor {cfg.disk_headroom_gb} GB)")
    logger.info(f"  total runtime       {total_min:.1f} min  (ceiling {cfg.max_runtime_min} min)")
    logger.info(f"  samples / psutil    {monitor.samples} / {_HAS_PSUTIL}")
    logger.info("=" * 60)

    breaches = []
    if peak_rss_mb > 0 and peak_rss_mb > max_rss_mb:
        breaches.append(f"peak RSS {peak_rss_mb}MB > ceiling {max_rss_mb}MB")
    if min_free_gb is not None and min_free_gb < cfg.disk_headroom_gb:
        breaches.append(f"min free disk {min_free_gb:.2f}GB < floor {cfg.disk_headroom_gb}GB")
    if total_min > cfg.max_runtime_min:
        breaches.append(f"runtime {total_min:.1f}min > ceiling {cfg.max_runtime_min}min")

    if peak_rss_mb == 0 and not ram_measurable():
        logger.warning("[GUARDRAIL] RAM not measured on this platform; "
                       "`pip install psutil` to enforce the RAM ceiling.")

    if not breaches:
        logger.info("[GUARDRAIL] all measured limits within the free-tier envelope.")
        return

    for b in breaches:
        logger.critical(f"[GUARDRAIL] BREACH: {b}")
    if enforce and cfg.enforce:
        raise GuardrailError("guardrail breach: " + "; ".join(breaches))
