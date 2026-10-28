// Scenes: the story paused while the guest takes the evening somewhere new. Included by bridge.cpp
// after world.cpp (uses http_post, engine_ready, new_action, perf_*, follow_tick, world_json).
//
// When the line service answers a speech push with "hold" (see engine/service/improv.py), the
// character's drama-manager thread stays here, inside its push, so its behaviours (and the beat it is
// in) can't move on. Grace and Trip are separate threads: the other one is held at their own next
// line. Meanwhile this loop plays the scene: it asks /scene what happens next and pushes the scene
// lines itself, with fresh action objects. When the service says the scene is over, the held push
// asks /line again and the story line plays, bridged from the scene.
//
// Also here: the front door. Trip shuts it with a right-arm gesture; with the guest out in the
// hallway that locked them out for good, so while they aren't inside the gesture is swapped for
// his resting arm and the door stays open.

static volatile LONG g_scene;   // 1 while a scene runs (one loop, on whichever thread got there first)

static bool scene_on() { return g_scene != 0; }

// When each character's speech last started (this process) and finished (engine process, via a
// small shared block): a scene line must never cut off a line that is still playing.
struct Shared { volatile LONG finished[2]; };
static Shared *shared() {
    static Shared *s;
    if (!s) {
        HANDLE h = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, sizeof(Shared),
                                      "Local\\FacadeFanRemakeShared");
        if (h) s = (Shared *)MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Shared));
    }
    return s;
}
static volatile LONG g_spoke[2];
static void speech_started(int ch) { if (ch == 0 || ch == 1) g_spoke[ch] = (LONG)GetTickCount(); }
static void speech_finished(int ch) { if ((ch == 0 || ch == 1) && shared()) shared()->finished[ch] = (LONG)GetTickCount(); }
static bool speaking(int ch) {
    LONG start = g_spoke[ch], now = (LONG)GetTickCount();
    if (!start || now - start > 30000) return false;
    return !shared() || shared()->finished[ch] - start < 0;
}

static void scene_hold(JNIEnv *env, jclass cls) {
    if (InterlockedCompareExchange(&g_scene, 1, 0) != 0) {
        // The scene is running on the other character's thread: this one's story waits too.
        DWORD t0 = GetTickCount();
        while (g_scene && GetTickCount() - t0 < 360000) Sleep(100);
        return;
    }
    DWORD start = GetTickCount(), busy_until = 0, last_ask = 0;
    logf("scene: the story is paused");
    int fails = 0, lines = 0;
    while (GetTickCount() - start < 300000 && fails < 8) {
        follow_tick(env);
        perf_due(env, cls);
        DWORD now = GetTickCount();
        if ((LONG)(now - busy_until) < 0 || now - last_ask < 300) { Sleep(50); continue; }
        last_ask = now;
        std::string free_ = "[";  // who can talk now (not in the middle of a line)
        if (!speaking(0)) free_ += "0";
        if (!speaking(1)) free_ += free_.size() > 1 ? ",1" : "1";
        std::string reply, body = "{\"free\":" + free_ + "],\"where\":" + world_json(env) + "}";
        DWORD t0 = GetTickCount();
        if (!http_post(L"/scene", body, reply, 9000)) { fails++; continue; }
        if (reply.find("\"end\": true") != std::string::npos) break;
        if (reply.find("\"say\": true") == std::string::npos) {
            if (reply.find("\"wait\": true") == std::string::npos) fails++;  // an error: don't wait forever
            continue;
        }
        fails = 0;
        int ch = json_int(reply, "char"), ms = json_int(reply, "ms", 2500);
        std::string perform;
        int script = (ch == 0 || ch == 1) ? engine_ready(ch, reply, perform, t0) : -1;
        jobject act = script > 0 ? new_action(env, "facade/primact/PushMultipleAnimationScripts") : NULL;
        if (!act) continue;
        speech_started(ch);
        CALL(void, pushMultipleAnimationScripts, 36, (JNIEnv *, jclass, jint, jint, jint, jint, jint, jint, jobject),
             env, cls, ch, script, -1, -1, -1, 7, act);
        env->DeleteLocalRef(act);
        if (!perform.empty()) perf_start(env, cls, ch, perform.c_str());
        busy_until = GetTickCount() + ms + 600;
        lines++;
    }
    // Let the last scene line finish before the story line goes on top of it.
    while ((LONG)(GetTickCount() - busy_until) < 0) { perf_due(env, cls); Sleep(50); }
    logf("scene: over after %lu s, %d line(s)%s; the story resumes", (GetTickCount() - start) / 1000, lines,
         fails >= 8 ? " (line service not answering)" : "");
    InterlockedExchange(&g_scene, 0);
}

static bool door_guard(JNIEnv *env, int ch, int part, jint &script) {
    if (ch != 1 || part != 1 || script != 42) return false;  // Trip's right arm shutting the front door
    float px, pz;
    if (!door_known(env) || !world_pos(env, 2, px, pz) || (px == 0 && pz == 0)) return false;
    if (past_door(px, pz) > 60) return false;  // the guest is inside: shut it as usual
    logf("door: guest at (%.0f,%.0f) isn't inside; Trip leaves the front door open", px, pz);
    script = kArmRest[1];
    return true;
}
