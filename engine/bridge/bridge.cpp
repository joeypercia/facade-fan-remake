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

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = h;
        InitializeCriticalSection(&g_lock);
        char exe[MAX_PATH];
        GetModuleFileNameA(NULL, exe, MAX_PATH);
        const char *base = strrchr(exe, '\\');
        base = base ? base + 1 : exe;
        _snprintf(g_proc, sizeof g_proc, "%s", _strnicmp(base, "java", 4) == 0 ? "java" : "engine");
    }
    return TRUE;
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
        if (t) env->ReleaseStringUTFChars(s, t);
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
    static bool(*f)(int, int) = (bool (*)(int, int))orig("?AblCallbackAPI_ScriptFinished@@YA_NHH@Z");
    return f(ch, script);
}
