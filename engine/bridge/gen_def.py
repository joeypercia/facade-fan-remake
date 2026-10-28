"""Generate bridge.def: forward every export of the original bridge that bridge.cpp doesn't wrap.

    python engine/bridge/gen_def.py [path to the original nativeaniminterface.dll]
"""
import re
import sys
from pathlib import Path

import pefile

HERE = Path(__file__).resolve().parent
DEFAULT = Path(r"C:\Program Files (x86)\Facade\util\sources\facade\nativeaniminterface.dll")
ORIG = "nativeaniminterface_orig"


def wrapped():
    src = (HERE / "bridge.cpp").read_text(encoding="utf-8")
    jni = set(re.findall(r"JNI_FN\(\w+, (\w+),", src)) | set(
        re.findall(r"Java_facade_nativeinterface_NativeAnimationInterface_(\w+)\(JNIEnv", src))
    cpp = set(re.findall(r"bool (AblCallbackAPI_\w+)\(", src))
    return jni, cpp


def main():
    dll = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if dll.name.lower() != "nativeaniminterface.dll":
        dll = dll.with_name("nativeaniminterface.dll")
    if not dll.exists():
        dll = dll.with_name(ORIG + ".dll")  # already installed: read the renamed original
    jni, cpp = wrapped()
    names = [s.name.decode() for s in pefile.PE(str(dll)).DIRECTORY_ENTRY_EXPORT.symbols if s.name]
    lines = ["LIBRARY nativeaniminterface", "EXPORTS"]
    skipped = []
    for n in names:
        m = re.match(r"_Java_facade_nativeinterface_NativeAnimationInterface_(\w+)@\d+$", n)
        c = re.match(r"\?(AblCallbackAPI_\w+)@@", n)
        if (m and m.group(1) in jni) or (c and c.group(1) in cpp):
            skipped.append(n)
            continue
        lines.append(f"    {n}={ORIG}.{n}")
    (HERE / "bridge.def").write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"{len(names)} exports: {len(names) - len(skipped)} forwarded, {len(skipped)} wrapped")


if __name__ == "__main__":
    main()
