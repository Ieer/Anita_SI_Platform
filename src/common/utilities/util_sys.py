import time
import psutil
import socket
import platform
import os
import cpuinfo
from cacheout import Cache
from threading import Lock

cache = Cache()


def bytes2human(n, format_str='%(value).1f%(symbol)s'):
    """Used by various scripts. See:
    http://goo.gl/zeJZl

    >>> bytes2human(10000)
    '9.8K'
    >>> bytes2human(100001221)
    '95.4M'
    """
    symbols = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
    prefix = {}
    for i, s in enumerate(symbols[1:]):
        prefix[s] = 1 << (i + 1) * 10
    for symbol in reversed(symbols[1:]):
        if n >= prefix[symbol]:
            value = float(n) / prefix[symbol]
            return format_str % locals()
    return format_str % dict(symbol=symbols[0], value=n)


cpu_info = cpuinfo.get_cpu_info()
cpu_num = psutil.cpu_count(logical=True)
_sample_lock = Lock()
_cpu_sample = psutil.cpu_times()


def duration(seconds):
    minutes = max(0, int(seconds)) // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f'{days}d/{hours}h/{minutes}m'


def optional_read(reader):
    try:
        return reader()
    except (psutil.Error, OSError, NotImplementedError):
        return None


@cache.memoize(ttl=5, typed=True)
def get_sys_info():
    global _cpu_sample
    # 获取CPU
    cpu_name = cpu_info.get('brand_raw') or platform.processor() or 'N/A'
    with _sample_lock:
        current_cpu = psutil.cpu_times()
        deltas = {field: max(0, getattr(current_cpu, field) - getattr(_cpu_sample, field))
                  for field in current_cpu._fields}
        _cpu_sample = current_cpu
    total = sum(value for field, value in deltas.items() if field not in ('guest', 'guest_nice'))
    cpu_user_usage_percent = deltas.get('user', 0) / total if total else None
    cpu_sys_usage_percent = deltas.get('system', 0) / total if total else None
    idle = deltas.get('idle', 0) + deltas.get('iowait', 0)
    cpu_free_percent = idle / total if total else None
    cpu_all_usage_percent = 1 - cpu_free_percent if total else None
    frequency = optional_read(psutil.cpu_freq)

    # 内存信息
    memory_info = psutil.virtual_memory()
    memory_total: str = bytes2human(memory_info.total)
    memory_used: str = bytes2human(memory_info.used)
    memory_free: str = bytes2human(memory_info.free)
    memory_usage_percent: float = memory_info.percent / 100
    swap = optional_read(psutil.swap_memory)

    # 主机信息
    hostname: str = socket.gethostname()
    os_name: str = platform.platform()
    computer_name: str = platform.node()
    os_arch: str = platform.machine()

    # python解释器信息
    current_process = psutil.Process(os.getpid())
    start_time_stamp = current_process.create_time()
    python_version: str = platform.python_version()
    start_time: str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time_stamp))
    current_time_stamp = time.time()
    difference = current_time_stamp - start_time_stamp
    run_time = duration(difference)
    current_process_memory_usage = bytes2human(current_process.memory_info().rss)
    process_threads = optional_read(current_process.num_threads)
    process_memory_percent = optional_read(current_process.memory_percent)
    network = optional_read(psutil.net_io_counters)
    disk_io = optional_read(psutil.disk_io_counters)
    boot_time = optional_read(psutil.boot_time)

    # 磁盘信息
    io = optional_read(psutil.disk_partitions) or []
    sys_files = []
    for i in io:
        o = optional_read(lambda: psutil.disk_usage(i.mountpoint))
        disk_data = dict(
            key=i.mountpoint,
            dir_name=i.device,
            sys_type_name=i.fstype,
            type_name=i.mountpoint,
            total=bytes2human(o.total) if o else 'N/A',
            used=bytes2human(o.used) if o else 'N/A',
            free=bytes2human(o.free) if o else 'N/A',
            usage=f'{o.percent:.1f}%' if o else 'N/A',
            usage_percent=o.percent if o else None,
            readable=o is not None,
        )
        sys_files.append(disk_data)
    # import pprint
    # pprint.pprint(sys_files)
    return dict(
        # 系统信息
        hostname=hostname,
        os_name=os_name,
        computer_name=computer_name,
        os_arch=os_arch,
        sampled_at=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time_stamp)),
        boot_time=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(boot_time)) if boot_time else 'N/A',
        host_uptime=duration(current_time_stamp - boot_time) if boot_time else 'N/A',
        # cpu
        cpu_name=cpu_name,
        cpu_num=cpu_num,
        cpu_physical_num=optional_read(lambda: psutil.cpu_count(logical=False)),
        cpu_frequency=f'{frequency.current / 1000:.2f} GHz' if frequency and frequency.current else 'N/A',
        cpu_free_percent=cpu_free_percent,
        cpu_user_usage_percent=cpu_user_usage_percent,
        cpu_sys_usage_percent=cpu_sys_usage_percent,
        cpu_all_usage_percent=cpu_all_usage_percent,
        #  内存
        memory_total=memory_total,
        memory_used=memory_used,
        memory_free=memory_free,
        memory_available=bytes2human(memory_info.available),
        memory_usage_percent=memory_usage_percent,
        swap_total=bytes2human(swap.total) if swap else 'N/A',
        swap_used=bytes2human(swap.used) if swap else 'N/A',
        swap_usage_percent=swap.percent / 100 if swap else None,
        # 程序运行状态
        python_version=python_version,
        start_time=start_time,
        run_time=run_time,
        current_process_memory_usage=current_process_memory_usage,
        process_id=current_process.pid,
        process_threads=process_threads,
        process_memory_percent=process_memory_percent / 100 if process_memory_percent is not None else None,
        network_sent=bytes2human(network.bytes_sent) if network else 'N/A',
        network_received=bytes2human(network.bytes_recv) if network else 'N/A',
        network_errors=network.errin + network.errout if network else None,
        network_dropped=network.dropin + network.dropout if network else None,
        disk_read=bytes2human(disk_io.read_bytes) if disk_io else 'N/A',
        disk_written=bytes2human(disk_io.write_bytes) if disk_io else 'N/A',
        # 磁盘
        sys_files=sys_files
    )
