// Generated lines: ask the line service for new words, then make the engine play them as the line
// the drama manager chose. Included by bridge.cpp (shares logf, orig, patch_iat, each_region...).
//
// Java process: report the player's text to the service (/heard); before each speech push (layer
//   7), ask /line. On a replacement, wait for the engine side's "ack", then push the one script.
//   If the service isn't running, errors, or is slow, the original push happens unchanged.
// Engine process: find the per-character script tables (pointer per record, in file order), then
//   apply each job from the service: point the table entry at the generated record, and serve the
//   generated WAV the next time the engine opens that line's voice file. Restore after the line.

#include <winhttp.h>
#include <map>
#include <vector>
#include <string>

static const char *kChar[2] = {"grace", "trip"};

static std::string inject_dir() {
    char dir[MAX_PATH];
    GetEnvironmentVariableA("LOCALAPPDATA", dir, MAX_PATH);
    return std::string(dir) + "\\FacadeFanRemake\\inject\\";
}

// ======================= Java side =======================

static bool http_post(const wchar_t *path, const std::string &body, std::string &reply, DWORD timeout_ms) {
    bool ok = false;
    HINTERNET s = WinHttpOpen(L"FacadeFanRemake", WINHTTP_ACCESS_TYPE_NO_PROXY, NULL, NULL, 0);
    HINTERNET c = s ? WinHttpConnect(s, L"127.0.0.1", 8095, 0) : NULL;
    HINTERNET r = c ? WinHttpOpenRequest(c, L"POST", path, NULL, NULL, NULL, 0) : NULL;
    if (r) {
        WinHttpSetTimeouts(r, 500, 500, 2000, (int)timeout_ms);
        if (WinHttpSendRequest(r, L"Content-Type: application/json", (DWORD)-1L, (void *)body.data(),
                               (DWORD)body.size(), (DWORD)body.size(), 0) &&
            WinHttpReceiveResponse(r, NULL)) {
            char buf[4096];
            DWORD n;
            while (WinHttpReadData(r, buf, sizeof buf, &n) && n) reply.append(buf, n);
            ok = true;
        }
    }
    if (r) WinHttpCloseHandle(r);
    if (c) WinHttpCloseHandle(c);
    if (s) WinHttpCloseHandle(s);
    return ok;
}

static std::string json_str(const char *t) {
    std::string o = "\"";
    for (; *t; t++) {
        unsigned char ch = (unsigned char)*t;
        if (ch == '"' || ch == '\\') { o += '\\'; o += (char)ch; }
        else if (ch < 0x20) { char e[8]; _snprintf(e, sizeof e, "\\u%04x", ch); o += e; }
        else if (ch < 0x80) o += (char)ch;  // the engine's text is 8-bit; keep JSON plain ASCII
    }
    return o + "\"";
}

static DWORD WINAPI heard_thread(LPVOID p) {
    std::string *body = (std::string *)p, reply;
    http_post(L"/heard", *body, reply, 1000);
    delete body;
    return 0;
}

static void report_heard(const char *text) {
    if (!text || !*text || strcmp(text, " ") == 0 || strcmp(text, "warm up nlu") == 0) return;
    std::string *body = new std::string("{\"text\":" + json_str(text) + "}");
    CloseHandle(CreateThread(NULL, 0, heard_thread, body, 0, NULL));
}

// What the player meant, as phrases the 2005 parser reads as the reactions the service's LLM picked.
// Returns false (keep the original text) if the service isn't running or is slow.
static bool understood_text(const char *text, std::string &out) {
    if (!text || !*text || strcmp(text, " ") == 0 || strcmp(text, "warm up nlu") == 0) return false;
    std::string reply;
    DWORD t0 = GetTickCount();
    if (!http_post(L"/understand", "{\"text\":" + json_str(text) + "}", reply, 1500)) return false;
    size_t f = reply.find("\"follow\": ");   // the guest asked someone to come along (or to stay)
    if (f != std::string::npos) follow_request(atoi(reply.c_str() + f + 10));
    if (reply.find("\"hide\": true") != std::string::npos) {  // scene words: the scene answers them
        logf("understand: \"%s\" goes to the scene (%lu ms)", text, GetTickCount() - t0);
        out.clear();
        return true;
    }
    size_t at = reply.find("\"text\": \"");
    if (at == std::string::npos) return false;
    for (size_t i = at + 9; i < reply.size() && reply[i] != '"'; i++) {
        if (reply[i] == '\\' && i + 1 < reply.size()) {
            char e = reply[++i];
            if (e == 'u' && i + 4 < reply.size()) { out += (char)strtol(reply.substr(i + 1, 4).c_str(), NULL, 16); i += 4; }
            else out += e == 'n' ? ' ' : e;
        } else {
            out += reply[i];
        }
    }
    logf("understand: \"%s\" -> \"%s\" (%lu ms)", text, out.c_str(), GetTickCount() - t0);
    return !out.empty() && out != text;
}

static int json_int(const std::string &reply, const char *key, int dflt = -1) {
    std::string k = std::string("\"") + key + "\": ";
    size_t at = reply.find(k);
    return at == std::string::npos ? dflt : atoi(reply.c_str() + at + k.size());
}

// A generated line is in the reply: wait for the engine side to patch its table and queue the voice
// file. Returns the script to push, or -1 if the engine didn't confirm.
static int engine_ready(int ch, const std::string &reply, std::string &perform, DWORD t0) {
    int script = json_int(reply, "script");
    if (script <= 0) return -1;
    std::string ack = inject_dir() + kChar[ch] + "_" + std::to_string(script) + ".ack";
    for (int i = 0; i < 200 && GetFileAttributesA(ack.c_str()) == INVALID_FILE_ATTRIBUTES; i++) Sleep(5);
    bool acked = DeleteFileA(ack.c_str()) != 0;
    logf("line service: generated %s #%d in %lu ms, engine %s: %s", kChar[ch], script, GetTickCount() - t0,
         acked ? "ready" : "DID NOT CONFIRM (original plays)", reply.c_str());
    size_t at;
    if ((at = reply.find("\"perform\": \"")) != std::string::npos)
        perform = reply.substr(at + 12, reply.find('"', at + 12) - (at + 12));
    return acked ? script : -1;
}

// Returns the script to push instead (one generated line), -1 to push the original, or -2: hold
// this line, a scene is on (scene.cpp). `where` is everyone's position (world.cpp); `perform` gets
// the line's performance (see perform.cpp).
static int generated_line(int ch, int s1, int s2, int s3, int s4, const std::string &where, std::string &perform) {
    if (ch < 0 || ch > 1 || s1 <= 0) return -1;
    char head[128];
    _snprintf(head, sizeof head, "{\"char\":%d,\"scripts\":[%d,%d,%d,%d],\"where\":", ch, s1, s2, s3, s4);
    std::string body = head + where + "}";
    logf("line service: asking %s", body.c_str());
    std::string reply;
    DWORD t0 = GetTickCount();
    if (!http_post(L"/line", body, reply, 6500)) return -1;  // service down or overloaded: originals
    if (reply.find("\"hold\": true") != std::string::npos) {
        logf("line service: hold [%d,%d,%d,%d]: a scene is on", s1, s2, s3, s4);
        return -2;
    }
    if (reply.find("\"replace\": true") == std::string::npos) {
        logf("line service: keep original [%d,%d,%d,%d] (%lu ms) %s", s1, s2, s3, s4, GetTickCount() - t0, reply.c_str());
        return -1;
    }
    return engine_ready(ch, reply, perform, t0);
}

// ======================= engine side =======================

struct Table { int **slots = NULL; int count = 0; std::map<int, int> order; };  // script -> record order
static Table g_tables[2];
static std::map<std::pair<int, int>, std::string> g_pending_wav;     // (char, file number) -> our WAV
static std::vector<std::pair<int **, int *>> g_patched[2];          // slot, original pointer
static CRITICAL_SECTION g_inject_lock;
static std::vector<void *> g_our_allocs;

static bool is_ours(void *alloc_base) {
    for (void *a : g_our_allocs) if (a == alloc_base) return true;
    return false;
}

static void *our_alloc(size_t n) {
    void *p = VirtualAlloc(NULL, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (p) g_our_allocs.push_back(p);
    return p;
}

// Read <char>Script.sbinary from disk (the engine's cwd is the game folder) and find its in-memory
// copy and the table of pointers to each record's body.
static bool find_table(int ch) {
    char path[MAX_PATH];
    _snprintf(path, MAX_PATH, "animation\\%s\\%sScript.sbinary", kChar[ch], kChar[ch]);
    HANDLE f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) { logf("inject: can't read %s", path); return false; }
    DWORD size = GetFileSize(f, NULL), got = 0;
    int *file = (int *)our_alloc(size);
    ReadFile(f, file, size, &got, NULL);
    CloseHandle(f);
    int n = (int)(got / 4);
    std::vector<int> body_off;      // token offset of each record's body (REC_START + 5)
    Table t;
    for (int i = 0; i + 1 < n; i++)
        if (file[i] == 199998) {
            if (file[i + 1] == 199999) break;
            t.order[file[i + 1]] = (int)body_off.size();
            body_off.push_back(i + 5);
        }
    if (body_off.size() < 3) return false;
    // 1. The stream in memory: match the first 64 bytes of the file.
    BYTE *stream = NULL;
    each_region([&](BYTE *b, SIZE_T s) {
        MEMORY_BASIC_INFORMATION mbi;
        VirtualQuery(b, &mbi, sizeof mbi);
        if (stream || is_ours(mbi.AllocationBase)) return;
        BYTE *h[1];
        if (find_all(b, s, (BYTE *)file, 64, h, 1)) stream = h[0];
    });
    if (!stream) return false;
    // 2. The table: three consecutive pointers to the first three record bodies.
    int *want[3];
    for (int k = 0; k < 3; k++) want[k] = (int *)(stream + body_off[k] * 4);
    int **slots = NULL;
    each_region([&](BYTE *b, SIZE_T s) {
        MEMORY_BASIC_INFORMATION mbi;
        VirtualQuery(b, &mbi, sizeof mbi);
        if (slots || is_ours(mbi.AllocationBase)) return;
        BYTE *h[4];
        int found = find_all(b, s, (BYTE *)want, sizeof want, h, 4);
        if (found) slots = (int **)h[0];
    });
    if (!slots) { logf("inject: %s stream at %p but no table", kChar[ch], stream); return false; }
    // Check the last record too, so we know the table covers the whole file.
    size_t last = body_off.size() - 1;
    bool full = slots[last] == (int *)(stream + body_off[last] * 4);
    t.slots = slots;
    t.count = (int)body_off.size();
    g_tables[ch] = t;
    logf("inject: %s table %p (%d records, last entry %s), stream %p", kChar[ch], slots, t.count,
         full ? "matches" : "DOES NOT MATCH", stream);
    return full;
}

static void apply_job(const std::string &dir, const std::string &name) {
    // name: <char>_<script>.job
    int ch = name.rfind("grace_", 0) == 0 ? 0 : name.rfind("trip_", 0) == 0 ? 1 : -1;
    if (ch < 0) return;
    int script = atoi(name.c_str() + strlen(kChar[ch]) + 1);
    std::string stem = dir + kChar[ch] + "_" + std::to_string(script);
    char line[600];
    int file_no = -1;
    std::string wav;
    FILE *jf = fopen((stem + ".job").c_str(), "r");
    if (!jf) return;
    while (fgets(line, sizeof line, jf)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, "file=", 5)) file_no = atoi(line + 5);
        if (!strncmp(line, "wav=", 4)) wav = line + 4;
    }
    fclose(jf);
    DeleteFileA((stem + ".job").c_str());
    Table &t = g_tables[ch];
    auto it = t.order.find(script);
    HANDLE f = CreateFileA((stem + ".tok").c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (!t.slots || it == t.order.end() || f == INVALID_HANDLE_VALUE || file_no < 0) {
        if (f != INVALID_HANDLE_VALUE) CloseHandle(f);
        logf("inject: can't apply %s #%d (table %s)", kChar[ch], script, t.slots ? "ok" : "not found");
        return;
    }
    DWORD size = GetFileSize(f, NULL), got = 0;
    int *rec = (int *)VirtualAlloc(NULL, size + 64, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    ReadFile(f, rec, size, &got, NULL);
    CloseHandle(f);
    // The engine counts a record's alternative performances by scanning forward to the next
    // record's start marker (animEngineDLL+0x16376), so ours must be followed by one, as in the file.
    rec[got / 4] = 199998;
    rec[got / 4 + 1] = 199999;
    DeleteFileA((stem + ".tok").c_str());
    EnterCriticalSection(&g_inject_lock);
    int **slot = &t.slots[it->second];
    g_patched[ch].push_back({slot, *slot});
    *slot = rec + 5;  // the table points at the body, just after [REC_START, script, h0, h1, h2]
    g_pending_wav[{ch, file_no}] = wav;
    LeaveCriticalSection(&g_inject_lock);
    FILE *ack = fopen((stem + ".ack").c_str(), "w");
    if (ack) fclose(ack);
    logf("inject: %s #%d patched (record %d), voice file %d -> %s", kChar[ch], script, it->second, file_no, wav.c_str());
}

// The engine wants a voice file: serve the generated one if a job queued it.
static const char *redirect_wav(const char *name, std::string &keep) {
    if (!name) return name;
    const char *p = strstr(name, "sounds/");
    if (!p) p = strstr(name, "sounds\\");
    if (!p) return name;
    int ch = strstr(p, "grace") == p + 7 ? 0 : strstr(p, "trip") == p + 7 ? 1 : -1;
    const char *num = strrchr(name, '/');
    if (!num) num = strrchr(name, '\\');
    if (ch < 0 || !num) return name;
    int file_no = atoi(num + 1);
    EnterCriticalSection(&g_inject_lock);
    auto it = g_pending_wav.find({ch, file_no});
    bool hit = it != g_pending_wav.end();
    if (hit) { keep = it->second; g_pending_wav.erase(it); }
    LeaveCriticalSection(&g_inject_lock);
    if (!hit) return name;
    logf("inject: voice %s -> generated %s", name, keep.c_str());
    return keep.c_str();
}

// ---- subtitles last as long as the voice ----
// The engine times a subtitle by its text, not its audio (animEngineDLL v1.1, 0x1009DD90): it types the
// line out word by word at 0.75 frames per character, holds it 60 frames, then clears it. That fits
// the actors' pacing; generated speech runs longer, and its subtitle went before the words did. So
// while a character's voice file is still playing, the hold's start (the frame it began) is kept at
// the current frame. The character objects are the engine's globals at 0x1097947C (Grace) and
// 0x10979480 (Trip); in each, the subtitle text is at +0x13E1C, the shown range at +0x1411C/+0x14120,
// and the frame the current step started at +0x14124. The frame counter is 0x1083F62C.
static BYTE *g_eng_base;
static volatile DWORD g_voice_until[2];
static bool g_hold_logged[2];

static void note_voice(const char *name, const char *opened) {
    const char *p = name ? strstr(name, "sounds") : NULL;
    if (!p || !opened) return;
    int ch = strstr(p, "grace") == p + 7 ? 0 : strstr(p, "trip") == p + 7 ? 1 : -1;
    WIN32_FILE_ATTRIBUTE_DATA fa;
    if (ch < 0 || !GetFileAttributesExA(opened, GetFileExInfoStandard, &fa)) return;
    DWORD ms = (DWORD)((fa.nFileSizeLow > 44 ? fa.nFileSizeLow - 44 : 0) / 44.1);  // 22.05 kHz 16-bit mono
    g_voice_until[ch] = GetTickCount() + ms + 200;
}

static void hold_subtitles() {
    if (!g_eng_base) return;
    DWORD now = GetTickCount();
    for (int ch = 0; ch < 2; ch++) {
        if ((LONG)(now - g_voice_until[ch]) >= 0) continue;
        __try {
            BYTE *obj = *(BYTE **)(g_eng_base + 0x97947C + 4 * ch);
            if (!obj) continue;
            const char *text = (const char *)(obj + 0x13E1C);
            int len = (int)strnlen(text, 512), a = *(int *)(obj + 0x1411C), b = *(int *)(obj + 0x14120);
            if (len > 0 && b == len && a < len) {  // all of it shown, holding: start the hold over
                *(int *)(obj + 0x14124) = *(int *)(g_eng_base + 0x83F62C);
                if (!g_hold_logged[ch]) { g_hold_logged[ch] = true; logf("inject: holding %s's subtitle \"%.40s\"", kChar[ch], text); }
            }
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            g_eng_base = NULL;   // the layout isn't what we expect: stop touching it
            logf("inject: subtitle hold disabled (unexpected memory layout)");
            return;
        }
    }
}

static void restore_lines(int ch) {
    if (ch < 0 || ch > 1) return;
    EnterCriticalSection(&g_inject_lock);
    for (auto &p : g_patched[ch]) *p.first = p.second;
    if (!g_patched[ch].empty()) logf("inject: restored %u %s record(s)", (unsigned)g_patched[ch].size(), kChar[ch]);
    g_patched[ch].clear();
    LeaveCriticalSection(&g_inject_lock);
}

static DWORD WINAPI inject_thread(LPVOID) {
    std::string dir = inject_dir();
    CreateDirectoryA(dir.substr(0, dir.size() - strlen("inject\\")).c_str(), NULL);  // ...\FacadeFanRemake
    CreateDirectoryA(dir.c_str(), NULL);
    // Find the tables once the engine has loaded its data (a few seconds after start).
    for (int tries = 0; tries < 60 && !(g_tables[0].slots && g_tables[1].slots); tries++) {
        Sleep(2000);
        for (int ch = 0; ch < 2; ch++) if (!g_tables[ch].slots) find_table(ch);
    }
    logf("inject: ready (grace %s, trip %s)", g_tables[0].slots ? "ok" : "MISSING", g_tables[1].slots ? "ok" : "MISSING");
    for (;;) {
        WIN32_FIND_DATAA fd;
        HANDLE h = FindFirstFileA((dir + "*.job").c_str(), &fd);
        if (h != INVALID_HANDLE_VALUE) {
            do apply_job(dir, fd.cFileName);
            while (FindNextFileA(h, &fd));
            FindClose(h);
        }
        hold_subtitles();
        Sleep(5);
    }
}

static void inject_attach(HMODULE eng) {
    // hold_subtitles' globals must lie inside the engine's image (v1.1 has them; another build might not).
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)((BYTE *)eng + ((IMAGE_DOS_HEADER *)eng)->e_lfanew);
    if (nt->OptionalHeader.SizeOfImage > 0x9BE97C) g_eng_base = (BYTE *)eng;
    logf("inject: subtitle hold %s", g_eng_base ? "on" : "off (unknown engine build)");
    InitializeCriticalSection(&g_inject_lock);
    CloseHandle(CreateThread(NULL, 0, inject_thread, NULL, 0, NULL));
}
