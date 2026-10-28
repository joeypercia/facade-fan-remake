// Where everyone is (Java side): positions sent with each line request, so the line service knows
// where the guest is (by the bar, at the door, out in the hallway). Included by bridge.cpp.
//
// Positions come from the engine's own getObjectPosition (x, z on the floor; y is height).

struct Landmark { const char *name; int sprite; };
static const Landmark kLandmarks[] = {
    {"player", 2}, {"grace", 0}, {"trip", 1}, {"door", 47}, {"elevator", 48}, {"bar", 7}, {"couch", 3},
    {"window", 46}, {"worktable", 5}, {"wedding_picture", 38}, {"italy_picture", 39}, {"painting", 34},
    {"phone", 10},
};

static bool world_pos(JNIEnv *env, int sprite, float &x, float &z) {
    static jclass pt_cls;
    static jmethodID pt_init;
    static jfieldID fx, fz;
    if (!pt_cls) {
        jclass c = env->FindClass("facade/util/Point3D");
        if (!c) { env->ExceptionClear(); return false; }
        pt_cls = (jclass)env->NewGlobalRef(c);
        pt_init = env->GetMethodID(pt_cls, "<init>", "()V");
        fx = env->GetFieldID(pt_cls, "x", "F");
        fz = env->GetFieldID(pt_cls, "z", "F");
    }
    jobject p = env->NewObject(pt_cls, pt_init);
    if (!p) { env->ExceptionClear(); return false; }
    jclass nai = env->FindClass("facade/nativeinterface/NativeAnimationInterface");
    if (!nai) { env->ExceptionClear(); env->DeleteLocalRef(p); return false; }
    CALL(void, getObjectPosition, 16, (JNIEnv *, jclass, jint, jobject), env, nai, sprite, p);
    x = env->GetFloatField(p, fx);
    z = env->GetFloatField(p, fz);
    env->DeleteLocalRef(p);
    env->DeleteLocalRef(nai);
    return true;
}

// ---- following the guest ----
// The drama manager stages Grace and Trip around the living room and never follows a guest who
// wanders off. When the guest is well away from both and the drama manager isn't walking the nearer
// one, we walk that one over (walkStep, re-issued every half second as the drama manager does),
// stopping a conversation's distance short. Any walk of the drama manager's own wins: we back off.
static const float kFollowFrom = 170;   // start following when the guest is this far from both
static const float kStopShort = 70;     // stop this far from the guest
static DWORD g_dm_walk[2];              // tick of the drama manager's last walkStep per character
static jobject g_dm_walk_act[2];        // and its Walk action (global ref), to see when it's done
static int g_following = -1;

static void world_saw_walk(JNIEnv *env, int ch, jobject act) {
    if (ch != 0 && ch != 1) return;
    g_dm_walk[ch] = GetTickCount();
    if (g_dm_walk_act[ch]) env->DeleteGlobalRef(g_dm_walk_act[ch]);
    g_dm_walk_act[ch] = act ? env->NewGlobalRef(act) : NULL;
}

// Is the drama manager's last walk for this character still going? Replacing a walk it's waiting on
// fails that Walk action, and its staging behaviours don't survive that (the drama manager died).
static bool dm_walking(JNIEnv *env, int ch) {
    if (GetTickCount() - g_dm_walk[ch] < 2500) return true;
    if (!g_dm_walk_act[ch]) return false;
    static jfieldID status;
    if (!status) {
        jclass pa = env->FindClass("abl/runtime/PrimitiveAction");
        if (!pa) { env->ExceptionClear(); return true; }
        status = env->GetFieldID(pa, "completionStatus", "I");
        if (!status) { env->ExceptionClear(); return true; }
    }
    return env->GetIntField(g_dm_walk_act[ch], status) == 0;  // 0 running, 1 succeeded, 2 failed
}

// The front door and which way is in (toward the couch), read once.
static float g_door[2], g_in[2];
static bool door_known(JNIEnv *env) {
    if (g_in[0] || g_in[1]) return true;
    float cx, cz;
    if (!world_pos(env, 47, g_door[0], g_door[1]) || !world_pos(env, 3, cx, cz)) return false;
    float ix = cx - g_door[0], iz = cz - g_door[1], len = hypotf(ix, iz);
    if (len < 1) return false;
    g_in[0] = ix / len;
    g_in[1] = iz / len;
    return true;
}
// How far a point is past the front door toward the living room (negative: out in the hallway).
static float past_door(float x, float z) { return (x - g_door[0]) * g_in[0] + (z - g_door[1]) * g_in[1]; }

// The guest has come in: they start out in the hallway, and the intro stages Trip and Grace behind
// the closed door. "Come in" = seen out in the hallway, then well past the door for 3 s running
// (positions before the game places the guest are meaningless).
static bool g_came_in;
static void check_came_in(JNIEnv *env, float px, float pz) {
    static DWORD since;
    static bool seen_out;
    if (g_came_in || !door_known(env) || (px == 0 && pz == 0)) return;
    if (past_door(px, pz) < -20) seen_out = true;
    if (!seen_out || past_door(px, pz) < 100) { since = 0; return; }
    if (!since) since = GetTickCount();
    if (GetTickCount() - since > 3000) {
        g_came_in = true;
        logf("follow: the guest has come in; following on");
    }
}

static bool env_off(const char *name) {
    char v[4];
    return GetEnvironmentVariableA(name, v, sizeof v) > 0;
}

static void follow_tick(JNIEnv *env) {
    static DWORD last;
    static bool off = env_off("FFR_NO_FOLLOW");
    DWORD now = GetTickCount();
    if (off || now - last < 500) return;
    last = now;
    float px, pz, c[2][2];
    if (!world_pos(env, 2, px, pz) || !world_pos(env, 0, c[0][0], c[0][1]) || !world_pos(env, 1, c[1][0], c[1][1]))
        return;
    check_came_in(env, px, pz);
    if (!g_came_in) return;
    float d[2];
    for (int k = 0; k < 2; k++) d[k] = hypotf(c[k][0] - px, c[k][1] - pz);
    int ch = d[0] < d[1] ? 0 : 1;
    bool busy = dm_walking(env, ch);
    if (d[ch] < kFollowFrom * (g_following == ch ? 0.6f : 1.0f) || busy) {
        if (g_following >= 0) logf("follow: %s stops (guest %.0f away%s)", g_following ? "trip" : "grace", d[ch],
                                   busy ? ", drama manager walking" : "");
        g_following = -1;
        return;
    }
    float k = (d[ch] - kStopShort) / d[ch];
    float tx = c[ch][0] + (px - c[ch][0]) * k, tz = c[ch][1] + (pz - c[ch][1]) * k;
    jclass walk_cls = env->FindClass("facade/primact/Walk");
    jclass pt_cls = env->FindClass("facade/util/Point3D");
    jclass nai = env->FindClass("facade/nativeinterface/NativeAnimationInterface");
    if (!walk_cls || !pt_cls || !nai) { env->ExceptionClear(); return; }
    jobject act = env->NewObject(walk_cls, env->GetMethodID(walk_cls, "<init>", "()V"));
    jobject pt = env->NewObject(pt_cls, env->GetMethodID(pt_cls, "<init>", "()V"));
    if (!act || !pt) { env->ExceptionClear(); return; }
    if (g_following != ch) logf("follow: %s follows the guest (%.0f away) to (%.0f,%.0f)", ch ? "trip" : "grace", d[ch], tx, tz);
    g_following = ch;
    CALL(void, walkStep, 40, (JNIEnv *, jclass, jint, jint, jfloat, jfloat, jfloat, jfloat, jobject, jobject),
         env, nai, ch, 0, tx, 0.0f, tz, -666.0f, pt, act);
    env->DeleteLocalRef(act);
    env->DeleteLocalRef(pt);
    env->DeleteLocalRef(walk_cls);
    env->DeleteLocalRef(pt_cls);
    env->DeleteLocalRef(nai);
}

// ---- the doorway, as the drama manager sees it ----
// The drama manager treats a guest out in the hallway as leaving: warnings, then it throws them out
// and ends the evening. So while the guest is out there but still near the door, the positions it
// reads put them just inside the doorway; its walks and staging then keep to the doorway, and our
// following (which reads the real position) brings someone out. Walking on toward the elevator
// shows the real position again, so leaving still ends the evening as in the original.
static const float kNearDoor = 250;     // hallway within this of the door counts as "at the doorway"
static const float kJustInside = 110;   // where the drama manager sees them (clear of its leave test)

static bool disguise_player(JNIEnv *env, float &x, float &z) {
    static bool off = env_off("FFR_NO_DISGUISE");
    if (off || !g_came_in || !door_known(env)) return false;
    if (past_door(x, z) > 60 || hypotf(x - g_door[0], z - g_door[1]) > kNearDoor) return false;
    static DWORD logged;
    if (GetTickCount() - logged > 5000) {
        logged = GetTickCount();
        logf("doorway: guest at (%.0f,%.0f) shown to the drama manager just inside", x, z);
    }
    x = g_door[0] + g_in[0] * kJustInside;
    z = g_door[1] + g_in[1] * kJustInside;
    return true;
}

// The drama manager's own console output (exceptions, its "##" debug lines) has nowhere to go: send it
// to %LOCALAPPDATA%\FacadeFanRemake\java.log.
static void java_log_init(JNIEnv *env) {
    static bool done;
    if (done) return;
    done = true;
    char path[MAX_PATH];
    GetEnvironmentVariableA("LOCALAPPDATA", path, MAX_PATH);
    strcat(path, "\\FacadeFanRemake\\java.log");
    jclass fos = env->FindClass("java/io/FileOutputStream"), ps = env->FindClass("java/io/PrintStream"),
           sys = env->FindClass("java/lang/System");
    if (!fos || !ps || !sys) { env->ExceptionClear(); return; }
    jobject f = env->NewObject(fos, env->GetMethodID(fos, "<init>", "(Ljava/lang/String;)V"), env->NewStringUTF(path));
    jobject p = f ? env->NewObject(ps, env->GetMethodID(ps, "<init>", "(Ljava/io/OutputStream;Z)V"), f, JNI_TRUE) : NULL;
    if (!p) { env->ExceptionClear(); return; }
    env->CallStaticVoidMethod(sys, env->GetStaticMethodID(sys, "setErr", "(Ljava/io/PrintStream;)V"), p);
    env->CallStaticVoidMethod(sys, env->GetStaticMethodID(sys, "setOut", "(Ljava/io/PrintStream;)V"), p);
    env->ExceptionClear();
    logf("java: console -> %s", path);
}

// {"player":[x,z],"grace":[x,z],...}
static std::string world_json(JNIEnv *env) {
    std::string o = "{";
    for (const Landmark &l : kLandmarks) {
        float x, z;
        if (!world_pos(env, l.sprite, x, z)) continue;
        char b[96];
        _snprintf(b, sizeof b, "%s\"%s\":[%.0f,%.0f]", o.size() > 1 ? "," : "", l.name, x, z);
        o += b;
    }
    return o + "}";
}
