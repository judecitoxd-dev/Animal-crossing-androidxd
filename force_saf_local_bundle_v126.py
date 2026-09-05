#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

# Force EVERY game opened through the Storage Access Framework to be materialized
# into a normal local cache file before a libretro core sees it. This removes the
# last dependency on cloud-provider virtual file descriptors for NES/GBA/GB/etc.
saf = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/StorageAccessFrameworkProvider.kt"
text = saf.read_text()
old = '''        val forceLocalCache = isGoogleDriveUri(sourceUri)

        return when {
            isZipped && dataFiles.isEmpty() -> getGameRomFilesZipped(game, sourceUri)
            allowVirtualFiles && !forceLocalCache -> getGameRomFilesVirtual(game, dataFiles)
            else -> getGameRomFilesStandard(game, dataFiles, sourceUri)
        }
'''
new = '''        // This provider only handles content:// URIs selected through SAF.
        // Never hand a cloud/document-provider file descriptor directly to a
        // libretro core. Some cores tolerate it while others fail silently.
        // Always materialize the ROM/data files into the app cache first.
        val forceLocalCache = true

        return when {
            isZipped && dataFiles.isEmpty() -> getGameRomFilesZipped(game, sourceUri)
            allowVirtualFiles && !forceLocalCache -> getGameRomFilesVirtual(game, dataFiles)
            else -> getGameRomFilesStandard(game, dataFiles, sourceUri)
        }
'''
if old not in text:
    raise SystemExit("Could not find v1.2.5 forceLocalCache launch block")
text = text.replace(old, new, 1)
saf.write_text(text)

# v1.2.6
build = root / "lemuroid-app/build.gradle.kts"
g = build.read_text()
g = g.replace("versionCode = 258", "versionCode = 259", 1)
g = g.replace('versionNameSuffix = "-DRIVE-1.2.5"', 'versionNameSuffix = "-DRIVE-1.2.6"', 1)
if '-DRIVE-1.2.6' not in g:
    raise SystemExit("Could not bump version to 1.2.6")
build.write_text(g)

print("Lemuroid Drive v1.2.6: force-local SAF launch patch applied")
