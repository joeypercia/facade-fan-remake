"""Façade's own 2005 parser, kept running beside the line service (tools/nlu_probe/NluProbe.java).

It reads the guest's actual words the way the game would: Agree, Praise, ReferTo paintings, Explain
angry... The act picker (acts.py) shows that reading to the model, which keeps what's right and drops
what a word's other meaning set off ("now we're talking" = "you're communicative").

Needs a JDK (JAVA_HOME, else C:\\Program Files\\Java\\jdk-21) and the game (FACADE_DIR). The parser's
support files are copied to %LOCALAPPDATA%\\FacadeFanRemake\\nlu_probe, never into the repo.
"""
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

FACADE = Path(os.environ.get("FACADE_DIR", r"C:\Program Files (x86)\Facade"))
JDK = Path(os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-21"))
CLASSES = FACADE / "util" / "classes"
SOURCES = FACADE / "util" / "sources" / "facade"
WORK = Path(os.environ["LOCALAPPDATA"]) / "FacadeFanRemake" / "nlu_probe"
PROBE = Path(__file__).resolve().parents[2] / "tools" / "nlu_probe"


def build():
    """Compile NluProbe (and the native-interface stand-in) and copy the parser's support files."""
    (WORK / "nlu" / "templates").mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCES / "scriptlib.clp", WORK)
    for f in ("TemplateSupport.jess", "wordnet.bin"):
        shutil.copy(SOURCES / "nlu" / "templates" / f, WORK / "nlu" / "templates")
    subprocess.run([str(JDK / "bin" / "javac"), "-nowarn", "-encoding", "UTF-8", "-cp", str(CLASSES),
                    "-d", str(WORK), str(PROBE / "NluProbe.java"),
                    str(PROBE / "stub" / "facade" / "nativeinterface" / "NativeAnimationInterface.java")],
                   check=True, capture_output=True)


def command(lines_file="-"):
    return [str(JDK / "bin" / "java"), "-cp", f"{WORK}{os.pathsep}{CLASSES}", "NluProbe", str(SOURCES),
            lines_file, "DummyBeat"]


def parse_reply(reply):
    """"text\\tExplain grace angry; Agree grace" -> [("Explain", ["grace", "angry"]), ("Agree", ["grace"])]."""
    acts = reply.split("\t", 1)[1] if "\t" in reply else "-"
    if acts.strip() in ("-", "") or acts.startswith("ERROR"):
        return []
    out = []
    for da in acts.split("; "):
        f = da.split()
        out.append((f[0], f[1:]))
    return out


class Parser:
    """One parser process; read() is thread-safe. If it can't start, read() returns None."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.replies = queue.Queue()
        try:
            build()
            self.proc = subprocess.Popen(command(), cwd=WORK, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._pump, daemon=True).start()
            if self.replies.get(timeout=60) != "READY":
                raise RuntimeError("no READY")
        except (OSError, subprocess.SubprocessError, queue.Empty, RuntimeError) as e:
            print(f"Façade's parser didn't start ({e}); the act picker works without it", flush=True)
            self.close()

    def _pump(self):
        for line in self.proc.stdout:
            self.replies.put(line.rstrip("\n"))

    def read(self, text, timeout=0.5):
        """Façade's reading of the guest's words, as [(type, params)], or None if it's unavailable."""
        if not self.proc or self.proc.poll() is not None:
            return None
        text = " ".join(text.replace("\t", " ").split())
        with self.lock:
            while not self.replies.empty():
                self.replies.get_nowait()
            try:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
                return parse_reply(self.replies.get(timeout=timeout))
            except (OSError, queue.Empty):
                return None

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None
