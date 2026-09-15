#!/usr/bin/env python3
"""
Runtime stats for demoneye (--stats).

Prints a single refreshing line with FPS, CPU, GPU and temperature so you can
confirm the Pi is holding framerate with the heavier shaders.

GPU load isn't exposed the same way everywhere, so the source is probed once at
startup and reported honestly:
  * Pi 4/5 (V3D)  -> /sys/kernel/debug/dri/*/gpu_usage  (needs read access)
  * Pi (VideoCore) -> vcgencmd get_config / measure_clock v3d as a proxy
  * elsewhere      -> shown as "n/a" rather than a made-up number

Standalone check of what's available here:
    python stats.py
"""

import glob
import os
import shutil
import subprocess
import time


class Stats:
    def __init__(self, interval=1.0):
        self.interval = interval
        self.frames = 0
        self.t0 = time.monotonic()
        self.last = self.t0
        self.fps = 0.0
        self.fps_min = None
        self.worst_frame_ms = 0.0
        self._frame_mark = time.monotonic()

        # --- CPU: prefer psutil, else parse /proc/stat ---
        try:
            import psutil
            self._psutil = psutil
            psutil.cpu_percent(None)       # prime it
        except ImportError:
            self._psutil = None
        self._prev_cpu = self._read_proc_stat()

        # --- GPU source probe ---
        self.gpu_src = None
        self._v3d_path = None
        for p in glob.glob("/sys/kernel/debug/dri/*/gpu_usage"):
            try:
                with open(p) as f:
                    f.read()
                self._v3d_path = p
                self.gpu_src = "v3d"
                break
            except (PermissionError, OSError):
                continue
        if self.gpu_src is None and shutil.which("vcgencmd"):
            try:
                r = subprocess.run(["vcgencmd", "measure_clock", "v3d"],
                                   capture_output=True, text=True, timeout=2)
                if r.returncode == 0 and "=" in r.stdout:
                    self.gpu_src = "vcgencmd"
            except Exception:
                pass
        self._prev_v3d = self._read_v3d()

    # -- CPU --
    @staticmethod
    def _read_proc_stat():
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()[1:]
            vals = [int(v) for v in parts]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return sum(vals), idle
        except Exception:
            return None

    def cpu_percent(self):
        if self._psutil:
            return self._psutil.cpu_percent(None)
        cur = self._read_proc_stat()
        if not cur or not self._prev_cpu:
            self._prev_cpu = cur
            return None
        dt_total = cur[0] - self._prev_cpu[0]
        dt_idle = cur[1] - self._prev_cpu[1]
        self._prev_cpu = cur
        if dt_total <= 0:
            return None
        return 100.0 * (1.0 - dt_idle / dt_total)

    # -- GPU --
    def _read_v3d(self):
        if self.gpu_src != "v3d" or not self._v3d_path:
            return None
        try:
            with open(self._v3d_path) as f:
                txt = f.read()
            # Lines look like "RENDER; 9876543 ns; 45.30%" (or "render ... 31%").
            # Report the BUSIEST queue — for a fragment-shader workload that's
            # RENDER, and taking the first row would under-report badly.
            best = None
            for line in txt.splitlines():
                for tok in line.replace(";", " ").split():
                    if tok.endswith("%"):
                        try:
                            v = float(tok.rstrip("%"))
                        except ValueError:
                            continue
                        if best is None or v > best:
                            best = v
            return best
        except Exception:
            return None

    def gpu_percent(self):
        if self.gpu_src == "v3d":
            return self._read_v3d()
        return None

    def gpu_clock_mhz(self):
        if self.gpu_src != "vcgencmd":
            return None
        try:
            r = subprocess.run(["vcgencmd", "measure_clock", "v3d"],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0 and "=" in r.stdout:
                return int(r.stdout.strip().split("=")[1]) / 1e6
        except Exception:
            pass
        return None

    # -- temperature --
    def temp_c(self):
        for p in ("/sys/class/thermal/thermal_zone0/temp",):
            try:
                with open(p) as f:
                    return int(f.read().strip()) / 1000.0
            except Exception:
                pass
        if shutil.which("vcgencmd"):
            try:
                r = subprocess.run(["vcgencmd", "measure_temp"],
                                   capture_output=True, text=True, timeout=2)
                if "=" in r.stdout:
                    return float(r.stdout.split("=")[1].split("'")[0])
            except Exception:
                pass
        return None

    # -- per-frame hook --
    def frame(self):
        """Call once per rendered frame. Returns a status line when due, else None."""
        now = time.monotonic()
        ms = (now - self._frame_mark) * 1000.0
        self._frame_mark = now
        if ms > self.worst_frame_ms and self.frames > 5:
            self.worst_frame_ms = ms
        self.frames += 1
        if now - self.last < self.interval:
            return None

        self.fps = self.frames / (now - self.last)
        if self.fps_min is None or self.fps < self.fps_min:
            self.fps_min = self.fps
        self.frames = 0
        self.last = now

        cpu = self.cpu_percent()
        gpu = self.gpu_percent()
        clk = self.gpu_clock_mhz()
        tmp = self.temp_c()

        parts = [f"fps {self.fps:5.1f}"]
        parts.append(f"min {self.fps_min:5.1f}" if self.fps_min else "min   n/a")
        parts.append(f"worst {self.worst_frame_ms:5.1f}ms")
        parts.append(f"cpu {cpu:5.1f}%" if cpu is not None else "cpu   n/a")
        if gpu is not None:
            parts.append(f"gpu {gpu:5.1f}%")
        elif clk is not None:
            parts.append(f"v3d {clk:4.0f}MHz")
        else:
            parts.append("gpu   n/a")
        if tmp is not None:
            parts.append(f"{tmp:4.1f}C")
        return "  ".join(parts)

    def describe(self):
        src = {"v3d": "/sys debugfs gpu_usage",
               "vcgencmd": "vcgencmd v3d clock (proxy)"}.get(self.gpu_src)
        cpu_src = "psutil" if self._psutil else ("/proc/stat" if self._prev_cpu else "unavailable")
        return (f"[stats] cpu via {cpu_src}; gpu via {src or 'no source found (shown as n/a)'}"
                + ("" if self.gpu_src != "v3d"
                   else ""))


if __name__ == "__main__":
    s = Stats(interval=1.0)
    print(s.describe())
    print("sampling for 5s...")
    t_end = time.time() + 5
    while time.time() < t_end:
        line = s.frame()
        if line:
            print(line)
        time.sleep(1 / 60)
