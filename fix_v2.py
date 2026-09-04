#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
p = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/main/GameLaunchTaskHandler.kt"
text = p.read_text()
old = "val coreName = data.getStringExtra(BaseGameActivity.PLAY_GAME_RESULT_CORE_NAME)"
new = "val coreName = data?.getStringExtra(BaseGameActivity.PLAY_GAME_RESULT_CORE_NAME)"
if old not in text:
    raise SystemExit("Expected nullable Intent pattern not found")
p.write_text(text.replace(old, new))
print("v1.1 compile fix applied")
