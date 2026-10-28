// Minimal stand-in for animEngineStarter.exe, for automated test runs.
//
// The original starter only calls animEngineDLL's AnimEngineWinMain. Windows forces that exe to
// run elevated, which means a UAC prompt per launch; this one doesn't. It passes the original
// starter (mapped as an image, not run) as hInstance, so the engine still finds its icons, menus
// and dialogs there. Built without a manifest, so Windows treats it as a legacy app and redirects
// the engine's writes into Program Files (stageplays) to the user's VirtualStore, as before.
//
// Usage: facade_test_starter.exe <Facade>\util\sources\facade   (also used as working directory)
#include <windows.h>
#include <stdio.h>

typedef int(__cdecl *WinMainFn)(HINSTANCE, HINSTANCE, char *, int);

int WINAPI WinMain(HINSTANCE self, HINSTANCE prev, LPSTR cmd, int show) {
    char dir[MAX_PATH], path[MAX_PATH];
    if (!cmd || !*cmd) {
        MessageBoxA(NULL, "usage: facade_test_starter.exe <Facade>\\util\\sources\\facade", "Facade test starter", MB_OK);
        return 1;
    }
    lstrcpynA(dir, cmd[0] == '"' ? cmd + 1 : cmd, MAX_PATH);
    char *q = strchr(dir, '"');
    if (q) *q = 0;
    SetCurrentDirectoryA(dir);

    _snprintf(path, MAX_PATH, "%s\\animEngineStarter.exe", dir);
    HINSTANCE res = (HINSTANCE)LoadLibraryExA(path, NULL, DONT_RESOLVE_DLL_REFERENCES);
    _snprintf(path, MAX_PATH, "%s\\animEngineDLL.dll", dir);
    HMODULE eng = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!eng) {
        char msg[300];
        _snprintf(msg, sizeof msg, "Couldn't load %s (error %lu)", path, GetLastError());
        MessageBoxA(NULL, msg, "Facade test starter", MB_OK);
        return 1;
    }
    WinMainFn main = (WinMainFn)GetProcAddress(eng, "?AnimEngineWinMain@@YAHPAUHINSTANCE__@@0PADH@Z");
    if (!main) return 2;
    return main(res ? res : self, NULL, (char *)"", show);
}
