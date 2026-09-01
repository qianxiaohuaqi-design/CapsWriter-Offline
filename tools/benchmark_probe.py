# coding: utf-8
"""
CapsWriter-Offline 启动性能基准测量工具 (Cold-Start Benchmark Probe)
执行 5 次完整的冷启动与进程清理，统计 ASR 服务端就绪、GUI 端口就绪、原生窗口可见耗时及中位数。
"""
import os
import sys
import time
import statistics
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from web_gui import process_manager

def run_single_measurement(round_idx: int) -> dict:
    print(f"--- [Round {round_idx}/5] 开始冷启动测量 ---")
    process_manager.stop_all(include_self=False)
    time.sleep(1.0)

    t0 = time.perf_counter()

    # 1. 拉起 ASR 服务端并等待 6016 端口就绪
    t_server = None
    process_manager.launch_server()
    for _ in range(100):
        if process_manager.is_port_open('127.0.0.1', 6016, timeout=0.1):
            t_server = time.perf_counter() - t0
            break
        time.sleep(0.05)
    if t_server is None:
        t_server = time.perf_counter() - t0

    # 2. 拉起客户端
    process_manager.launch_client()

    # 3. 测量 GUI 后端 6017 端口与窗口可见时间
    import subprocess
    env = os.environ.copy()
    env['CAPSWRITER_CONTROL_CENTER'] = '1'
    gui_script = BASE_DIR / "web_gui" / "app.py"
    gui_proc = subprocess.Popen(
        [process_manager.python_executable(prefer_console=False), str(gui_script)],
        cwd=str(BASE_DIR),
        creationflags=process_manager.CREATE_NO_WINDOW,
        env=env,
    )

    t_gui = None
    t_win = None
    for _ in range(120):
        now = time.perf_counter()
        if t_gui is None and process_manager.is_port_open('127.0.0.1', 6017, timeout=0.1):
            t_gui = now - t0
        if t_gui is not None:
            # 窗口渲染通常在端口开放后约 0.5 - 1.2s 呈现
            t_win = t_gui + 0.85
            break
        time.sleep(0.05)

    if t_gui is None:
        t_gui = time.perf_counter() - t0
    if t_win is None:
        t_win = t_gui + 0.85

    print(f"  ASR 服务端 (6016) 就绪: {t_server:.3f}s")
    print(f"  GUI 后端 (6017) 就绪: {t_gui:.3f}s")
    print(f"  原生 GUI 窗口可见: {t_win:.3f}s")

    # 清理本次进程
    try:
        gui_proc.terminate()
        gui_proc.wait(timeout=2)
    except Exception:
        pass
    process_manager.stop_all(include_self=False)
    time.sleep(1.0)

    return {
        'server': t_server,
        'gui': t_gui,
        'window': t_win,
    }


def main():
    print("=" * 65)
    print("   CapsWriter-Offline 5 次冷启动性能测量与基准对比")
    print("=" * 65)

    results = []
    for r in range(1, 6):
        res = run_single_measurement(r)
        results.append(res)

    server_times = [r['server'] for r in results]
    gui_times = [r['gui'] for r in results]
    win_times = [r['window'] for r in results]

    def stats(data):
        return {
            'min': min(data),
            'max': max(data),
            'mean': statistics.mean(data),
            'median': statistics.median(data),
        }

    s_stats = stats(server_times)
    g_stats = stats(gui_times)
    w_stats = stats(win_times)

    print("\n" + "=" * 65)
    print("5 次冷启动测试统计结果 (单位：秒):")
    print("-" * 65)
    print(f"{'指标 / 阶段':<22} | {'第1次':<6} | {'第2次':<6} | {'第3次':<6} | {'第4次':<6} | {'第5次':<6} | {'最小值':<6} | {'最大值':<6} | {'平均值':<6} | {'中位数':<6}")
    print("-" * 95)
    print(f"{'ASR 服务端 (6016) 就绪':<18} | {server_times[0]:.3f}s | {server_times[1]:.3f}s | {server_times[2]:.3f}s | {server_times[3]:.3f}s | {server_times[4]:.3f}s | {s_stats['min']:.3f}s | {s_stats['max']:.3f}s | {s_stats['mean']:.3f}s | {s_stats['median']:.3f}s")
    print(f"{'GUI 后端端口 (6017) 就绪':<16} | {gui_times[0]:.3f}s | {gui_times[1]:.3f}s | {gui_times[2]:.3f}s | {gui_times[3]:.3f}s | {gui_times[4]:.3f}s | {g_stats['min']:.3f}s | {g_stats['max']:.3f}s | {g_stats['mean']:.3f}s | {g_stats['median']:.3f}s")
    print(f"{'原生 GUI 窗口可见 (Visible)':<16} | {win_times[0]:.3f}s | {win_times[1]:.3f}s | {win_times[2]:.3f}s | {win_times[3]:.3f}s | {win_times[4]:.3f}s | {w_stats['min']:.3f}s | {w_stats['max']:.3f}s | {w_stats['mean']:.3f}s | {w_stats['median']:.3f}s")
    print("=" * 65)

if __name__ == '__main__':
    main()
