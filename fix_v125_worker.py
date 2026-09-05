#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/library/LibraryIndexWork.kt"
text = path.read_text()
old = '''        return Result.retry()\n\n    companion object {'''
new = '''        return Result.retry()\n    }\n\n    companion object {'''
if old not in text:
    raise SystemExit("v1.2.5 worker closing-brace pattern not found")
path.write_text(text.replace(old, new, 1))
print("v1.2.5 LibraryIndexWork syntax fix applied")
