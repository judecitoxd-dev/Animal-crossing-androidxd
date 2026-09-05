#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

# ---------------------------------------------------------------------------
# 1) Keep every persisted SAF grant instead of revoking the previous tree.
#    Games indexed from an earlier Drive folder must remain readable later.
# ---------------------------------------------------------------------------
picker = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/settings/StorageFrameworkPickerLauncher.kt"
text = picker.read_text()
start = text.find("    private fun updatePersistableUris(uri: Uri) {")
end = text.find("    private fun startLibraryIndexWork()", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate updatePersistableUris")
new_picker = r'''    private fun updatePersistableUris(uri: Uri) {
        val readFlag = Intent.FLAG_GRANT_READ_URI_PERMISSION
        val writeFlag = Intent.FLAG_GRANT_WRITE_URI_PERMISSION

        // Never release older SAF grants. A game already stored in the DB keeps
        // the exact tree/document URI that was used when it was indexed.
        // Revoking that tree makes the game visible in the library but impossible
        // to open. Keep all user-approved Drive roots alive instead.
        runCatching {
            contentResolver.takePersistableUriPermission(uri, readFlag or writeFlag)
        }.recoverCatching {
            // Some providers expose a read-only tree.
            contentResolver.takePersistableUriPermission(uri, readFlag)
        }
    }

'''
text = text[:start] + new_picker + text[end:]
picker.write_text(text)

# ---------------------------------------------------------------------------
# 2) Replace Drive directory paging with a hybrid scanner:
#    - try real OFFSET/LIMIT pages first (1000 rows)
#    - if Drive ignores OFFSET, switch to repeated unpaged queries and merge
#      document IDs while its asynchronous cache fills.
# ---------------------------------------------------------------------------
saf = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/StorageAccessFrameworkProvider.kt"
text = saf.read_text()
start = text.find("    private suspend fun listBaseStorageFiles(")
end = text.find("    private fun getDriveArchiveHint(", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate Drive listBaseStorageFiles")
new_listing = r'''    private suspend fun listBaseStorageFiles(
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
        var queryFailures = 0
        var usePagedQueries = isDrive && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
        var fallbackPass = 0
        var stableFallbackPasses = 0

        while (true) {
            val beforeCount = resultFiles.size + resultDirectories.size
            var rowCount = 0
            var providerLoading = false
            var totalCount = -1

            val queryResult = runCatching {
                val cursor =
                    if (usePagedQueries) {
                        // Do not request sorting here. Several cloud providers accept
                        // OFFSET/LIMIT but reject or silently ignore the combination
                        // when a sort argument is also supplied.
                        val queryArgs = Bundle().apply {
                            putInt(ContentResolver.QUERY_ARG_LIMIT, DRIVE_PAGE_SIZE)
                            putInt(ContentResolver.QUERY_ARG_OFFSET, offset)
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
                if (queryFailures >= MAX_DRIVE_QUERY_RETRIES) {
                    throw queryResult.exceptionOrNull()
                        ?: IllegalStateException("Drive query failed")
                }
                queryFailures += 1
                delay(DRIVE_QUERY_RETRY_DELAY_MS)
                continue
            }
            queryFailures = 0

            val afterCount = resultFiles.size + resultDirectories.size
            val newItems = afterCount - beforeCount

            if (!isDrive) break

            if (totalCount >= 0 && afterCount >= totalCount) break

            if (usePagedQueries) {
                if (rowCount == 0) break

                // If a second page returns only IDs that were already present,
                // Google Drive ignored OFFSET. Switch to async-cache re-query mode.
                if (offset > 0 && newItems == 0) {
                    Timber.w(
                        "Drive ignored paging at %s offset=%d; switching to cumulative re-query",
                        currentPath,
                        offset,
                    )
                    usePagedQueries = false
                    fallbackPass = 0
                    stableFallbackPasses = 0
                    delay(DRIVE_FALLBACK_REQUERY_DELAY_MS)
                    continue
                }

                offset += rowCount
                page += 1
                if (page >= MAX_DRIVE_PAGES) {
                    throw IllegalStateException("Drive paging exceeded safety limit at $currentPath")
                }

                // Always try the next offset, even when Drive returned fewer rows
                // than requested. Cloud providers often impose their own page cap.
                delay(DRIVE_PAGE_DELAY_MS)
                continue
            }

            // Unpaged fallback: Drive's DocumentsProvider can initially expose a
            // partial local cache and later return additional children. Merge each
            // pass by document ID until several consecutive passes add nothing.
            fallbackPass += 1
            stableFallbackPasses = if (newItems == 0) stableFallbackPasses + 1 else 0

            if (!providerLoading && stableFallbackPasses >= DRIVE_REQUIRED_STABLE_FALLBACK_PASSES) {
                break
            }
            if (fallbackPass >= MAX_DRIVE_FALLBACK_PASSES) {
                Timber.w(
                    "Drive cumulative re-query reached safety limit at %s (%d entries)",
                    currentPath,
                    afterCount,
                )
                break
            }

            delay(DRIVE_FALLBACK_REQUERY_DELAY_MS)
        }

        Timber.i(
            "Drive directory scanned: %s (%d files, %d directories, %d paged queries, %d fallback passes)",
            currentPath,
            resultFiles.size,
            resultDirectories.size,
            page,
            fallbackPass,
        )

        return resultFiles.values.toList() to resultDirectories.values.toList()
    }

'''
text = text[:start] + new_listing + text[end:]

# Replace paging constants from v1.2.2.
old_constants = '''        private const val MAX_DIRECTORY_RETRIES = 12
        private const val DIRECTORY_RETRY_DELAY_MS = 800L
        private const val MAX_DRIVE_QUERY_RETRIES = 12
        private const val DRIVE_QUERY_RETRY_DELAY_MS = 750L
        private const val DRIVE_PAGE_SIZE = 300
        private const val DRIVE_PAGE_DELAY_MS = 120L
        private const val MAX_DRIVE_PAGES = 100
'''
new_constants = '''        private const val MAX_DIRECTORY_RETRIES = 12
        private const val DIRECTORY_RETRY_DELAY_MS = 800L
        private const val MAX_DRIVE_QUERY_RETRIES = 12
        private const val DRIVE_QUERY_RETRY_DELAY_MS = 750L
        private const val DRIVE_PAGE_SIZE = 1000
        private const val DRIVE_PAGE_DELAY_MS = 150L
        private const val MAX_DRIVE_PAGES = 100
        private const val MAX_DRIVE_FALLBACK_PASSES = 12
        private const val DRIVE_REQUIRED_STABLE_FALLBACK_PASSES = 3
        private const val DRIVE_FALLBACK_REQUERY_DELAY_MS = 900L
'''
if old_constants not in text:
    raise SystemExit("Could not locate v1.2.3 Drive constants")
text = text.replace(old_constants, new_constants, 1)

# ---------------------------------------------------------------------------
# 3) Make launching Drive games independent from DocumentFile metadata.
#    Copy/open the exact content URI stored in the DB and identify ZIP sources
#    using DISPLAY_NAME/MIME. ZIP extraction accepts renamed archives by taking
#    the first entry with the expected ROM extension.
# ---------------------------------------------------------------------------
start = text.find("    override fun getGameRomFiles(")
end = text.find("    override fun getInputStream(uri: Uri): InputStream?", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate getGameRomFiles section")
new_launch = r'''    override fun getGameRomFiles(
        game: Game,
        dataFiles: List<DataFile>,
        allowVirtualFiles: Boolean,
    ): RomFiles {
        val sourceUri = Uri.parse(game.fileUri)
        val sourceName = getDocumentDisplayName(sourceUri)
        val sourceMime = runCatching { context.contentResolver.getType(sourceUri) }.getOrNull()
        val sourceIsZip =
            sourceName?.endsWith(".zip", ignoreCase = true) == true ||
                sourceMime.equals("application/zip", ignoreCase = true) ||
                sourceMime.equals("application/x-zip-compressed", ignoreCase = true)
        val isZipped = sourceIsZip && !sourceName.equals(game.fileName, ignoreCase = true)
        val forceLocalCache = isGoogleDriveUri(sourceUri)

        return when {
            isZipped && dataFiles.isEmpty() -> getGameRomFilesZipped(game, sourceUri)
            allowVirtualFiles && !forceLocalCache -> getGameRomFilesVirtual(game, dataFiles)
            else -> getGameRomFilesStandard(game, dataFiles, sourceUri)
        }
    }

    private fun getDocumentDisplayName(uri: Uri): String? {
        val projection = arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
        return runCatching {
            context.contentResolver.query(uri, projection, null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else null
            }
        }.getOrNull()
    }

    private fun getGameRomFilesStandard(
        game: Game,
        dataFiles: List<DataFile>,
        sourceUri: Uri,
    ): RomFiles {
        val gameEntry = getGameRomStandard(game, sourceUri)
        val dataEntries = dataFiles.map { getDataFileStandard(game, it) }
        return RomFiles.Standard(listOf(gameEntry) + dataEntries)
    }

    private fun getGameRomFilesZipped(
        game: Game,
        sourceUri: Uri,
    ): RomFiles {
        val cacheFile = GameCacheUtils.getCacheFileForGame(SAF_CACHE_SUBFOLDER, context, game)
        if (cacheFile.exists() && cacheFile.length() > 0L) {
            return RomFiles.Standard(listOf(cacheFile))
        }
        cacheFile.delete()

        val expectedExtension = game.fileName.substringAfterLast('.', "").lowercase()
        val raw =
            context.contentResolver.openInputStream(sourceUri)
                ?: throw IllegalStateException("Could not open Drive ZIP: $sourceUri")

        ZipInputStream(raw).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                if (entry.isDirectory) continue

                val entryName = entry.name.substringAfterLast('/')
                val entryExtension = entryName.substringAfterLast('.', "").lowercase()
                val exact = entryName.equals(game.fileName, ignoreCase = true)
                val compatible = expectedExtension.isNotBlank() && entryExtension == expectedExtension

                if (exact || compatible) {
                    cacheFile.parentFile?.mkdirs()
                    cacheFile.outputStream().use { output -> zip.copyTo(output) }
                    if (cacheFile.length() <= 0L) {
                        cacheFile.delete()
                        throw IllegalStateException("Drive ZIP entry was empty: $entryName")
                    }
                    return RomFiles.Standard(listOf(cacheFile))
                }
            }
        }

        cacheFile.delete()
        throw IllegalStateException(
            "No compatible .$expectedExtension ROM found inside Drive ZIP for ${game.fileName}",
        )
    }

    private fun getGameRomFilesVirtual(
        game: Game,
        dataFiles: List<DataFile>,
    ): RomFiles {
        val gameEntry = getGameRomVirtual(game)
        val dataEntries = dataFiles.map { getDataFileVirtual(it) }
        return RomFiles.Virtual(listOf(gameEntry) + dataEntries)
    }

    private fun getDataFileVirtual(dataFile: DataFile): RomFiles.Virtual.Entry {
        return RomFiles.Virtual.Entry(
            "$VIRTUAL_FILE_PATH/${dataFile.fileName}",
            context.contentResolver.openFileDescriptor(Uri.parse(dataFile.fileUri), "r")
                ?: throw IllegalStateException("Could not open data file ${dataFile.fileUri}"),
        )
    }

    private fun getDataFileStandard(
        game: Game,
        dataFile: DataFile,
    ): File {
        val cacheFile =
            GameCacheUtils.getDataFileForGame(
                SAF_CACHE_SUBFOLDER,
                context,
                game,
                dataFile,
            )

        if (cacheFile.exists() && cacheFile.length() > 0L) return cacheFile
        cacheFile.delete()

        val uri = Uri.parse(dataFile.fileUri)
        val stream =
            context.contentResolver.openInputStream(uri)
                ?: throw IllegalStateException("Could not open Drive data file: $uri")
        stream.writeToFile(cacheFile)
        return cacheFile
    }

    private fun getGameRomVirtual(game: Game): RomFiles.Virtual.Entry {
        return RomFiles.Virtual.Entry(
            "$VIRTUAL_FILE_PATH/${game.fileName}",
            context.contentResolver.openFileDescriptor(Uri.parse(game.fileUri), "r")
                ?: throw IllegalStateException("Could not open game URI ${game.fileUri}"),
        )
    }

    private fun getGameRomStandard(
        game: Game,
        sourceUri: Uri,
    ): File {
        val cacheFile = GameCacheUtils.getCacheFileForGame(SAF_CACHE_SUBFOLDER, context, game)
        if (cacheFile.exists() && cacheFile.length() > 0L) return cacheFile
        cacheFile.delete()

        val stream =
            context.contentResolver.openInputStream(sourceUri)
                ?: throw IllegalStateException("Could not open Drive ROM: $sourceUri")
        stream.writeToFile(cacheFile)
        if (cacheFile.length() <= 0L) {
            cacheFile.delete()
            throw IllegalStateException("Drive returned an empty ROM for ${game.fileName}")
        }
        return cacheFile
    }

'''
text = text[:start] + new_launch + text[end:]
saf.write_text(text)

# ---------------------------------------------------------------------------
# 4) Make scan results cumulative. Google Drive can expose different partial
#    subsets on consecutive cloud-provider queries. Never delete a game merely
#    because it was absent from one pass.
# ---------------------------------------------------------------------------
library = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/library/LemuroidLibrary.kt"
text = library.read_text()
old_cleanup = '''            if (completedSuccessfully) {
                cleanUp(startedAtMs)
            } else {
                Timber.w("Skipping library cleanup because indexing did not complete")
            }
'''
new_cleanup = '''            if (completedSuccessfully) {
                // Drive scans are cumulative in this fork. A cloud DocumentsProvider
                // can return a valid but partial subset even without throwing.
                // Removing unseen rows here made the game count jump up and down.
                Timber.i("Cumulative Drive indexing pass complete; retaining previous library entries")
            } else {
                Timber.w("Skipping library cleanup because indexing did not complete")
            }
'''
if old_cleanup not in text:
    raise SystemExit("Could not locate v1.2.3 cleanup block")
text = text.replace(old_cleanup, new_cleanup, 1)
library.write_text(text)

# ---------------------------------------------------------------------------
# 5) Run several successful sweeps automatically too, not only failure retries.
#    This accumulates cloud results without the user pressing Analyze repeatedly.
# ---------------------------------------------------------------------------
worker = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/library/LibraryIndexWork.kt"
text = worker.read_text()
start = text.find("        var lastFailure: Throwable? = null")
end = text.find("    companion object {", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate v1.2.3 worker retry block")
new_worker = r'''        var lastFailure: Throwable? = null
        var successfulSweeps = 0

        for (sweep in 1..MAX_CUMULATIVE_SCAN_SWEEPS) {
            val result =
                withContext(Dispatchers.IO) {
                    kotlin.runCatching {
                        lemuroidLibrary.indexLibrary()
                    }
                }

            if (result.isSuccess) {
                successfulSweeps += 1
                lastFailure = null
                Timber.i(
                    "Drive cumulative library sweep %d/%d completed",
                    sweep,
                    MAX_CUMULATIVE_SCAN_SWEEPS,
                )
            } else {
                lastFailure = result.exceptionOrNull()
                Timber.w(
                    lastFailure,
                    "Drive cumulative library sweep %d/%d failed; continuing automatically",
                    sweep,
                    MAX_CUMULATIVE_SCAN_SWEEPS,
                )
            }

            if (sweep < MAX_CUMULATIVE_SCAN_SWEEPS) {
                delay(CUMULATIVE_SCAN_SWEEP_DELAY_MS)
            }
        }

        if (successfulSweeps > 0) {
            LibraryIndexScheduler.scheduleCoreUpdate(applicationContext)
            return Result.success()
        }

        Timber.e(lastFailure, "All cumulative Drive scan sweeps failed; scheduling retry")
        return Result.retry()

'''
text = text[:start] + new_worker + text[end:]
text = text.replace(
    '''        private const val MAX_AUTOMATIC_SCAN_ATTEMPTS = 8
        private const val AUTOMATIC_SCAN_RETRY_DELAY_MS = 2_000L
''',
    '''        private const val MAX_CUMULATIVE_SCAN_SWEEPS = 4
        private const val CUMULATIVE_SCAN_SWEEP_DELAY_MS = 1_500L
''',
    1,
)
worker.write_text(text)

# ---------------------------------------------------------------------------
# 6) Prevent cloud-folder duplication. Covers and saves may launch many parallel
#    jobs, and Drive's findFile() can temporarily return null. Cache the chosen
#    directory URI and serialize folder creation. Never create these management
#    folders inside a console subfolder such as Nintendo DS/Gba/Nes.
# ---------------------------------------------------------------------------
cover = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/covers/CoverUtils.kt"
text = cover.read_text()
needle = '''    private val coverClient = OkHttpClient.Builder().build()
'''
if needle not in text:
    raise SystemExit("Could not locate CoverUtils fields")
text = text.replace(
    needle,
    needle + '''    private val coversDirectoryLock = Any()
    private const val PREF_COVERS_TREE_URI = "lemuroid_drive_covers_tree_uri"
    private const val PREF_COVERS_DIRECTORY_URI = "lemuroid_drive_covers_directory_uri"
''',
    1,
)
start = text.find("    private fun coversDirectory(context: Context, create: Boolean): DocumentFile? {")
end = text.find("    private fun pullCoverFromDrive(", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate coversDirectory")
new_covers_dir = r'''    private fun coversDirectory(context: Context, create: Boolean): DocumentFile? {
        val tree = selectedTree(context) ?: return null
        val treeUri = tree.uri.toString()
        val prefs = SharedPreferencesHelper.getLegacySharedPreferences(context)

        fun cached(): DocumentFile? {
            if (prefs.getString(PREF_COVERS_TREE_URI, null) != treeUri) return null
            val cachedUri = prefs.getString(PREF_COVERS_DIRECTORY_URI, null) ?: return null
            return runCatching { DocumentFile.fromSingleUri(context, Uri.parse(cachedUri)) }.getOrNull()
                ?.takeIf { it.isDirectory }
        }

        cached()?.let { return it }

        return synchronized(coversDirectoryLock) {
            cached()?.let { return@synchronized it }

            val existing = runCatching { tree.findFile("Lemuroid Covers") }.getOrNull()
                ?.takeIf { it.isDirectory }
            if (existing != null) {
                prefs.edit()
                    .putString(PREF_COVERS_TREE_URI, treeUri)
                    .putString(PREF_COVERS_DIRECTORY_URI, existing.uri.toString())
                    .apply()
                return@synchronized existing
            }

            if (!create || !tree.canWrite()) return@synchronized null

            // The user should select the common Roms root. Do not pollute a
            // console/game folder if it was selected temporarily.
            val treeName = runCatching { tree.name }.getOrNull()
            if (treeName != null && !treeName.equals("Roms", ignoreCase = true)) {
                return@synchronized null
            }

            val created = tree.createDirectory("Lemuroid Covers") ?: return@synchronized null
            prefs.edit()
                .putString(PREF_COVERS_TREE_URI, treeUri)
                .putString(PREF_COVERS_DIRECTORY_URI, created.uri.toString())
                .apply()
            created
        }
    }

'''
text = text[:start] + new_covers_dir + text[end:]
cover.write_text(text)

sync = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/drive/DriveSafGameSync.kt"
text = sync.read_text()
start = text.find("    private fun getSyncRoot(create: Boolean): DocumentFile? {")
end = text.find("    private fun getOrCreateDirectory(", start)
if start < 0 or end < 0:
    raise SystemExit("Could not locate DriveSafGameSync.getSyncRoot")
new_sync_root = r'''    private fun getSyncRoot(create: Boolean): DocumentFile? {
        val tree = selectedTree() ?: return null
        val treeUri = tree.uri.toString()
        val prefs = SharedPreferencesHelper.getLegacySharedPreferences(context)

        fun cached(): DocumentFile? {
            if (prefs.getString(PREF_SYNC_TREE_URI, null) != treeUri) return null
            val cachedUri = prefs.getString(PREF_SYNC_DIRECTORY_URI, null) ?: return null
            return runCatching { DocumentFile.fromSingleUri(context, Uri.parse(cachedUri)) }.getOrNull()
                ?.takeIf { it.isDirectory }
        }

        cached()?.let { return it }

        return synchronized(SYNC_ROOT_LOCK) {
            cached()?.let { return@synchronized it }

            val existing = runCatching { tree.findFile("Lemuroid Sync") }.getOrNull()
                ?.takeIf { it.isDirectory }
            if (existing != null) {
                prefs.edit()
                    .putString(PREF_SYNC_TREE_URI, treeUri)
                    .putString(PREF_SYNC_DIRECTORY_URI, existing.uri.toString())
                    .apply()
                return@synchronized existing
            }

            if (!create || !tree.canWrite()) return@synchronized null
            val treeName = runCatching { tree.name }.getOrNull()
            if (treeName != null && !treeName.equals("Roms", ignoreCase = true)) {
                return@synchronized null
            }

            val created = tree.createDirectory("Lemuroid Sync") ?: return@synchronized null
            prefs.edit()
                .putString(PREF_SYNC_TREE_URI, treeUri)
                .putString(PREF_SYNC_DIRECTORY_URI, created.uri.toString())
                .apply()
            created
        }
    }

'''
text = text[:start] + new_sync_root + text[end:]
old_companion = '''    companion object {
        private const val CLOCK_SLOP_MS = 1500L
    }
'''
new_companion = '''    companion object {
        private const val CLOCK_SLOP_MS = 1500L
        private const val PREF_SYNC_TREE_URI = "lemuroid_drive_sync_tree_uri"
        private const val PREF_SYNC_DIRECTORY_URI = "lemuroid_drive_sync_directory_uri"
        private val SYNC_ROOT_LOCK = Any()
    }
'''
if old_companion not in text:
    raise SystemExit("Could not locate DriveSafGameSync companion")
text = text.replace(old_companion, new_companion, 1)
sync.write_text(text)

# ---------------------------------------------------------------------------
# 7) Bump APK version over v1.2.4.
# ---------------------------------------------------------------------------
gradle = root / "lemuroid-app/build.gradle.kts"
g = gradle.read_text()
g = g.replace("versionCode = 257", "versionCode = 258", 1)
g = g.replace('versionNameSuffix = "-DRIVE-1.2.4"', 'versionNameSuffix = "-DRIVE-1.2.5"', 1)
if '-DRIVE-1.2.5' not in g:
    raise SystemExit("Could not bump version to 1.2.5")
gradle.write_text(g)

print("Lemuroid Drive v1.2.5 storage integrity patch applied")
