// LLM-directed performance (Java process). Included by bridge.cpp after inject.cpp.
//
// With each generated line the line service also picks how it's performed: a facial expression, an
// arm or body gesture landing on a chosen word, and where the character looks. The drama manager's
// own behaviours keep running; these choices sit on top of them:
// - a gesture only goes on arms the drama manager has left at rest (never over a drink, a door or
//   another gesture), and a body gesture (nod, sigh) is a one-shot on its own layer;
// - when the line ends, whatever is still ours is put back: arms to rest, the drama manager's last
//   expression and gaze. Anything the drama manager changed meanwhile is left alone.
// Calls are made from the drama manager's own threads (inside its calls into this DLL), with fresh
// action objects, so the original code sees them as ordinary actions.

#include <deque>

enum { kArmL, kArmR, kArmsBoth, kBody };
static const int kArmRest[2] = {98, 64};  // egraceScript_ / etripScript_layer_arm_default

struct PerfState {
    int arm[3] = {-1, -1, -1};      // the drama manager's last gesture per arm part (-1: none yet)
    int ours[3] = {0, 0, 0};        // our gesture still showing on that part
    int face[3] = {-1, -1, -1};     // its last base expression call (expr, a, b)
    bool our_face = false;
    int gaze[4] = {2, 1, 0, 1};     // its last gaze call (target, a, b, c); starts on the player
    bool our_gaze = false;
};
static PerfState g_perf[2];

struct PerfAct { DWORD at; int ch; char kind; int a, b; };  // kind: 'g' gesture, 'e' end of line
static std::deque<PerfAct> g_perf_queue;
static CRITICAL_SECTION g_perf_lock;
static LONG g_perf_init = 0;

static void perf_lock_init() {
    if (InterlockedCompareExchange(&g_perf_init, 1, 0) == 0) InitializeCriticalSection(&g_perf_lock);
    while (g_perf_init != 1) Sleep(0);
}

static jobject new_action(JNIEnv *env, const char *cls) {
    jclass c = env->FindClass(cls);
    jobject o = NULL;
    if (c) {
        jmethodID init = env->GetMethodID(c, "<init>", "()V");
        if (init) o = env->NewObject(c, init);
        env->DeleteLocalRef(c);
    }
    if (env->ExceptionCheck()) { env->ExceptionClear(); o = NULL; }
    return o;
}

static bool arm_free(int ch, int part) {
    auto rest = [&](int p) { return g_perf[ch].arm[p] == -1 || g_perf[ch].arm[p] == kArmRest[ch]; };
    if (part == kArmsBoth) return rest(kArmL) && rest(kArmR) && rest(kArmsBoth);
    return rest(part) && rest(kArmsBoth);
}

static void do_gesture(JNIEnv *env, jclass cls, int ch, int part, int script) {
    jobject act = new_action(env, part == kBody ? "facade/primact/PushMultipleAnimationScripts"
                                                : "facade/primact/DoGestureAnimation");
    if (!act) return;
    if (part == kBody)
        CALL(void, pushMultipleAnimationScripts, 36, (JNIEnv *, jclass, jint, jint, jint, jint, jint, jint, jobject),
             env, cls, ch, script, -1, -1, -1, 3, act);
    else
        CALL(void, doGestureAnimation, 24, (JNIEnv *, jclass, jint, jint, jint, jobject), env, cls, ch, part, script, act);
    env->DeleteLocalRef(act);
}

static void do_face(JNIEnv *env, jclass cls, int ch, int expr, int a, int b) {
    jobject act = new_action(env, "facade/primact/DoFullExpressionBaseAnimation");
    if (!act) return;
    CALL(void, doFullExpressionBaseAnimation, 28, (JNIEnv *, jclass, jint, jint, jint, jint, jobject),
         env, cls, ch, expr, a, b, act);
    env->DeleteLocalRef(act);
}

static void do_gaze(JNIEnv *env, jclass cls, int ch, int target, int a, int b, int c) {
    CALL(void, setGazeTracking, 40, (JNIEnv *, jclass, jint, jint, jfloat, jfloat, jfloat, jint, jint, jint),
         env, cls, ch, target, 0.f, 0.f, 0.f, a, b, c);
}

// The line is over: put back whatever is still ours.
static void perf_end(JNIEnv *env, jclass cls, int ch) {
    PerfState &s = g_perf[ch];
    for (int p = kArmL; p <= kArmsBoth; p++)
        if (s.ours[p]) { do_gesture(env, cls, ch, p, kArmRest[ch]); s.ours[p] = 0; }
    if (s.our_face) { do_face(env, cls, ch, s.face[0], s.face[1], s.face[2]); s.our_face = false; }
    if (s.our_gaze) { do_gaze(env, cls, ch, s.gaze[0], s.gaze[1], s.gaze[2], s.gaze[3]); s.our_gaze = false; }
}

// Run whatever is due. Called from the drama manager's threads on their (frequent) calls in.
static void perf_due(JNIEnv *env, jclass cls) {
    perf_lock_init();
    if (g_perf_queue.empty() || !TryEnterCriticalSection(&g_perf_lock)) return;
    DWORD now = GetTickCount();
    for (size_t i = 0; i < g_perf_queue.size();) {
        PerfAct a = g_perf_queue[i];
        if ((LONG)(now - a.at) < 0) { i++; continue; }
        g_perf_queue.erase(g_perf_queue.begin() + i);
        if (a.kind == 'e') {
            perf_end(env, cls, a.ch);
        } else if (a.a == kBody || arm_free(a.ch, a.a)) {
            do_gesture(env, cls, a.ch, a.a, a.b);
            if (a.a != kBody) g_perf[a.ch].ours[a.a] = a.b;
            logf("perform: %s gesture part=%d script=%d", kChar[a.ch], a.a, a.b);
        } else {
            logf("perform: %s gesture part=%d skipped (arm busy: %d,%d,%d)", kChar[a.ch], a.a,
                 g_perf[a.ch].arm[0], g_perf[a.ch].arm[1], g_perf[a.ch].arm[2]);
        }
    }
    LeaveCriticalSection(&g_perf_lock);
}

// A generated line was just pushed. `spec` is the service's "perform" field:
// "face=<expr> look=<sprite> gesture=<part>,<script> at=<ms> end=<ms>" (-1: no change).
static void perf_start(JNIEnv *env, jclass cls, int ch, const char *spec) {
    int face = -1, look = -1, part = -1, script = -1, at = 0, end = 0;
    const char *p;
    if ((p = strstr(spec, "face="))) face = atoi(p + 5);
    if ((p = strstr(spec, "look="))) look = atoi(p + 5);
    if ((p = strstr(spec, "gesture="))) sscanf(p + 8, "%d,%d", &part, &script);
    if ((p = strstr(spec, "at="))) at = atoi(p + 3);
    if ((p = strstr(spec, "end="))) end = atoi(p + 4);
    if (end <= 0) return;
    perf_lock_init();
    EnterCriticalSection(&g_perf_lock);
    // A new line from this character: finish the previous one's performance first.
    for (size_t i = 0; i < g_perf_queue.size();)
        if (g_perf_queue[i].ch == ch) g_perf_queue.erase(g_perf_queue.begin() + i); else i++;
    perf_end(env, cls, ch);
    DWORD now = GetTickCount();
    if (face >= 0) { do_face(env, cls, ch, face, -1, -1); g_perf[ch].our_face = true; }
    if (look >= 0) { do_gaze(env, cls, ch, look, 1, 0, 1); g_perf[ch].our_gaze = true; }
    if (part >= kArmL && part <= kBody && script > 0) g_perf_queue.push_back({now + at, ch, 'g', part, script});
    g_perf_queue.push_back({now + end, ch, 'e', 0, 0});
    LeaveCriticalSection(&g_perf_lock);
    logf("perform: %s %s", kChar[ch], spec);
    perf_due(env, cls);
}

// ---- what the drama manager itself is doing (from the wrappers in bridge.cpp) ----
static void perf_saw_gesture(int ch, int part, int script) {
    if (ch < 0 || ch > 1 || part < kArmL || part > kArmsBoth) return;
    g_perf[ch].arm[part] = script;
    g_perf[ch].ours[part] = 0;  // its gesture replaced ours: nothing of ours to put back
}
static void perf_saw_face(int ch, int expr, int a, int b) {
    if (ch < 0 || ch > 1) return;
    PerfState &s = g_perf[ch];
    s.face[0] = expr; s.face[1] = a; s.face[2] = b;
    s.our_face = false;
}
static void perf_saw_gaze(int ch, int target, int a, int b, int c) {
    if (ch < 0 || ch > 1) return;
    PerfState &s = g_perf[ch];
    s.gaze[0] = target; s.gaze[1] = a; s.gaze[2] = b; s.gaze[3] = c;
    s.our_gaze = false;
}
