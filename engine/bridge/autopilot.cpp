// Autopilot: lets automated tests play Façade without a person, inside the engine process.
// Active only when FACADE_AUTOPILOT names a script file (engine/autopilot/launch.py sets it);
// normal play is unaffected. Included at the end of bridge.cpp (shares logf, patch_iat, orig).
//
// Script lines: <seconds since start> <action> [args]
//   key <KEY> <hold seconds>     hold a key (RETURN ESCAPE SPACE UP DOWN LEFT RIGHT, A-Z, 0-9)
//   click [left|right]           a mouse click
//   move <dx> <dy>               relative mouse movement
//   say <text...>                as if the player typed text and pressed Enter
//   shot                         save the next frame to %LOCALAPPDATA%\FacadeFanRemake\frames
//   shots <every seconds>        save a frame periodically
// Also: the game's audio is muted, and the engine always believes its window has focus.

#include <mmdeviceapi.h>
#include <audiopolicy.h>
#include <vector>
#include <string>

// t is seconds after start, or after an anchor: the first time the engine opens a file whose
// name contains `anchor` (e.g. "@tr_intro_end+12" = 12 s after the title music's last cue).
struct Step { double t; std::string anchor, action, arg; };
static std::vector<std::pair<std::string, double>> g_opened;  // (file, seconds) as the engine opens them
static CRITICAL_SECTION g_opened_lock;

static void note_open(const char *name) {
    if (!g_t0 || !name) return;
    EnterCriticalSection(&g_opened_lock);
    g_opened.push_back({name, (GetTickCount() - g_t0) / 1000.0});
    LeaveCriticalSection(&g_opened_lock);
}
static double anchor_time(const std::string &a) {  // -1 until it has happened
    double t = -1;
    EnterCriticalSection(&g_opened_lock);
    for (auto &o : g_opened) if (o.first.find(a) != std::string::npos) { t = o.second; break; }
    LeaveCriticalSection(&g_opened_lock);
    return t;
}
static std::vector<Step> g_steps;
static DWORD g_t0;
static HWND g_main;
static volatile LONG g_shot;             // frames still to capture
static double g_shot_every;
static BYTE g_keys[256];                 // DirectInput key codes currently held by the autopilot
static DWORD g_key_until[256];
static volatile LONG g_mouse_dx, g_mouse_dy, g_click;

static double since() { return (GetTickCount() - g_t0) / 1000.0; }

static int dik_for(const std::string &k) {
    static const struct { const char *n; int c; } named[] = {
        {"RETURN", 0x1C}, {"ESCAPE", 0x01}, {"SPACE", 0x39}, {"UP", 0xC8}, {"DOWN", 0xD0},
        {"LEFT", 0xCB}, {"RIGHT", 0xCD}, {"BACK", 0x0E}, {"TAB", 0x0F}};
    for (auto &n : named) if (_stricmp(k.c_str(), n.n) == 0) return n.c;
    static const char row[] = "QWERTYUIOP\0\0\0\0ASDFGHJKL\0\0\0\0\0ZXCVBNM";
    if (k.size() == 1) {
        char c = (char)toupper(k[0]);
        if (c >= '1' && c <= '9') return 0x02 + (c - '1');
        if (c == '0') return 0x0B;
        for (int i = 0; i < (int)sizeof row; i++) if (row[i] == c) return 0x10 + i;
    }
    return -1;
}
static int vk_for_dik(int dik) {
    switch (dik) {
    case 0x1C: return VK_RETURN; case 0x01: return VK_ESCAPE; case 0x39: return VK_SPACE;
    case 0xC8: return VK_UP; case 0xD0: return VK_DOWN; case 0xCB: return VK_LEFT; case 0xCD: return VK_RIGHT;
    }
    return MapVirtualKeyA(dik, 1 /* MAPVK_VSC_TO_VK */);
}

// ---- DirectInput: feed scripted keys and mouse to the engine's devices ----
static const GUID kSysKeyboard = {0x6F1D2B61, 0xD5A0, 0x11CF, {0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};
static const GUID kSysMouse = {0x6F1D2B60, 0xD5A0, 0x11CF, {0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00}};
static void *g_kbd, *g_mouse;
typedef HRESULT(WINAPI *DI8Create)(HINSTANCE, DWORD, REFIID, LPVOID *, void *);
typedef HRESULT(WINAPI *CreateDeviceFn)(void *, REFGUID, void **, void *);
typedef HRESULT(WINAPI *GetStateFn)(void *, DWORD, void *);
typedef HRESULT(WINAPI *GetDataFn)(void *, DWORD, void *, DWORD *, DWORD);
typedef HRESULT(WINAPI *SimpleFn)(void *);
static DI8Create real_DI8Create;
static CreateDeviceFn real_CreateDevice;
static GetStateFn real_GetState;
static GetDataFn real_GetData;
static SimpleFn real_Acquire;
static bool g_logged_state, g_logged_data;

static void patch_vtbl(void *obj, int index, void *repl, void **save) {
    void **vt = *(void ***)obj;
    if (vt[index] == repl) return;
    DWORD old;
    VirtualProtect(&vt[index], sizeof(void *), PAGE_READWRITE, &old);
    if (save && !*save) *save = vt[index];
    vt[index] = repl;
    VirtualProtect(&vt[index], sizeof(void *), old, &old);
}

static HRESULT WINAPI hook_Acquire(void *self) {
    HRESULT hr = real_Acquire(self);
    return FAILED(hr) ? S_OK : hr;  // a hidden desktop can't give real focus; pretend it did
}
static HRESULT WINAPI hook_GetState(void *self, DWORD size, void *data) {
    HRESULT hr = real_GetState(self, size, data);
    if (FAILED(hr)) { memset(data, 0, size); hr = S_OK; }
    if (!g_logged_state) { g_logged_state = true; logf("autopilot: engine polls %s with GetDeviceState(%lu)", self == g_kbd ? "keyboard" : "mouse", size); }
    if (self == g_kbd && size >= 256) {
        DWORD now = GetTickCount();
        for (int i = 0; i < 256; i++) if (g_keys[i] && now < g_key_until[i]) ((BYTE *)data)[i] = 0x80;
    } else if (self == g_mouse && size >= 16) {
        LONG *m = (LONG *)data;
        m[0] += InterlockedExchange(&g_mouse_dx, 0);
        m[1] += InterlockedExchange(&g_mouse_dy, 0);
        LONG c = InterlockedExchange(&g_click, 0);
        if (c) ((BYTE *)data)[12 + (c - 1)] = 0x80;
    }
    return hr;
}
// Buffered keyboard events the autopilot has queued: (due tick, DIK code, down?).
struct KeyEvent { DWORD due; int dik; bool down; };
static std::vector<KeyEvent> g_events;
static CRITICAL_SECTION g_events_lock;
static DWORD g_seq = 1000000;

static void queue_key(int dik, double hold) {
    DWORD now = GetTickCount();
    EnterCriticalSection(&g_events_lock);
    g_events.push_back({now, dik, true});
    g_events.push_back({now + (DWORD)(hold * 1000), dik, false});
    LeaveCriticalSection(&g_events_lock);
}

static HRESULT WINAPI hook_GetData(void *self, DWORD cb, void *rg, DWORD *inout, DWORD flags) {
    DWORD room = inout ? *inout : 0;
    HRESULT hr = real_GetData(self, cb, rg, inout, flags);
    if (!g_logged_data) { g_logged_data = true; logf("autopilot: engine reads %s with GetDeviceData (cb=%lu)", self == g_kbd ? "keyboard" : "mouse", cb); }
    if (FAILED(hr)) { if (inout) *inout = 0; hr = S_OK; }
    if (self != g_kbd || !inout) return hr;
    // Append due autopilot key events after whatever the real device produced.
    DWORD now = GetTickCount(), n = *inout;
    EnterCriticalSection(&g_events_lock);
    for (size_t i = 0; i < g_events.size() && n < room;) {
        if (g_events[i].due > now) { i++; continue; }
        if (rg && cb >= 16) {
            DWORD *e = (DWORD *)((BYTE *)rg + (size_t)n * cb);
            memset(e, 0, cb);
            e[0] = g_events[i].dik;                 // dwOfs: which key
            e[1] = g_events[i].down ? 0x80 : 0x00;  // dwData: pressed or released
            e[2] = now;                             // dwTimeStamp
            e[3] = g_seq++;                         // dwSequence
        }
        n++;
        if (!(flags & 1 /* DIGDD_PEEK */)) g_events.erase(g_events.begin() + i); else i++;
    }
    LeaveCriticalSection(&g_events_lock);
    *inout = n;
    return hr;
}
static HRESULT WINAPI hook_CreateDevice(void *di, REFGUID guid, void **dev, void *outer) {
    HRESULT hr = real_CreateDevice(di, guid, dev, outer);
    if (SUCCEEDED(hr) && dev && *dev) {
        bool kbd = IsEqualGUID(guid, kSysKeyboard), mouse = IsEqualGUID(guid, kSysMouse);
        if (kbd) g_kbd = *dev;
        if (mouse) g_mouse = *dev;
        logf("autopilot: DirectInput device %s", kbd ? "keyboard" : mouse ? "mouse" : "other");
        patch_vtbl(*dev, 7, (void *)hook_Acquire, (void **)&real_Acquire);
        patch_vtbl(*dev, 9, (void *)hook_GetState, (void **)&real_GetState);
        patch_vtbl(*dev, 10, (void *)hook_GetData, (void **)&real_GetData);
    }
    return hr;
}
static HRESULT WINAPI hook_DI8Create(HINSTANCE h, DWORD ver, REFIID iid, LPVOID *out, void *outer) {
    HRESULT hr = real_DI8Create(h, ver, iid, out, outer);
    if (SUCCEEDED(hr) && out && *out) patch_vtbl(*out, 3, (void *)hook_CreateDevice, (void **)&real_CreateDevice);
    return hr;
}

// ---- focus and polled keys ----
typedef HWND(WINAPI *HwndFn)();
typedef SHORT(WINAPI *KeyFn)(int);
typedef HWND(WINAPI *CreateWindowFn)(DWORD, LPCSTR, LPCSTR, DWORD, int, int, int, int, HWND, HMENU, HINSTANCE, LPVOID);
static HwndFn real_GetForeground;
static KeyFn real_GetAsyncKeyState;
static CreateWindowFn real_CreateWindowExA;

static HWND WINAPI hook_GetForeground() { return g_main ? g_main : real_GetForeground(); }
static SHORT WINAPI hook_GetAsyncKeyState(int vk) {
    DWORD now = GetTickCount();
    for (int i = 0; i < 256; i++)
        if (g_keys[i] && now < g_key_until[i] && vk_for_dik(i) == vk) return (SHORT)0x8001;
    return real_GetAsyncKeyState(vk);
}
static HWND WINAPI hook_CreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR name, DWORD style, int x, int y, int w, int h,
                                        HWND parent, HMENU menu, HINSTANCE inst, LPVOID param) {
    HWND wnd = real_CreateWindowExA(ex, cls, name, style, x, y, w, h, parent, menu, inst, param);
    if (!parent && !g_main) { g_main = wnd; logf("autopilot: main window %p \"%s\" %dx%d", wnd, name ? name : "", w, h); }
    return wnd;
}

// ---- frames: read the back buffer just before it's shown ----
typedef BOOL(WINAPI *SwapFn)(HDC);
static SwapFn real_SwapBuffers;
static double g_next_auto_shot;

static void save_frame() {
    HMODULE gl = GetModuleHandleA("opengl32.dll");
    typedef void(WINAPI *ReadPixels)(int, int, int, int, unsigned, unsigned, void *);
    typedef void(WINAPI *GetIntegerv)(unsigned, int *);
    typedef void(WINAPI *ReadBuffer)(unsigned);
    ReadPixels readPixels = (ReadPixels)GetProcAddress(gl, "glReadPixels");
    GetIntegerv getIntegerv = (GetIntegerv)GetProcAddress(gl, "glGetIntegerv");
    ReadBuffer readBuffer = (ReadBuffer)GetProcAddress(gl, "glReadBuffer");
    if (!readPixels || !getIntegerv) return;
    int vp[4];
    getIntegerv(0x0BA2 /* GL_VIEWPORT */, vp);
    int w = vp[2], h = vp[3];
    if (w <= 0 || h <= 0 || w > 8192 || h > 8192) return;
    int stride = (w * 3 + 3) & ~3;
    std::vector<BYTE> px((size_t)stride * h);
    if (readBuffer) readBuffer(0x0405 /* GL_BACK */);
    readPixels(vp[0], vp[1], w, h, 0x80E0 /* GL_BGR_EXT */, 0x1401 /* GL_UNSIGNED_BYTE */, px.data());
    char path[MAX_PATH];
    GetEnvironmentVariableA("LOCALAPPDATA", path, MAX_PATH);
    strcat(path, "\\FacadeFanRemake\\frames");
    CreateDirectoryA(path, NULL);
    char file[MAX_PATH];
    _snprintf(file, MAX_PATH, "%s\\frame_%06.1f.bmp", path, since());
    FILE *f = fopen(file, "wb");
    if (!f) return;
    BITMAPFILEHEADER bf = {0x4D42, (DWORD)(sizeof bf + sizeof(BITMAPINFOHEADER) + px.size()), 0, 0,
                           sizeof bf + sizeof(BITMAPINFOHEADER)};
    BITMAPINFOHEADER bi = {sizeof bi, w, h, 1, 24, BI_RGB};
    fwrite(&bf, sizeof bf, 1, f);
    fwrite(&bi, sizeof bi, 1, f);
    fwrite(px.data(), 1, px.size(), f);
    fclose(f);
    logf("autopilot: frame %dx%d -> %s", w, h, file);
}
static BOOL WINAPI hook_SwapBuffers(HDC dc) {
    if (g_shot_every > 0 && since() >= g_next_auto_shot) {
        g_next_auto_shot = since() + g_shot_every;
        InterlockedIncrement(&g_shot);
    }
    if (g_shot > 0) { InterlockedDecrement(&g_shot); save_frame(); }
    return real_SwapBuffers(dc);
}

// ---- mute this process's audio (Core Audio session volume) ----
static DWORD WINAPI mute_thread(LPVOID) {
    CoInitializeEx(NULL, COINIT_MULTITHREADED);
    IMMDeviceEnumerator *en = NULL;
    IMMDevice *dev = NULL;
    IAudioSessionManager *mgr = NULL;
    ISimpleAudioVolume *vol = NULL;
    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), NULL, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void **)&en);
    if (SUCCEEDED(hr)) hr = en->GetDefaultAudioEndpoint(eRender, eConsole, &dev);
    if (SUCCEEDED(hr)) hr = dev->Activate(__uuidof(IAudioSessionManager), CLSCTX_ALL, NULL, (void **)&mgr);
    if (SUCCEEDED(hr)) hr = mgr->GetSimpleAudioVolume(NULL, FALSE, &vol);
    if (SUCCEEDED(hr)) hr = vol->SetMute(TRUE, NULL);
    logf("autopilot: audio %s (hr=0x%lx)", SUCCEEDED(hr) ? "muted" : "NOT muted", hr);
    if (vol) vol->Release();
    if (mgr) mgr->Release();
    if (dev) dev->Release();
    if (en) en->Release();
    CoUninitialize();
    return 0;
}

// ---- the script ----
static void load_script(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { logf("autopilot: can't open script %s", path); return; }
    char line[512];
    while (fgets(line, sizeof line, f)) {
        char *s = line;
        while (*s == ' ' || *s == '\t') s++;
        if (*s == '#' || *s == '\n' || !*s) continue;
        Step st;
        char act[32] = {0}, when[128] = {0};
        int used = 0;
        if (sscanf(s, "%127s %31s %n", when, act, &used) < 2) continue;
        if (when[0] == '@') {  // @anchor+seconds
            char *plus = strrchr(when, '+');
            st.t = plus ? atof(plus + 1) : 0;
            if (plus) *plus = 0;
            st.anchor = when + 1;
        } else {
            st.t = atof(when);
        }
        st.action = act;
        st.arg = s + used;
        while (!st.arg.empty() && (st.arg.back() == '\n' || st.arg.back() == '\r')) st.arg.pop_back();
        g_steps.push_back(st);
    }
    fclose(f);
    logf("autopilot: %u steps from %s", (unsigned)g_steps.size(), path);
}

static void run_step(const Step &st) {
    logf("autopilot: t=%.1f %s %s", st.t, st.action.c_str(), st.arg.c_str());
    if (st.action == "key") {
        char k[32] = {0};
        double hold = 0.15;
        sscanf(st.arg.c_str(), "%31s %lf", k, &hold);
        int dik = dik_for(k);
        if (dik < 0) { logf("autopilot: unknown key %s", k); return; }
        g_keys[dik] = 1;
        g_key_until[dik] = GetTickCount() + (DWORD)(hold * 1000);
        queue_key(dik, hold);
        if (g_main) {  // also as window messages, for anything that reads WM_KEYDOWN/WM_CHAR
            int vk = vk_for_dik(dik);
            LPARAM lp = 1 | (dik << 16);
            PostMessageA(g_main, WM_KEYDOWN, vk, lp);
            if (strlen(k) == 1) PostMessageA(g_main, WM_CHAR, tolower(k[0]), lp);
            else if (vk == VK_RETURN) PostMessageA(g_main, WM_CHAR, '\r', lp);
            PostMessageA(g_main, WM_KEYUP, vk, lp | 0xC0000000);
        }
    } else if (st.action == "click") {
        InterlockedExchange(&g_click, st.arg.find("right") != std::string::npos ? 2 : 1);
    } else if (st.action == "move") {
        long dx = 0, dy = 0;
        sscanf(st.arg.c_str(), "%ld %ld", &dx, &dy);
        InterlockedExchangeAdd(&g_mouse_dx, dx);
        InterlockedExchangeAdd(&g_mouse_dy, dy);
    } else if (st.action == "say") {
        static bool (*entered)(char *const) =
            (bool (*)(char *const))orig("?AblCallbackAPI_PlayerEnteredText@@YA_NQAD@Z");
        std::string text = st.arg;
        logf("PlayerEnteredText \"%s\" (autopilot)", text.c_str());
        entered(&text[0]);
    } else if (st.action == "shot") {
        InterlockedIncrement(&g_shot);
    } else if (st.action == "shots") {
        g_shot_every = atof(st.arg.c_str());
    }
}

static DWORD WINAPI script_thread(LPVOID) {
    for (size_t i = 0; i < g_steps.size(); i++) {
        const Step &st = g_steps[i];
        if (!st.anchor.empty()) {
            double a;
            while ((a = anchor_time(st.anchor)) < 0) Sleep(50);
            while (since() < a + st.t) Sleep(20);
        } else {
            while (since() < st.t) Sleep(20);
        }
        run_step(st);
    }
    logf("autopilot: script finished");
    return 0;
}

static void autopilot_attach(HMODULE eng) {
    char script[MAX_PATH];
    if (!GetEnvironmentVariableA("FACADE_AUTOPILOT", script, MAX_PATH)) return;
    InitializeCriticalSection(&g_events_lock);
    InitializeCriticalSection(&g_opened_lock);
    g_t0 = GetTickCount();
    real_DI8Create = (DI8Create)patch_iat(eng, "DINPUT8.dll", "DirectInput8Create", (void *)hook_DI8Create);
    real_GetForeground = (HwndFn)patch_iat(eng, "USER32.dll", "GetForegroundWindow", (void *)hook_GetForeground);
    real_GetAsyncKeyState = (KeyFn)patch_iat(eng, "USER32.dll", "GetAsyncKeyState", (void *)hook_GetAsyncKeyState);
    real_CreateWindowExA = (CreateWindowFn)patch_iat(eng, "USER32.dll", "CreateWindowExA", (void *)hook_CreateWindowExA);
    real_SwapBuffers = (SwapFn)patch_iat(eng, "GDI32.dll", "SwapBuffers", (void *)hook_SwapBuffers);
    logf("autopilot: hooks DI8=%d fg=%d keys=%d wnd=%d swap=%d", !!real_DI8Create, !!real_GetForeground,
         !!real_GetAsyncKeyState, !!real_CreateWindowExA, !!real_SwapBuffers);
    load_script(script);
    CloseHandle(CreateThread(NULL, 0, mute_thread, NULL, 0, NULL));
    CloseHandle(CreateThread(NULL, 0, script_thread, NULL, 0, NULL));
}
