"""Read-only self-memory probe; outputs no QQ/account/host identity data."""
import ctypes
import errno
import gzip
import json
import os
import platform
from pathlib import Path


class IOVec(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('length', ctypes.c_size_t)]


source = ctypes.create_string_buffer(b'rdk-test')
target = ctypes.create_string_buffer(len(source.raw))
local = IOVec(ctypes.addressof(target), len(target.raw))
remote = IOVec(ctypes.addressof(source), len(source.raw))
lib = ctypes.CDLL(None, use_errno=True)
readv = lib.process_vm_readv
readv.restype = ctypes.c_ssize_t
readv.argtypes = [ctypes.c_int, ctypes.POINTER(IOVec), ctypes.c_ulong,
                  ctypes.POINTER(IOVec), ctypes.c_ulong, ctypes.c_ulong]
ctypes.set_errno(0)
count = readv(os.getpid(), ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
error = ctypes.get_errno() if count < 0 else None
report = {
    'architecture': platform.machine(),
    'kernel': platform.release(),
    'process_vm_readv_self': {
        'bytes_read': count,
        'errno': error,
        'errno_name': errno.errorcode.get(error) if error is not None else None,
        'test_data_matches': count == len(source.raw) and source.raw == target.raw,
    },
}
try:
    with open('/proc/self/mem', 'rb', buffering=0) as mem:
        value = os.pread(mem.fileno(), len(source.raw), ctypes.addressof(source))
    report['proc_self_mem'] = {'test_data_matches': value == source.raw}
except OSError as exc:
    report['proc_self_mem'] = {'errno': exc.errno}

report['cross_memory_attach_config'] = 'unavailable'
for path in [Path('/boot/config-' + platform.release()), Path('/proc/config.gz')]:
    try:
        data = gzip.open(path, 'rt').read() if path.suffix == '.gz' else path.read_text()
    except OSError:
        continue
    for line in data.splitlines():
        if line.startswith('CONFIG_CROSS_MEMORY_ATTACH=') or line == '# CONFIG_CROSS_MEMORY_ATTACH is not set':
            report['cross_memory_attach_config'] = line
            break
    if report['cross_memory_attach_config'] != 'unavailable':
        break
print(json.dumps(report, indent=2, ensure_ascii=False))
