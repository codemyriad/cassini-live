/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Whistle 3.3.0's C ABI has no thread-count option. On homogeneous x86 it
 * sizes its pool from hardware_concurrency(), ignoring container CPU limits.
 * Preload ONLY in the isolated recognition subprocess to bound that query.
 * This is a compatibility bridge for the pinned engine, not a CPU quota.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdlib.h>
#include <unistd.h>

static long (*real_sysconf)(int);
static pthread_once_t init = PTHREAD_ONCE_INIT;
static void resolve_sysconf(void) {
    real_sysconf = (long (*)(int))dlsym(RTLD_NEXT, "sysconf");
}

long sysconf(int name) {
    pthread_once(&init, resolve_sysconf);
    if (!real_sysconf) {
        errno = ENOSYS;
        return -1;
    }
    long value = real_sysconf(name);
    if (name != _SC_NPROCESSORS_ONLN && name != _SC_NPROCESSORS_CONF)
        return value;
    const char *setting = getenv("CASSINI_WHISTLE_THREADS");
    if (!setting || !*setting)
        return value;
    char *end;
    long limit = strtol(setting, &end, 10);
    if (*end || limit < 1 || limit > 32)
        return value;
    cpu_set_t allowed;
    if (sched_getaffinity(0, sizeof(allowed), &allowed) == 0) {
        int count = CPU_COUNT(&allowed);
        if (count > 0 && count < limit)
            limit = count;
    }
    return value > limit ? limit : value;
}
