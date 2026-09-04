#!/usr/bin/env python3
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()

# 1) Add a shallow archive representation. For the Nintendo DS collection we
# can infer the inner ROM name from the ZIP filename without opening Drive.
parser = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/DocumentFileParser.kt"
text = parser.read_text()

if "import com.swordfish.lemuroid.lib.library.SystemID\n" not in text:
    text = text.replace(
        "import com.swordfish.lemuroid.lib.storage.BaseStorageFile\n",
        "import com.swordfish.lemuroid.lib.library.SystemID\nimport com.swordfish.lemuroid.lib.storage.BaseStorageFile\n",
    )

needle = '''    fun parseShallow(baseStorageFile: BaseStorageFile): StorageFile {
        return StorageFile(
            baseStorageFile.name,
            baseStorageFile.size,
            null,
            null,
            baseStorageFile.uri,
            baseStorageFile.uri.path,
            null,
        )
    }

'''
replacement = needle + '''    fun parseShallowArchive(
        baseStorageFile: BaseStorageFile,
        romExtension: String,
        systemID: SystemID,
    ): StorageFile {
        return StorageFile(
            "${baseStorageFile.extensionlessName}.$romExtension",
            baseStorageFile.size,
            null,
            null,
            baseStorageFile.uri,
            baseStorageFile.path,
            systemID,
        )
    }

'''
if "fun parseShallowArchive(" not in text:
    if needle not in text:
        raise SystemExit("DocumentFileParser shallow insertion point not found")
    text = text.replace(needle, replacement, 1)
parser.write_text(text)

# 2) Track logical Drive folder names while scanning. This lets us recognize
# ZIPs under Roms/Nintendo DS/... without reading the ZIP itself.
saf = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/StorageAccessFrameworkProvider.kt"
text = saf.read_text()

if "import com.swordfish.lemuroid.lib.library.SystemID\n" not in text:
    text = text.replace(
        "import com.swordfish.lemuroid.lib.library.db.entity.DataFile\n",
        "import com.swordfish.lemuroid.lib.library.SystemID\nimport com.swordfish.lemuroid.lib.library.db.entity.DataFile\n",
    )

old_get = '''    override fun getStorageFile(baseStorageFile: BaseStorageFile): StorageFile? {
        return if (isGoogleDriveUri(baseStorageFile.uri) && baseStorageFile.extension != "zip") {
            DocumentFileParser.parseShallow(baseStorageFile)
        } else {
            DocumentFileParser.parseDocumentFile(context, baseStorageFile)
        }
    }
'''
new_get = '''    override fun getStorageFile(baseStorageFile: BaseStorageFile): StorageFile? {
        if (isGoogleDriveUri(baseStorageFile.uri)) {
            if (baseStorageFile.extension.equals("zip", ignoreCase = true)) {
                getDriveArchiveHint(baseStorageFile)?.let { hint ->
                    return DocumentFileParser.parseShallowArchive(
                        baseStorageFile,
                        hint.romExtension,
                        hint.systemID,
                    )
                }
            } else {
                return DocumentFileParser.parseShallow(baseStorageFile)
            }
        }

        return DocumentFileParser.parseDocumentFile(context, baseStorageFile)
    }
'''
if old_get not in text:
    raise SystemExit("StorageAccessFrameworkProvider getStorageFile pattern not found")
text = text.replace(old_get, new_get, 1)

start = text.find("    private fun traverseDirectoryEntries(rootUri: Uri): Flow<List<BaseStorageFile>> =")
end = text.find("    override fun getGameRomFiles(", start)
if start < 0 or end < 0:
    raise SystemExit("SAF traversal block not found")

new_traversal = '''    private data class PendingDirectory(
        val documentId: String,
        val logicalPath: String,
    )

    private data class DriveArchiveHint(
        val systemID: SystemID,
        val romExtension: String,
    )

    private fun traverseDirectoryEntries(rootUri: Uri): Flow<List<BaseStorageFile>> =
        flow {
            val pendingDirectories = mutableListOf<PendingDirectory>()
            DocumentsContract.getTreeDocumentId(rootUri)?.let {
                pendingDirectories.add(PendingDirectory(it, ""))
            }

            while (pendingDirectories.isNotEmpty()) {
                val currentDirectory = pendingDirectories.removeAt(0)

                val result =
                    runCatching {
                        listBaseStorageFiles(
                            rootUri,
                            currentDirectory.documentId,
                            currentDirectory.logicalPath,
                        )
                    }
                if (result.isFailure) {
                    Timber.e(result.exceptionOrNull(), "Error while listing files")
                }

                val (files, directories) =
                    result.getOrDefault(
                        listOf<BaseStorageFile>() to listOf<PendingDirectory>(),
                    )

                emit(files)
                pendingDirectories.addAll(directories)
            }
        }

    private fun listBaseStorageFiles(
        treeUri: Uri,
        rootDocumentId: String,
        currentPath: String,
    ): Pair<List<BaseStorageFile>, List<PendingDirectory>> {
        val resultFiles = mutableListOf<BaseStorageFile>()
        val resultDirectories = mutableListOf<PendingDirectory>()

        val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, rootDocumentId)

        Timber.d("Querying files in directory: $childrenUri")

        val projection =
            arrayOf(
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_SIZE,
                DocumentsContract.Document.COLUMN_MIME_TYPE,
            )
        context.contentResolver.query(childrenUri, projection, null, null, null)?.use {
            while (it.moveToNext()) {
                val documentId = it.getString(0)
                val documentName = it.getString(1)
                val documentSize = it.getLong(2)
                val mimeType = it.getString(3)

                if (mimeType == DocumentsContract.Document.MIME_TYPE_DIR) {
                    if (documentName !in RESERVED_REMOTE_DIRECTORIES) {
                        val childPath =
                            if (currentPath.isBlank()) {
                                documentName
                            } else {
                                "$currentPath/$documentName"
                            }
                        resultDirectories.add(PendingDirectory(documentId, childPath))
                    }
                } else {
                    val documentUri =
                        DocumentsContract.buildDocumentUriUsingTree(
                            treeUri,
                            documentId,
                        )
                    resultFiles.add(
                        BaseStorageFile(
                            name = documentName,
                            size = documentSize,
                            uri = documentUri,
                            path = if (isGoogleDriveUri(documentUri)) currentPath else documentUri.path,
                        ),
                    )
                }
            }
        }

        return resultFiles to resultDirectories
    }

    private fun getDriveArchiveHint(baseStorageFile: BaseStorageFile): DriveArchiveHint? {
        val pathSegments =
            baseStorageFile.path
                .orEmpty()
                .split('/')
                .map { it.trim() }
                .filter { it.isNotBlank() }

        val isNintendoDs =
            pathSegments.any {
                it.equals("Nintendo DS", ignoreCase = true) ||
                    it.equals("NDS", ignoreCase = true)
            }

        return if (isNintendoDs) {
            DriveArchiveHint(SystemID.NDS, "nds")
        } else {
            null
        }
    }

'''
text = text[:start] + new_traversal + text[end:]
saf.write_text(text)

# 3) Bump version so Android sees this as a newer update over v1.1/v1.1.1.
gradle = root / "lemuroid-app/build.gradle.kts"
g = gradle.read_text()
g = g.replace('versionCode = 252', 'versionCode = 253', 1)
g = g.replace('versionNameSuffix = "-DRIVE-1.1.1"', 'versionNameSuffix = "-DRIVE-1.2"', 1)
if '-DRIVE-1.2' not in g:
    raise SystemExit("Could not bump Drive version suffix to 1.2")
gradle.write_text(g)

print("Lemuroid Drive v1.2 fast Drive ZIP scan patch applied")
