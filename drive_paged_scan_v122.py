#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

saf = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/StorageAccessFrameworkProvider.kt"
text = saf.read_text()

# Android O+ exposes paged ContentResolver queries through Bundle args.
if "import android.content.ContentResolver\n" not in text:
    text = text.replace("import android.content.Context\n", "import android.content.ContentResolver\nimport android.content.Context\n", 1)
if "import android.os.Build\n" not in text:
    text = text.replace("import android.net.Uri\n", "import android.net.Uri\nimport android.os.Build\nimport android.os.Bundle\n", 1)

start = text.find("    private data class PendingDirectory(")
end = text.find("    private fun getDriveArchiveHint(", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate Drive traversal block")

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

                    throw error ?: IllegalStateException("Drive directory scan failed")
                }

                val (files, directories) = result.getOrThrow()
                if (files.isNotEmpty()) emit(files)
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
        var offset = 0
        var page = 0
        var noProgressRetries = 0
        var useSqlLimitFallback = false

        while (true) {
            val beforeCount = resultFiles.size + resultDirectories.size
            var rowCount = 0
            var providerLoading = false
            var totalCount = -1

            val queryResult = runCatching {
                val cursor =
                    if (isDrive && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        val queryArgs = Bundle().apply {
                            if (useSqlLimitFallback) {
                                putString(
                                    ContentResolver.QUERY_ARG_SQL_LIMIT,
                                    "$DRIVE_PAGE_SIZE OFFSET $offset",
                                )
                            } else {
                                putInt(ContentResolver.QUERY_ARG_LIMIT, DRIVE_PAGE_SIZE)
                                putInt(ContentResolver.QUERY_ARG_OFFSET, offset)
                                putStringArray(
                                    ContentResolver.QUERY_ARG_SORT_COLUMNS,
                                    arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME),
                                )
                                putInt(
                                    ContentResolver.QUERY_ARG_SORT_DIRECTION,
                                    ContentResolver.QUERY_SORT_DIRECTION_ASCENDING,
                                )
                            }
                        }
                        context.contentResolver.query(childrenUri, projection, queryArgs, null)
                    } else {
                        context.contentResolver.query(childrenUri, projection, null, null, null)
                    }

                cursor?.use {
                    providerLoading =
                        runCatching {
                            it.extras.getBoolean(DocumentsContract.EXTRA_LOADING, false)
                        }.getOrDefault(false)

                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        totalCount =
                            runCatching {
                                it.extras.getInt(ContentResolver.EXTRA_TOTAL_COUNT, -1)
                            }.getOrDefault(-1)
                    }

                    while (it.moveToNext()) {
                        rowCount += 1
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
                if (noProgressRetries >= MAX_DRIVE_QUERY_RETRIES) {
                    throw queryResult.exceptionOrNull()
                        ?: IllegalStateException("Drive query failed")
                }
                noProgressRetries += 1
                delay(DRIVE_QUERY_RETRY_DELAY_MS)
                continue
            }

            val afterCount = resultFiles.size + resultDirectories.size
            val newItems = afterCount - beforeCount

            if (!isDrive || Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
                if (providerLoading && noProgressRetries < MAX_DRIVE_QUERY_RETRIES) {
                    noProgressRetries += 1
                    delay(DRIVE_QUERY_RETRY_DELAY_MS)
                    continue
                }
                break
            }

            if (totalCount >= 0 && afterCount >= totalCount) break
            if (rowCount == 0) break

            if (newItems == 0) {
                if (providerLoading && noProgressRetries < MAX_DRIVE_QUERY_RETRIES) {
                    noProgressRetries += 1
                    delay(DRIVE_QUERY_RETRY_DELAY_MS)
                    continue
                }

                // Some providers ignore structured OFFSET/LIMIT but honor the SQL limit
                // compatibility argument. Try it once before accepting a partial listing.
                if (!useSqlLimitFallback && offset > 0) {
                    useSqlLimitFallback = true
                    noProgressRetries = 0
                    continue
                }

                Timber.w(
                    "Drive paging stopped without progress at %s offset=%d rows=%d total=%d",
                    currentPath,
                    offset,
                    rowCount,
                    totalCount,
                )
                break
            }

            noProgressRetries = 0

            if (rowCount < DRIVE_PAGE_SIZE && !providerLoading && totalCount < 0) break

            offset += rowCount
            page += 1
            if (page >= MAX_DRIVE_PAGES) {
                throw IllegalStateException("Drive paging exceeded safety limit at $currentPath")
            }

            // Avoid hammering Drive when a folder contains thousands of entries.
            delay(DRIVE_PAGE_DELAY_MS)
        }

        Timber.i(
            "Drive directory scanned: %s (%d files, %d directories, %d pages)",
            currentPath,
            resultFiles.size,
            resultDirectories.size,
            page + 1,
        )

        return resultFiles.values.toList() to resultDirectories.values.toList()
    }

'''

text = text[:start] + replacement + text[end:]

# Replace the v1.2.1 retry constants with the paged scanner constants.
old_constants = '''        private const val MAX_DIRECTORY_RETRIES = 5
        private const val DIRECTORY_RETRY_DELAY_MS = 1_000L
        private const val MAX_DRIVE_QUERY_RETRIES = 20
        private const val DRIVE_QUERY_RETRY_DELAY_MS = 750L
        private const val DRIVE_REQUIRED_STABLE_PASSES = 2
        private const val DRIVE_PARTIAL_PAGE_THRESHOLD = 300
'''
new_constants = '''        private const val MAX_DIRECTORY_RETRIES = 5
        private const val DIRECTORY_RETRY_DELAY_MS = 1_000L
        private const val MAX_DRIVE_QUERY_RETRIES = 12
        private const val DRIVE_QUERY_RETRY_DELAY_MS = 750L
        private const val DRIVE_PAGE_SIZE = 300
        private const val DRIVE_PAGE_DELAY_MS = 120L
        private const val MAX_DRIVE_PAGES = 100
'''
if old_constants not in text:
    raise SystemExit("v1.2.1 Drive constants not found")
text = text.replace(old_constants, new_constants, 1)
saf.write_text(text)

# Bump the APK over v1.2.1.
gradle = root / "lemuroid-app/build.gradle.kts"
g = gradle.read_text()
g = g.replace("versionCode = 254", "versionCode = 255", 1)
g = g.replace('versionNameSuffix = "-DRIVE-1.2.1"', 'versionNameSuffix = "-DRIVE-1.2.2"', 1)
if '-DRIVE-1.2.2' not in g:
    raise SystemExit("Could not bump version to 1.2.2")
gradle.write_text(g)

print("Lemuroid Drive v1.2.2 paged Drive scanner applied")
