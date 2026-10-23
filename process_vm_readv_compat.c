#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <unistd.h>

/* Private RDK compatibility adapter. It preserves /proc's ptrace permission
 * checks and only falls back when the kernel lacks CROSS_MEMORY_ATTACH.
 * Loading it is scoped to SnowLuma/QQ by their launcher, never /etc/ld.so.preload.
 */
typedef ssize_t (*readv_fn)(pid_t, const struct iovec *, unsigned long,
                           const struct iovec *, unsigned long, unsigned long);
static readv_fn original_readv;
static pthread_once_t resolve_once = PTHREAD_ONCE_INIT;

static void resolve_original(void) {
    original_readv = (readv_fn)dlsym(RTLD_NEXT, "process_vm_readv");
}

static int valid_vectors(const struct iovec *v, unsigned long n) {
    size_t total = 0;
    if (n > IOV_MAX) {
        errno = EINVAL;
        return 0;
    }
    if (n && !v) {
        errno = EFAULT;
        return 0;
    }
    for (unsigned long i = 0; i < n; ++i) {
        if (v[i].iov_len > (size_t)SSIZE_MAX - total) {
            errno = EINVAL;
            return 0;
        }
        total += v[i].iov_len;
    }
    return 1;
}

__attribute__((visibility("default")))
ssize_t process_vm_readv(pid_t pid, const struct iovec *local, unsigned long lc,
                         const struct iovec *remote, unsigned long rc,
                         unsigned long flags) {
    pthread_once(&resolve_once, resolve_original);
    if (original_readv) {
        ssize_t n = original_readv(pid, local, lc, remote, rc, flags);
        if (n >= 0 || errno != ENOSYS) {
            return n;
        }
    }
    if (flags || !valid_vectors(local, lc) || !valid_vectors(remote, rc)) {
        if (flags) errno = EINVAL;
        return -1;
    }
    if (!lc || !rc) return 0;
    if (pid <= 0) {
        errno = ESRCH;
        return -1;
    }
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        if (errno == ENOENT) errno = ESRCH;
        return -1;
    }
    unsigned long li = 0, ri = 0;
    size_t lo = 0, ro = 0;
    ssize_t total = 0;
    while (li < lc && ri < rc) {
        if (lo == local[li].iov_len) { ++li; lo = 0; continue; }
        if (ro == remote[ri].iov_len) { ++ri; ro = 0; continue; }
        size_t len = local[li].iov_len - lo;
        if (len > remote[ri].iov_len - ro) len = remote[ri].iov_len - ro;
        uintptr_t address = (uintptr_t)remote[ri].iov_base;
        if (address > (uintptr_t)INT64_MAX || ro > (uintptr_t)INT64_MAX - address) {
            errno = EFAULT;
            break;
        }
        ssize_t n;
        do {
            n = pread(fd, (void *)((uintptr_t)local[li].iov_base + lo), len,
                      (off_t)(address + ro));
        } while (n < 0 && errno == EINTR);
        if (n <= 0) {
            if (n == 0 || errno == EIO) errno = EFAULT;
            break;
        }
        total += n;
        lo += (size_t)n;
        ro += (size_t)n;
        if ((size_t)n < len) break;
    }
    int saved_errno = errno;
    close(fd);
    errno = saved_errno;
    if (total || li == lc || ri == rc) return total;
    return -1;
}
