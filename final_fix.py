#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
p = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/covers/CoverUtils.kt"
text = p.read_text()

text = text.replace(
    "import com.swordfish.lemuroid.lib.library.db.entity.Game\n",
    "import com.swordfish.lemuroid.lib.library.GameSystem\nimport com.swordfish.lemuroid.lib.library.SystemID\nimport com.swordfish.lemuroid.lib.library.db.entity.Game\n",
)
text = text.replace(
    "        return game.coverFrontUrl\n",
    "        return resolveCoverUrl(game)\n",
)
text = text.replace(
    "                val url = game.coverFrontUrl ?: return@launch\n",
    "                val url = resolveCoverUrl(game) ?: return@launch\n",
)
needle = '''    private fun localCoverFile(context: Context, game: Game): File =\n        File(File(context.filesDir, "drive-cover-cache"), coverFileName(game))\n\n'''
insert = '''    private fun resolveCoverUrl(game: Game): String? {\n        game.coverFrontUrl?.let { return it.replace("http://", "https://") }\n\n        val system = runCatching { GameSystem.findById(game.systemId) }.getOrNull() ?: return null\n        val systemName = if (system.id == SystemID.MAME2003PLUS) "MAME" else system.libretroFullName\n        val title = game.title.ifBlank { game.fileName.substringBeforeLast(".") }\n        val safeTitle = title.replace(Regex("[&*/:`<>?\\\\|]"), "_")\n        return "https://thumbnails.libretro.com/$systemName/Named_Boxarts/$safeTitle.png"\n    }\n\n    private fun localCoverFile(context: Context, game: Game): File =\n        File(File(context.filesDir, "drive-cover-cache"), coverFileName(game))\n\n'''
if needle not in text:
    raise SystemExit("Cover insertion point not found")
text = text.replace(needle, insert)

# Give the final package a visibly distinct version suffix.
gradle = root / "lemuroid-app/build.gradle.kts"
g = gradle.read_text().replace('versionNameSuffix = "-DRIVE-1.1"', 'versionNameSuffix = "-DRIVE-1.1.1"')
gradle.write_text(g)

p.write_text(text)
print("Final cover fallback fix applied")
