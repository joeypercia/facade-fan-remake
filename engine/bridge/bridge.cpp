// Observe-only proxy for Façade's nativeaniminterface.dll (the bridge between the Java drama
// manager and the animation engine).
//
// Installed in place of the original, which is renamed nativeaniminterface_orig.dll. Every export
// is forwarded to the original: most by linker forwarders (bridge.def), and the calls we want to
// see by the wrappers below, which log and then call through unchanged. Behaviour is identical to
// the original game.
//
// Log: %LOCALAPPDATA%\FacadeFanRemake\bridge.log, one line per call, tagged with the process
// (the Java drama manager and the engine both load this DLL).

#include <windows.h>
#include <jni.h>
#include <stdarg.h>
#include <stdio.h>
#include <string>

static HMODULE g_self, g_orig;
static CRITICAL_SECTION g_lock;
static FILE *g_log;
static char g_proc[32];

static FARPROC orig(const char *name) {
    if (!g_orig) {
        char path[MAX_PATH];
        GetModuleFileNameA(g_self, path, MAX_PATH);
        char *slash = strrchr(path, '\\');
        strcpy(slash ? slash + 1 : path, "nativeaniminterface_orig.dll");
        g_orig = LoadLibraryA(path);
    }
    FARPROC p = g_orig ? GetProcAddress(g_orig, name) : NULL;
    if (!p) {  // nothing sensible to do without the original: fail loudly
        char msg[300];
        _snprintf(msg, sizeof msg, "Facade Fan Remake bridge: missing %s in nativeaniminterface_orig.dll", name);
        MessageBoxA(NULL, msg, "Facade bridge", MB_ICONERROR);
        ExitProcess(1);
    }
    return p;
}

static void logf(const char *fmt, ...) {
    EnterCriticalSection(&g_lock);
    if (!g_log) {
        char dir[MAX_PATH];
        DWORD n = GetEnvironmentVariableA("LOCALAPPDATA", dir, MAX_PATH);
        if (n && n < MAX_PATH - 40) {
            strcat(dir, "\\FacadeFanRemake");
            CreateDirectoryA(dir, NULL);
            strcat(dir, "\\bridge.log");
            g_log = fopen(dir, "a");
        }
    }
    if (g_log) {
        fprintf(g_log, "%10lu %-6s ", GetTickCount(), g_proc);
        va_list ap;
        va_start(ap, fmt);
        vfprintf(g_log, fmt, ap);
        va_end(ap);
        fputc('\n', g_log);
        fflush(g_log);
    }
    LeaveCriticalSection(&g_lock);
}

static void engine_attach();
static void autopilot_attach(HMODULE eng);
// inject.cpp: generated lines
static int generated_line(int ch, int s1, int s2, int s3, int s4);
static void report_heard(const char *text);
static bool understood_text(const char *text, std::string &out);
static const char *redirect_wav(const char *name, std::string &keep);
static void restore_lines(int ch);
static void inject_attach();

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = h;
        InitializeCriticalSection(&g_lock);
        char exe[MAX_PATH];
        GetModuleFileNameA(NULL, exe, MAX_PATH);
        const char *base = strrchr(exe, '\\');
        base = base ? base + 1 : exe;
        bool java = _strnicmp(base, "java", 4) == 0;
        _snprintf(g_proc, sizeof g_proc, "%s", java ? "java" : "engine");
        if (!java) engine_attach();
    }
    return TRUE;
}

// ======================= engine process: observe file loads, find the script table ===========

// Swap one entry of a module's import table. Returns the previous target, or NULL.
static void *patch_iat(HMODULE mod, const char *dll, const char *fn, void *repl) {
    BYTE *base = (BYTE *)mod;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + ((IMAGE_DOS_HEADER *)base)->e_lfanew);
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    for (IMAGE_IMPORT_DESCRIPTOR *d = (IMAGE_IMPORT_DESCRIPTOR *)(base + dir.VirtualAddress); d->Name; d++) {
        if (_stricmp((char *)(base + d->Name), dll) != 0) continue;
        IMAGE_THUNK_DATA *names = (IMAGE_THUNK_DATA *)(base + d->OriginalFirstThunk);
        IMAGE_THUNK_DATA *slots = (IMAGE_THUNK_DATA *)(base + d->FirstThunk);
        for (; names->u1.AddressOfData; names++, slots++) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME *ibn = (IMAGE_IMPORT_BY_NAME *)(base + names->u1.AddressOfData);
            if (strcmp((char *)ibn->Name, fn) != 0) continue;
            DWORD old;
            VirtualProtect(&slots->u1.Function, sizeof(void *), PAGE_READWRITE, &old);
            void *prev = (void *)slots->u1.Function;
            slots->u1.Function = (ULONG_PTR)repl;
            VirtualProtect(&slots->u1.Function, sizeof(void *), old, &old);
            return prev;
        }
    }
    return NULL;
}

typedef void *(WINAPI *MmioOpenA)(LPSTR, void *, DWORD);
typedef HANDLE(WINAPI *CreateFileAFn)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
static MmioOpenA real_mmioOpenA;
static CreateFileAFn real_CreateFileA;

static void note_open(const char *name);

static void *WINAPI hook_mmioOpenA(LPSTR name, void *info, DWORD flags) {
    std::string keep;
    const char *use = redirect_wav(name, keep);  // a generated line's voice, if one is queued
    void *h = real_mmioOpenA((LPSTR)use, info, flags);
    if (!h && use != name) h = real_mmioOpenA(name, info, flags);  // generated file unreadable: original
    note_open(name);
    logf("mmioOpenA \"%s\" flags=0x%lx -> %p", name ? name : "(null)", flags, h);
    return h;
}
static HANDLE WINAPI hook_CreateFileA(LPCSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                      DWORD attrs, HANDLE tmpl) {
    HANDLE h = real_CreateFileA(name, access, share, sa, disp, attrs, tmpl);
    logf("CreateFileA \"%s\" access=0x%lx", name ? name : "(null)", access);
    return h;
}

static void engine_attach() {
    // The engine DLL imports this bridge, so by the time we initialise its imports are bound.
    HMODULE eng = GetModuleHandleA("animEngineDLL.dll");
    if (!eng) { logf("engine: animEngineDLL.dll not loaded yet"); return; }
    real_mmioOpenA = (MmioOpenA)patch_iat(eng, "WINMM.dll", "mmioOpenA", (void *)hook_mmioOpenA);
    real_CreateFileA = (CreateFileAFn)patch_iat(eng, "KERNEL32.dll", "CreateFileA", (void *)hook_CreateFileA);
    logf("engine: hooks mmioOpenA=%s CreateFileA=%s", real_mmioOpenA ? "on" : "MISSING",
         real_CreateFileA ? "on" : "MISSING");
    autopilot_attach(eng);  // only does anything in automated test runs
    inject_attach();

    // Longer input: the text box cuts typing off where it reaches the box's right edge
    // (animEngineDLL+0x9c9bf: "je keep_char" before the truncation). Make that jump unconditional,
    // so typing continues up to the 255-character buffer the engine already has.
    BYTE *jcc = (BYTE *)eng + 0x9c9bf;
    if (jcc[0] == 0x74 && jcc[1] == 0x2e) {
        DWORD old;
        VirtualProtect(jcc, 1, PAGE_EXECUTE_READWRITE, &old);
        jcc[0] = 0xEB;
        VirtualProtect(jcc, 1, old, &old);
        FlushInstructionCache(GetCurrentProcess(), jcc, 1);
        logf("engine: text input no longer cut off at the box edge (up to 255 characters)");
    } else {
        logf("engine: unexpected bytes at the text-width check; input length unchanged");
    }
}

// --- memory scan: where does the engine keep line 1055 (Trip, "Where are the new wine glasses?")?

struct Pattern { const char *name; BYTE bytes[64]; int len; };

static int put32(BYTE *p, const int *v, int n) { memcpy(p, v, n * 4); return n * 4; }

static void build_patterns(Pattern *ps, int *count) {
    static const int record[] = {199998, 1055, 0, 0, 0, 200000, 200005, 752};
    static const int phon[] = {70, 35, 38, 44, 31, 44, 49, 39, 55, 36};
    static const char text[] = "Where are the new wine glasses";
    int n = 0;
    ps[n].name = "record-int32"; ps[n].len = put32(ps[n].bytes, record, 8); n++;
    ps[n].name = "phonemes-int32"; ps[n].len = put32(ps[n].bytes, phon, 10); n++;
    ps[n].name = "phonemes-int16";
    for (int i = 0; i < 10; i++) ((short *)ps[n].bytes)[i] = (short)phon[i];
    ps[n].len = 20; n++;
    ps[n].name = "phonemes-int8";
    for (int i = 0; i < 10; i++) ps[n].bytes[i] = (BYTE)phon[i];
    ps[n].len = 10; n++;
    ps[n].name = "text-ascii"; ps[n].len = (int)strlen(text); memcpy(ps[n].bytes, text, ps[n].len); n++;
    ps[n].name = "text-int32";
    for (int i = 0; i < 14; i++) ((int *)ps[n].bytes)[i] = text[i];
    ps[n].len = 56; n++;
    *count = n;
}

static const char *region_owner(void *addr, char *buf, int size) {
    HMODULE m = NULL;
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCSTR)addr, &m) && m) {
        GetModuleFileNameA(m, buf, size);
        const char *s = strrchr(buf, '\\');
        return s ? s + 1 : buf;
    }
    return "heap/other";
}

// Visit every committed, readable region, except this DLL and the scanning thread's stack
// (both hold copies of the search patterns). SEH guards against pages that vanish mid-scan.
template <typename F> static void each_region(F f) {
    MEMORY_BASIC_INFORMATION mbi, me;
    VirtualQuery(&mbi, &me, sizeof me);  // our stack's allocation
    void *skip_stack = me.AllocationBase;
    for (BYTE *p = 0; VirtualQuery(p, &mbi, sizeof mbi) == sizeof mbi; p = (BYTE *)mbi.BaseAddress + mbi.RegionSize) {
        DWORD prot = mbi.Protect & 0xff;
        bool readable = mbi.State == MEM_COMMIT && !(mbi.Protect & PAGE_GUARD) &&
                        (prot == PAGE_READONLY || prot == PAGE_READWRITE || prot == PAGE_WRITECOPY ||
                         prot == PAGE_EXECUTE_READ || prot == PAGE_EXECUTE_READWRITE);
        bool ours = mbi.AllocationBase == (void *)g_self || mbi.AllocationBase == skip_stack;
        if (readable && !ours) f((BYTE *)mbi.BaseAddress, mbi.RegionSize);
        if ((ULONG_PTR)mbi.BaseAddress + mbi.RegionSize >= 0x7fff0000) break;
    }
}

static int find_all(BYTE *base, SIZE_T size, const BYTE *pat, int len, BYTE **out, int max) {
    int n = 0;
    __try {
        for (SIZE_T i = 0; i + len <= size && n < max; i++)
            if (base[i] == pat[0] && memcmp(base + i, pat, len) == 0) out[n++] = base + i;
    } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return n;
}

static void dump_words(const char *label, BYTE *at) {
    __try {
        int *w = (int *)at;
        logf("  %s %p: %d %d %d %d %d %d %d %d | %d %d %d %d %d %d %d %d", label, at, w[0], w[1], w[2], w[3], w[4],
             w[5], w[6], w[7], w[8], w[9], w[10], w[11], w[12], w[13], w[14], w[15]);
    } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

static DWORD WINAPI scan_thread(LPVOID) {
    Pattern ps[8];
    int np;
    build_patterns(ps, &np);
    logf("scan: start");
    BYTE *hits[64];
    int nhits = 0;
    for (int k = 0; k < np; k++) {
        int found = 0;
        each_region([&](BYTE *b, SIZE_T s) {
            BYTE *h[16];
            int n = find_all(b, s, ps[k].bytes, ps[k].len, h, 16);
            for (int i = 0; i < n && found < 16; i++, found++) {
                char mod[MAX_PATH];
                MEMORY_BASIC_INFORMATION mbi;
                VirtualQuery(h[i], &mbi, sizeof mbi);
                logf("scan: %s at %p (region %p+%lx, alloc %p, %s)", ps[k].name, h[i], mbi.BaseAddress,
                     (unsigned long)mbi.RegionSize, mbi.AllocationBase, region_owner(h[i], mod, sizeof mod));
                dump_words("words", h[i]);
                if (nhits < 64) hits[nhits++] = h[i];
            }
        });
        if (!found) logf("scan: %s not found", ps[k].name);
    }
    // Who points at (or just before) each hit? Pointer tables indexed by script number show up here.
    for (int i = 0; i < nhits; i++) {
        int refs = 0;
        each_region([&](BYTE *b, SIZE_T s) {
            __try {
                for (SIZE_T j = 0; j + 4 <= s && refs < 8; j += 4) {
                    ULONG_PTR v = *(ULONG_PTR *)(b + j);
                    if (v <= (ULONG_PTR)hits[i] && (ULONG_PTR)hits[i] - v < 64) {
                        logf("scan: ref to hit %p (-%lu) from %p", hits[i], (unsigned long)((ULONG_PTR)hits[i] - v), b + j);
                        dump_words("around ref", b + j - 32);
                        refs++;
                    }
                }
            } __except (EXCEPTION_EXECUTE_HANDLER) {}
        });
    }
    logf("scan: done");
    return 0;
}

static void scan_once() {
    static LONG started = 0;
    if (InterlockedExchange(&started, 1) == 0) CloseHandle(CreateThread(NULL, 0, scan_thread, NULL, 0, NULL));
}

#define JNI_NAME(n) "_Java_facade_nativeinterface_NativeAnimationInterface_" #n
#define JNI_FN(ret, n, ...) extern "C" JNIEXPORT ret JNICALL Java_facade_nativeinterface_NativeAnimationInterface_##n(JNIEnv *env, jclass cls, __VA_ARGS__)
#define CALL(ret, n, size, types, ...) \
    ((ret(JNICALL *) types)orig(JNI_NAME(n) "@" #size))(__VA_ARGS__)

// ---- dialogue and scripts ----
JNI_FN(void, pushAnimationUScript, jint ch, jint script, jint layer, jobject act) {
    logf("pushAnimationUScript ch=%d script=%d layer=%d", ch, script, layer);
    CALL(void, pushAnimationUScript, 24, (JNIEnv *, jclass, jint, jint, jint, jobject), env, cls, ch, script, layer, act);
}
JNI_FN(void, pushMultipleAnimationScripts, jint ch, jint s1, jint s2, jint s3, jint s4, jint layer, jobject act) {
    logf("pushMultipleAnimationScripts ch=%d scripts=%d,%d,%d,%d layer=%d", ch, s1, s2, s3, s4, layer);
    if (layer == 7) {  // speech: let the line service write this line, if it's running
        int g = generated_line(ch, s1, s2, s3, s4);
        if (g > 0) { s1 = g; s2 = s3 = s4 = -1; }
    }
    CALL(void, pushMultipleAnimationScripts, 36, (JNIEnv *, jclass, jint, jint, jint, jint, jint, jint, jobject),
         env, cls, ch, s1, s2, s3, s4, layer, act);
}
JNI_FN(void, pushPoseScript, jint ch, jint script, jobject act) {
    logf("pushPoseScript ch=%d script=%d", ch, script);
    CALL(void, pushPoseScript, 20, (JNIEnv *, jclass, jint, jint, jobject), env, cls, ch, script, act);
}
JNI_FN(void, abortAnimationScripts, jint ch, jint layer) {
    logf("abortAnimationScripts ch=%d layer=%d", ch, layer);
    CALL(void, abortAnimationScripts, 16, (JNIEnv *, jclass, jint, jint), env, cls, ch, layer);
}

// ---- gestures, face, gaze ----
JNI_FN(void, doGestureAnimation, jint ch, jint gesture, jint a, jobject act) {
    logf("doGestureAnimation ch=%d gesture=%d arg=%d", ch, gesture, a);
    CALL(void, doGestureAnimation, 24, (JNIEnv *, jclass, jint, jint, jint, jobject), env, cls, ch, gesture, a, act);
}
JNI_FN(void, doFullExpressionBaseAnimation, jint ch, jint expr, jint a, jint b, jobject act) {
    logf("doFullExpressionBaseAnimation ch=%d expr=%d args=%d,%d", ch, expr, a, b);
    CALL(void, doFullExpressionBaseAnimation, 28, (JNIEnv *, jclass, jint, jint, jint, jint, jobject),
         env, cls, ch, expr, a, b, act);
}
JNI_FN(void, doFullExpressionMoodAnimation, jint ch, jint expr, jint a, jint b, jobject act) {
    logf("doFullExpressionMoodAnimation ch=%d expr=%d args=%d,%d", ch, expr, a, b);
    CALL(void, doFullExpressionMoodAnimation, 28, (JNIEnv *, jclass, jint, jint, jint, jint, jobject),
         env, cls, ch, expr, a, b, act);
}
JNI_FN(void, setGazeTracking, jint ch, jint target, jfloat x, jfloat y, jfloat z, jint a, jint b, jint c) {
    logf("setGazeTracking ch=%d target=%d at=(%.0f,%.0f,%.0f) args=%d,%d,%d", ch, target, x, y, z, a, b, c);
    CALL(void, setGazeTracking, 40, (JNIEnv *, jclass, jint, jint, jfloat, jfloat, jfloat, jint, jint, jint),
         env, cls, ch, target, x, y, z, a, b, c);
}
JNI_FN(void, miscLittleAction, jint ch, jint action, jint a, jfloat f) {
    logf("miscLittleAction ch=%d action=%d arg=%d f=%.2f", ch, action, a, f);
    CALL(void, miscLittleAction, 24, (JNIEnv *, jclass, jint, jint, jint, jfloat), env, cls, ch, action, a, f);
}

// ---- movement and objects ----
JNI_FN(void, walkStep, jint ch, jint a, jfloat x, jfloat y, jfloat z, jfloat f, jobject point, jobject act) {
    logf("walkStep ch=%d arg=%d to=(%.0f,%.0f,%.0f) f=%.2f", ch, a, x, y, z, f);
    CALL(void, walkStep, 40, (JNIEnv *, jclass, jint, jint, jfloat, jfloat, jfloat, jfloat, jobject, jobject),
         env, cls, ch, a, x, y, z, f, point, act);
}
JNI_FN(void, turnInPlace, jint ch, jfloat angle, jobject act) {
    logf("turnInPlace ch=%d angle=%.1f", ch, angle);
    CALL(void, turnInPlace, 20, (JNIEnv *, jclass, jint, jfloat, jobject), env, cls, ch, angle, act);
}
JNI_FN(void, doPickupObjGestureAnimation, jint ch, jint obj, jint a, jint b, jint c, jobject act) {
    logf("doPickupObjGestureAnimation ch=%d obj=%d args=%d,%d,%d", ch, obj, a, b, c);
    CALL(void, doPickupObjGestureAnimation, 32, (JNIEnv *, jclass, jint, jint, jint, jint, jint, jobject),
         env, cls, ch, obj, a, b, c, act);
}
JNI_FN(void, doPutdownObjGestureAnimation, jint ch, jint obj, jint a, jint b, jint c, jfloat x, jfloat y, jfloat z,
       jint d, jobject act) {
    logf("doPutdownObjGestureAnimation ch=%d obj=%d args=%d,%d,%d to=(%.0f,%.0f,%.0f) %d", ch, obj, a, b, c, x, y, z, d);
    CALL(void, doPutdownObjGestureAnimation, 48,
         (JNIEnv *, jclass, jint, jint, jint, jint, jint, jfloat, jfloat, jfloat, jint, jobject),
         env, cls, ch, obj, a, b, c, x, y, z, d, act);
}
JNI_FN(void, setObjectToHold, jint ch, jint obj, jint hand) {
    logf("setObjectToHold ch=%d obj=%d hand=%d", ch, obj, hand);
    CALL(void, setObjectToHold, 20, (JNIEnv *, jclass, jint, jint, jint), env, cls, ch, obj, hand);
}
JNI_FN(void, music, jint a, jint b) {
    logf("music %d %d", a, b);
    CALL(void, music, 16, (JNIEnv *, jclass, jint, jint), env, cls, a, b);
}

// ---- the player's typing, as the drama manager reads it ----
extern "C" JNIEXPORT jstring JNICALL Java_facade_nativeinterface_NativeAnimationInterface_textSensor(JNIEnv *env, jclass cls) {
    jstring s = CALL(jstring, textSensor, 8, (JNIEnv *, jclass), env, cls);
    if (s) {
        const char *t = env->GetStringUTFChars(s, NULL);
        if (t && *t) logf("textSensor \"%s\"", t);
        std::string better;
        bool rewrite = t && understood_text(t, better);  // also records the real words for the line writer
        if (t) env->ReleaseStringUTFChars(s, t);
        if (rewrite) return env->NewStringUTF(better.c_str());  // the drama manager reads the restated text
    }
    return s;
}

// ---- callbacks the engine calls into the bridge ----
__declspec(dllexport) bool AblCallbackAPI_PlayerEnteredText(char *const text) {
    logf("PlayerEnteredText \"%s\"", text ? text : "");
    static bool(*f)(char *const) = (bool (*)(char *const))orig("?AblCallbackAPI_PlayerEnteredText@@YA_NQAD@Z");
    return f(text);
}
__declspec(dllexport) bool AblCallbackAPI_ScriptFinished(int ch, int script) {
    logf("ScriptFinished ch=%d script=%d", ch, script);
    if (script == 7) restore_lines(ch);  // (the second argument is the layer; 7 = speech)
    static bool(*f)(int, int) = (bool (*)(int, int))orig("?AblCallbackAPI_ScriptFinished@@YA_NHH@Z");
    return f(ch, script);
}

#include "autopilot.cpp"
#include "inject.cpp"
