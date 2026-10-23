/* Oomi.app's executable (Contents/MacOS/Oomi).
 *
 * It starts the Python that ships inside the app and runs oomi's window. Because this file is the app's real
 * executable, macOS treats the process as Oomi: its name in the menu bar, the ghost in the Dock, notifications.
 *
 * tools/build_app.py compiles it for arm64 and x86_64 and joins the two into one universal file. It also builds on
 * Linux, where it runs the same way around a Linux Python, so the packaging can be tested away from a Mac.
 */
#include <Python.h>

#include <dlfcn.h>
#include <limits.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#ifdef __APPLE__
#include <mach-o/dyld.h>
#define LIBPYTHON "lib/libpython3.13.dylib"
#else
#define LIBPYTHON "lib/libpython3.13.so.1.0"
#endif

#if defined(__aarch64__) || defined(__arm64__)
#define ARCH "arm64"
#else
#define ARCH "x86_64"
#endif

extern char **environ;

static void fail(const char *what, const char *detail) {
    fprintf(stderr, "Oomi can't start: %s: %s\n", what, detail ? detail : "");
#ifdef __APPLE__
    /* Without Python there's no window to say this in, so ask macOS to show it. */
    char *argv[] = {"/usr/bin/osascript", "-e",
                    "display alert \"Oomi can't start\" message \"Some of its files are missing or damaged. "
                    "Download Oomi again and replace the copy in your Applications folder.\" as critical",
                    NULL};
    pid_t pid;
    if (posix_spawn(&pid, argv[0], NULL, NULL, argv, environ) == 0) {
        int status;
        waitpid(pid, &status, 0);
    }
#endif
    exit(70);
}

static void self_path(char *out) {
    char raw[PATH_MAX];
#ifdef __APPLE__
    uint32_t size = sizeof raw;
    if (_NSGetExecutablePath(raw, &size) != 0) fail("path too long", NULL);
#else
    ssize_t n = readlink("/proc/self/exe", raw, sizeof raw - 1);
    if (n < 0) fail("can't find itself", NULL);
    raw[n] = 0;
#endif
    if (!realpath(raw, out)) fail("can't find itself", raw);
}

static void join(char *out, const char *a, const char *b) {
    if (snprintf(out, PATH_MAX, "%s/%s", a, b) >= PATH_MAX) fail("path too long", a);
}

/* Python's API is looked up at run time from the bundled libpython (Python.h supplies the exact signatures). */
#define LOAD(name)                                                    \
    __typeof__(&name) p_##name = (__typeof__(&name))dlsym(lib, #name); \
    if (!p_##name) fail("missing function", #name)

int main(int argc, char **argv) {
    (void)argc;
    (void)argv; /* macOS may pass -psn_… when opened from Finder; nothing here needs arguments */
    char exe[PATH_MAX], macos[PATH_MAX], res_raw[PATH_MAX], res[PATH_MAX], home[PATH_MAX], lib_path[PATH_MAX],
        python[PATH_MAX], boot[PATH_MAX];

    self_path(exe); /* …/Oomi.app/Contents/MacOS/Oomi */
    strcpy(macos, exe);
    char *slash = strrchr(macos, '/');
    if (!slash) fail("unexpected location", exe);
    *slash = 0;
    join(res_raw, macos, "../Resources");
    if (!realpath(res_raw, res)) fail("missing Resources folder", res_raw);
    join(home, res, "runtime/" ARCH "/python");
    join(lib_path, home, LIBPYTHON);
    join(python, home, "bin/python3");
    join(boot, res, "boot.py");

    /* Apps opened from the Dock get no locale; tools oomi runs should still speak UTF-8. */
    if (!getenv("LANG") && !getenv("LC_ALL")) setenv("LANG", "en_US.UTF-8", 1);

    void *lib = dlopen(lib_path, RTLD_NOW | RTLD_GLOBAL);
    if (!lib) fail("can't load Python", dlerror());

    LOAD(PyPreConfig_InitIsolatedConfig);
    LOAD(Py_PreInitialize);
    LOAD(PyConfig_InitIsolatedConfig);
    LOAD(PyConfig_SetBytesString);
    LOAD(PyConfig_SetBytesArgv);
    LOAD(Py_InitializeFromConfig);
    LOAD(PyConfig_Clear);
    LOAD(PyStatus_Exception);
    LOAD(Py_ExitStatusException);
    LOAD(Py_RunMain);

    /* Isolated: ignores PYTHON* environment variables and the user's site-packages, like `python3 -I`. */
    PyPreConfig pre;
    p_PyPreConfig_InitIsolatedConfig(&pre);
    pre.utf8_mode = 1;
    PyStatus status = p_Py_PreInitialize(&pre);
    if (p_PyStatus_Exception(status)) p_Py_ExitStatusException(status);

    PyConfig config;
    p_PyConfig_InitIsolatedConfig(&config);
    config.install_signal_handlers = 1; /* among other things, a closed connection must not kill the app (SIGPIPE) */
    char *args[] = {boot, "--app", NULL};
    status = p_PyConfig_SetBytesString(&config, &config.home, home);
    if (p_PyStatus_Exception(status)) goto error;
    status = p_PyConfig_SetBytesString(&config, &config.executable, python); /* sys.executable: the real interpreter */
    if (p_PyStatus_Exception(status)) goto error;
    status = p_PyConfig_SetBytesString(&config, &config.program_name, python);
    if (p_PyStatus_Exception(status)) goto error;
    status = p_PyConfig_SetBytesString(&config, &config.run_filename, boot);
    if (p_PyStatus_Exception(status)) goto error;
    /* Compiled Python files go to ~/.oomi/pycache, never into the app, so the app's signature stays intact. */
    const char *user_home = getenv("HOME");
    if (user_home && *user_home) {
        char cache[PATH_MAX];
        join(cache, user_home, ".oomi/pycache");
        status = p_PyConfig_SetBytesString(&config, &config.pycache_prefix, cache);
        if (p_PyStatus_Exception(status)) goto error;
    } else {
        config.write_bytecode = 0;
    }
    status = p_PyConfig_SetBytesArgv(&config, 2, args);
    if (p_PyStatus_Exception(status)) goto error;
    status = p_Py_InitializeFromConfig(&config);
    if (p_PyStatus_Exception(status)) goto error;
    p_PyConfig_Clear(&config);
    return p_Py_RunMain();

error:
    p_PyConfig_Clear(&config);
    p_Py_ExitStatusException(status);
    return 1;
}
