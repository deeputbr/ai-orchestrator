/* Native Finder launcher. Rebuild from the repository root:
 * clang -arch arm64 -arch x86_64 -Os launchers/macos.c -o
 *   "Start Orchestrator.app/Contents/MacOS/Orchestrator"
 * Terminal displays server status; all user actions happen in the browser.
 */
#include <mach-o/dyld.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>

int main(void) {
    char executable[PATH_MAX], root[PATH_MAX], command[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) || !realpath(executable, root)) return 1;
    for (int i = 0; i < 4; ++i) {
        char *slash = strrchr(root, '/');
        if (!slash) return 1;
        *slash = '\0';
    }
    if (snprintf(command, sizeof(command), "%s/Start Orchestrator.command", root) >= (int)sizeof(command)) return 1;
    execl("/usr/bin/open", "open", "-a", "Terminal", command, (char *)NULL);
    return 1;
}
