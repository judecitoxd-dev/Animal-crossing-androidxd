#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

# Make SAF/Google Drive traversal resilient to partial asynchronous directory listings.
saf = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/StorageAccessFrameworkProvider.kt"
text = saf.read_text()

if "import kotlinx.coroutines.delay\n" not in text:
    text = text.replace(
        "import kotlinx.coroutines.flow.emptyFlow\n",
        "import kotlinx.coroutines.delay\nimport kotlinx.coroutines.flow.emptyFlow\n",
        1,
    )

start = text.find("    private data class PendingDirectory(")
end = text.find("    private fun getDriveArchiveHint(", start)
if start < 0 or end < 0:
    raise SystemExit("Patched SAF traversal block not found")

replacement = r'''    private data class PendingDirectory(
        val documentId: String,
        val logicalPath: String,
        val retryCount: Int = 0,
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
                    val error = result.exceptionOrNull()
                    if (currentDirectory.retryCount < MAX_DIRECTORY_RETRIES) {
                        Timber.w(
                            error,
                            "Retrying Drive directory %s (%d/%d)",
                            currentDirectory.logicalPath,
                            currentDirectory.retryCount + 1,
                            MAX_DIRECTORY_RETRIES,
                        )
                        delay(DIRECTORY_RETRY_DELAY_MS)
                        pendingDirectories.add(
                            0,
                            currentDirectory.copy(retryCount = currentDirectory.retryCount + 1),
                        )
                        continue
                    }

                    // Do not silently skip a subtree. Propagating the failure lets the
                    // library keep the previous database instead of deleting games that
                    // were simply not seen during an incomplete Drive scan.
                    throw error ?: IllegalStateException("Drive directory scan failed")
                }

                val (files, directories) = result.getOrThrow()
                if (files.isNotEmpty()) {
                    emit(files)
                }
                pendingDirectories.addAll(directories)
            }
        }

    private suspend fun listBaseStorageFiles(
        treeUri: Uri,
        rootDocumentId: String,
        currentPath: String,
    ): Pair<List<BaseStorageFile>, List<PendingDirectory>> {
        val resultFiles = linkedMapOf<String, BaseStorageFile>()
        val resultDirectories = linkedMapOf<String, PendingDirectory>()
        val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, rootDocumentId)

        val projection =
            arrayOf(
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_SIZE,
                DocumentsContract.Document.COLUMN_MIME_TYPE,
            )

        val isDrive = isGoogleDriveUri(treeUri)
        var previousCount = -1
        var stablePasses = 0
        var attempt = 0

        while (true) {
            var providerLoading = false

            val queryResult = runCatching {
                context.contentResolver.query(childrenUri, projection, null, null, null)?.use { cursor ->
                    providerLoading =
                        runCatching {
                            cursor.extras.getBoolean(DocumentsContract.EXTRA_LOADING, false)
                        }.getOrDefault(false)

                    while (cursor.moveToNext()) {
                        val documentId = cursor.getString(0)
                        val documentName = cursor.getString(1)
                        val documentSize = cursor.getLong(2)
                        val mimeType = cursor.getString(3)

                        if (mimeType == DocumentsContract.Document.MIME_TYPE_DIR) {
                            if (documentName !in RESERVED_REMOTE_DIRECTORIES) {
                                val childPath =
                                    if (currentPath.isBlank()) {
                                        documentName
                                    } else {
                                        "$currentPath/$documentName"
                                    }
                                resultDirectories[documentId] = PendingDirectory(documentId, childPath)
                            }
                        } else {
                            val documentUri =
                                DocumentsContract.buildDocumentUriUsingTree(
                                    treeUri,
                                    documentId,
                                )
                            resultFiles[documentId] =
                                BaseStorageFile(
                                    name = documentName,
                                    size = documentSize,
                                    uri = documentUri,
                                    path = if (isGoogleDriveUri(documentUri)) currentPath else documentUri.path,
                                )
                        }
                    }
                } ?: throw IllegalStateException("Drive returned a null directory cursor")
            }

            if (queryResult.isFailure) {
                if (attempt >= MAX_DRIVE_QUERY_RETRIES) {
                    throw queryResult.exceptionOrNull()
                        ?: IllegalStateException("Drive query failed")
                }
                attempt += 1
                delay(DRIVE_QUERY_RETRY_DELAY_MS)
                continue
            }

            val currentCount = resultFiles.size + resultDirectories.size
            stablePasses =
                if (currentCount == previousCount) {
                    stablePasses + 1
                } else {
                    0
                }
            previousCount = currentCount

            if (!isDrive) {
                break
            }

            // Small Drive directories normally arrive in one pass. Large directories
            // (notably Nintendo DS with >1000 children) can initially expose only ~300
            // rows and mark the cursor as still loading. Re-query until Drive settles.
            val looksLikeLargeDriveDirectory =
                currentCount >= DRIVE_PARTIAL_PAGE_THRESHOLD ||
                    currentPath.equals("Nintendo DS", ignoreCase = true) ||
                    currentPath.endsWith("/Nintendo DS", ignoreCase = true)

            if (!providerLoading && !looksLikeLargeDriveDirectory) {
                break
            }

            if (!providerLoading && stablePasses >= DRIVE_REQUIRED_STABLE_PASSES && currentCount > DRIVE_PARTIAL_PAGE_THRESHOLD) {
                break
            }

            if (attempt >= MAX_DRIVE_QUERY_RETRIES) {
                if (providerLoading || currentCount == DRIVE_PARTIAL_PAGE_THRESHOLD) {
                    throw IllegalStateException(
                        "Google Drive directory listing remained partial after ${attempt + 1} passes: " +
                            "$currentPath ($currentCount entries)",
                    )
                }
                break
            }

            attempt += 1
            delay(DRIVE_QUERY_RETRY_DELAY_MS)
        }

        Timber.i(
            "Drive directory complete: %s (%d files, %d directories)",
            currentPath,
            resultFiles.size,
            resultDirectories.size,
        )

        return resultFiles.values.toList() to resultDirectories.values.toList()
    }

'''

text = text[:start] + replacement + text[end:]

# Add conservative retry constants to the companion object.
companion_marker = "    companion object {\n"
if companion_marker not in text:
    raise SystemExit("SAF companion object not found")
constants = '''    companion object {\n        private const val MAX_DIRECTORY_RETRIES = 5\n        private const val DIRECTORY_RETRY_DELAY_MS = 1_000L\n        private const val MAX_DRIVE_QUERY_RETRIES = 20\n        private const val DRIVE_QUERY_RETRY_DELAY_MS = 750L\n        private const val DRIVE_REQUIRED_STABLE_PASSES = 2\n        private const val DRIVE_PARTIAL_PAGE_THRESHOLD = 300\n'''
text = text.replace(companion_marker, constants, 1)
saf.write_text(text)

# Never purge existing games when an indexing pass aborts or is incomplete.
library = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/library/LemuroidLibrary.kt"
text = library.read_text()
old = '''    suspend fun indexLibrary() {\n        val startedAtMs = System.currentTimeMillis()\n\n        try {\n            indexProviders(startedAtMs)\n        } catch (e: Throwable) {\n            Timber.e("Library indexing stopped due to exception", e)\n        } finally {\n            cleanUp(startedAtMs)\n        }\n\n        val executionTime = System.currentTimeMillis() - startedAtMs\n        Timber.i("Library indexing completed in: $executionTime ms")\n    }\n'''
new = '''    suspend fun indexLibrary() {\n        val startedAtMs = System.currentTimeMillis()\n        var completedSuccessfully = false\n\n        try {\n            indexProviders(startedAtMs)\n            completedSuccessfully = true\n        } catch (e: Throwable) {\n            Timber.e("Library indexing stopped due to exception", e)\n        } finally {\n            if (completedSuccessfully) {\n                cleanUp(startedAtMs)\n            } else {\n                Timber.w("Skipping library cleanup because indexing did not complete")\n            }\n        }\n\n        val executionTime = System.currentTimeMillis() - startedAtMs\n        Timber.i("Library indexing completed in: $executionTime ms")\n    }\n'''
if old not in text:
    raise SystemExit("LemuroidLibrary indexLibrary pattern not found")
text = text.replace(old, new, 1)
library.write_text(text)

# Bump the install/update version over v1.2.
gradle = root / "lemuroid-app/build.gradle.kts"
g = gradle.read_text()
g = g.replace("versionCode = 253", "versionCode = 254", 1)
g = g.replace('versionNameSuffix = "-DRIVE-1.2"', 'versionNameSuffix = "-DRIVE-1.2.1"', 1)
if '-DRIVE-1.2.1' not in g:
    raise SystemExit("Could not bump Drive version to 1.2.1")
gradle.write_text(g)

print("Lemuroid Drive v1.2.1 resilient Drive scan patch applied")
