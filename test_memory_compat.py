"""Run on RDK with LD_PRELOAD set to the private compatibility adapter."""
import ctypes
import errno
import os


class IOVec(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('length', ctypes.c_size_t)]


lib = ctypes.CDLL(None, use_errno=True)
readv = lib.process_vm_readv
readv.restype = ctypes.c_ssize_t
readv.argtypes = [ctypes.c_int, ctypes.POINTER(IOVec), ctypes.c_ulong,
                  ctypes.POINTER(IOVec), ctypes.c_ulong, ctypes.c_ulong]


def vectors(*items):
    return (IOVec * len(items))(*(IOVec(ctypes.addressof(b) + start, size)
                                 for b, start, size in items))


source = ctypes.create_string_buffer(b'abcdefgh')
dest = ctypes.create_string_buffer(8)
local = vectors((dest, 0, 3), (dest, 3, 5))
remote = vectors((source, 0, 5), (source, 5, 3))
assert readv(os.getpid(), local, 2, remote, 2, 0) == 8
assert dest.raw == b'abcdefgh'
assert readv(os.getpid(), local, 2, remote, 2, 1) == -1
assert ctypes.get_errno() == errno.EINVAL
assert readv(os.getpid(), local, 0, remote, 2, 0) == 0
assert readv(2147483647, local, 2, remote, 2, 0) == -1
assert ctypes.get_errno() == errno.ESRCH
invalid = (IOVec * 1)(IOVec(1, 4))
assert readv(os.getpid(), local, 2, invalid, 1, 0) == -1
assert ctypes.get_errno() == errno.EFAULT
partial = (IOVec * 2)(remote[0], invalid[0])
assert readv(os.getpid(), local, 2, partial, 2, 0) == 5

# A child has its own live address space; read inherited memory without printing it.
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(w)
    os.read(r, 1)
    os._exit(0)
os.close(r)
try:
    assert readv(pid, local, 2, remote, 2, 0) == 8
    assert dest.raw == b'abcdefgh'
finally:
    os.close(w)
    os.waitpid(pid, 0)
print('PASS: scatter/gather, flags, empty, absent PID, invalid address, partial read, child process')

if os.environ.get('COMPAT_TEST_PERMISSION_GUARD') == '1':
    ready_r, ready_w = os.pipe()
    done_r, done_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        os.close(done_w)
        assert lib.prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE=0
        os.write(ready_w, b'1')
        os.read(done_r, 1)
        os._exit(0)
    os.close(ready_w)
    os.close(done_r)
    try:
        assert os.read(ready_r, 1) == b'1'
        assert readv(pid, local, 2, remote, 2, 0) == -1
        assert ctypes.get_errno() in (errno.EACCES, errno.EPERM)
    finally:
        os.close(done_w)
        os.close(ready_r)
        os.waitpid(pid, 0)
    print('PASS: permission denial preserved for a non-dumpable child')
