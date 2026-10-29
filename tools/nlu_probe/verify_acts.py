"""Checks every act on engine/service/acts.py's menu against Façade's own parser: builds NluProbe, runs
each act's phrase (alone, addressed to Trip, and paired with another act) and reports any phrase the
parser doesn't read as that act. Needs a JDK (JAVA_HOME or C:\\Program Files\\Java\\jdk-21) and the
game installed (FACADE_DIR). The parser's support files are copied to %LOCALAPPDATA%, never the repo.

    python tools/nlu_probe/verify_acts.py [-v]
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "engine", "service"))
import acts  # noqa: E402

FACADE = os.environ.get("FACADE_DIR", r"C:\Program Files (x86)\Facade")
JDK = os.environ.get("JAVA_HOME", r"C:\Program Files\Java\jdk-21")
CLASSES = os.path.join(FACADE, "util", "classes")
SOURCES = os.path.join(FACADE, "util", "sources", "facade")
WORK = os.path.join(os.environ["LOCALAPPDATA"], "FacadeFanRemake", "nlu_probe")


def build():
    os.makedirs(os.path.join(WORK, "nlu", "templates"), exist_ok=True)
    shutil.copy(os.path.join(SOURCES, "scriptlib.clp"), WORK)
    for f in ("TemplateSupport.jess", "wordnet.bin"):
        shutil.copy(os.path.join(SOURCES, "nlu", "templates", f), os.path.join(WORK, "nlu", "templates"))
    subprocess.run([os.path.join(JDK, "bin", "javac"), "-nowarn", "-encoding", "UTF-8", "-cp", CLASSES,
                    "-d", WORK, os.path.join(HERE, "NluProbe.java"),
                    os.path.join(HERE, "stub", "facade", "nativeinterface", "NativeAnimationInterface.java")],
                   check=True)


def parse(lines):
    """{line: "DA param; DA param"} from the real parser."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        out = subprocess.run([os.path.join(JDK, "bin", "java"), "-cp", WORK + os.pathsep + CLASSES, "NluProbe",
                              SOURCES, f.name, "DummyBeat"], cwd=WORK, capture_output=True, text=True,
                             encoding="utf-8", timeout=600).stdout
    finally:
        os.unlink(f.name)
    return dict(l.split("\t", 1) for l in out.splitlines() if "\t" in l)


def reads_as(result, expected, who=None):
    kind, _, param = expected.partition(" ")
    if kind in ("ReferTo", "ExplBig", "ExplBigNot"):
        who = None   # topics and reveals name no listener
    for da in result.split("; "):
        f = da.split(" ")
        if f[0] == kind and (not param or param in f[1:]) and (who is None or len(f) == 1 or who in f[1:]):
            return True
    return False


def main():
    build()
    cases = []   # (line, expected, who)
    every = list(acts.every_act())
    for a in every:
        text, _ = acts.render(a)
        cases.append((text, acts.phrase(a)[1], None))
        if not a.startswith(("reveal:", "drink:")):   # reveals name both; drinks go to whoever pours
            text, _ = acts.render(a + " @trip")
            cases.append((text, acts.phrase(a)[1], "trip"))
    for a in every[::7]:   # paired with another act, each must still be read
        for b in ("agree", "about:paintings"):
            if a != b:
                text, _ = acts.render(f"{b}, {a}")
                cases += [(text, acts.phrase(a)[1], None), (text, acts.phrase(b)[1], None)]
    result = parse(sorted({c[0] for c in cases}))
    bad = [(t, e, w, result.get(t.lower(), result.get(t, "?"))) for t, e, w in cases
           if not reads_as(result.get(t, "?"), e, w)]
    if "-v" in sys.argv:
        for t in sorted(result):
            print(f"{t:45} {result[t]}")
    for t, e, w, r in bad:
        print(f"MISREAD  {t!r}: expected {e}{' (' + w + ')' if w else ''}, parser read: {r}")
    print(f"{len(cases) - len(bad)}/{len(cases)} read as intended")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
