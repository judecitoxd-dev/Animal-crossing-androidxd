#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

def replace(path, old, new):
    p = root / path
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"Pattern not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new))

replace(
    "lemuroid-app/build.gradle.kts",
    '''        getByName("debug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-DEBUG"
            resValue("string", "lemuroid_name", "LemuroiDebug")
        }''',
    '''        getByName("debug") {
            applicationIdSuffix = ".drive"
            versionNameSuffix = "-DRIVE"
            resValue("string", "lemuroid_name", "Lemuroid Drive")
        }'''
)

picker_path = "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/settings/StorageFrameworkPickerLauncher.kt"
replace(
    picker_path,
    '''                Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
                    this.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    this.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
                    this.addFlags(Intent.FLAG_GRANT_PREFIX_URI_PERMISSION)
                    this.putExtra(Intent.EXTRA_LOCAL_ONLY, true)
                }''',
    '''                Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
                    this.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    this.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                    this.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
                    this.addFlags(Intent.FLAG_GRANT_PREFIX_URI_PERMISSION)
                }'''
)
replace(
    picker_path,
    '''    private fun updatePersistableUris(uri: Uri) {
        contentResolver.persistedUriPermissions
            .filter { it.isReadPermission }
            .filter { it.uri != uri }
            .forEach {
                contentResolver.releasePersistableUriPermission(
                    it.uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            }

        contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }''',
    '''    private fun updatePersistableUris(uri: Uri) {
        val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION

        contentResolver.persistedUriPermissions
            .filter { it.uri != uri }
            .forEach {
                runCatching {
                    var oldFlags = 0
                    if (it.isReadPermission) oldFlags = oldFlags or Intent.FLAG_GRANT_READ_URI_PERMISSION
                    if (it.isWritePermission) oldFlags = oldFlags or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    if (oldFlags != 0) {
                        contentResolver.releasePersistableUriPermission(it.uri, oldFlags)
                    }
                }
            }

        contentResolver.takePersistableUriPermission(uri, flags)
    }'''
)

parser_path = "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/DocumentFileParser.kt"
replace(
    parser_path,
    '''    fun parseDocumentFile(
        context: Context,
        baseStorageFile: BaseStorageFile,
    ): StorageFile {
        return if (baseStorageFile.extension == "zip") {''',
    '''    fun parseShallow(baseStorageFile: BaseStorageFile): StorageFile {
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

    fun parseDocumentFile(
        context: Context,
        baseStorageFile: BaseStorageFile,
    ): StorageFile {
        return if (baseStorageFile.extension == "zip") {'''
)

saf_path = "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/StorageAccessFrameworkProvider.kt"
replace(
    saf_path,
    '''    override fun getStorageFile(baseStorageFile: BaseStorageFile): StorageFile? {
        return DocumentFileParser.parseDocumentFile(context, baseStorageFile)
    }''',
    '''    override fun getStorageFile(baseStorageFile: BaseStorageFile): StorageFile? {
        return if (isGoogleDriveUri(baseStorageFile.uri) && baseStorageFile.extension != "zip") {
            DocumentFileParser.parseShallow(baseStorageFile)
        } else {
            DocumentFileParser.parseDocumentFile(context, baseStorageFile)
        }
    }'''
)
replace(
    saf_path,
    '''        val originalDocumentUri = Uri.parse(game.fileUri)
        val originalDocument = DocumentFile.fromSingleUri(context, originalDocumentUri)!!

        val isZipped = originalDocument.isZipped() && originalDocument.name != game.fileName

        return when {
            isZipped && dataFiles.isEmpty() -> getGameRomFilesZipped(game, originalDocument)
            allowVirtualFiles -> getGameRomFilesVirtual(game, dataFiles)
            else -> getGameRomFilesStandard(game, dataFiles, originalDocument)
        }''',
    '''        val originalDocumentUri = Uri.parse(game.fileUri)
        val originalDocument = DocumentFile.fromSingleUri(context, originalDocumentUri)!!

        val isZipped = originalDocument.isZipped() && originalDocument.name != game.fileName
        val forceLocalCache = isGoogleDriveUri(originalDocumentUri)

        return when {
            isZipped && dataFiles.isEmpty() -> getGameRomFilesZipped(game, originalDocument)
            allowVirtualFiles && !forceLocalCache -> getGameRomFilesVirtual(game, dataFiles)
            else -> getGameRomFilesStandard(game, dataFiles, originalDocument)
        }'''
)
replace(
    saf_path,
    '''    override fun getInputStream(uri: Uri): InputStream? {
        return context.contentResolver.openInputStream(uri)
    }

    companion object {''',
    '''    override fun getInputStream(uri: Uri): InputStream? {
        return context.contentResolver.openInputStream(uri)
    }

    private fun isGoogleDriveUri(uri: Uri): Boolean {
        val authority = uri.authority ?: return false
        return authority.contains("google.android.apps.docs.storage", ignoreCase = true)
    }

    companion object {'''
)

sync_path = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/drive/DriveSafGameSync.kt"
sync_path.parent.mkdir(parents=True, exist_ok=True)
sync_path.write_text(r'''package com.swordfish.lemuroid.app.shared.drive

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import com.swordfish.lemuroid.lib.library.db.entity.Game
import com.swordfish.lemuroid.lib.preferences.SharedPreferencesHelper
import com.swordfish.lemuroid.lib.storage.DirectoriesManager
import com.swordfish.lemuroid.lib.storage.local.StorageAccessFrameworkProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

class DriveSafGameSync(private val context: Context) {
    private val directoriesManager = DirectoriesManager(context)

    suspend fun pullBeforeGame() = withContext(Dispatchers.IO) {
        val root = getOrCreateSyncRoot() ?: return@withContext
        pullFolder(getOrCreateDirectory(root, "saves") ?: return@withContext, directoriesManager.getSavesDirectory())
        pullFolder(getOrCreateDirectory(root, "states") ?: return@withContext, directoriesManager.getStatesDirectory())
        pullFolder(
            getOrCreateDirectory(root, "state-previews") ?: return@withContext,
            directoriesManager.getStatesPreviewDirectory(),
        )
    }

    suspend fun pushAfterGame() = withContext(Dispatchers.IO) {
        val root = getOrCreateSyncRoot() ?: error("No writable Drive/SAF folder is configured")
        pushFolder(directoriesManager.getSavesDirectory(), getOrCreateDirectory(root, "saves") ?: error("Cannot create saves folder"))
        pushFolder(directoriesManager.getStatesDirectory(), getOrCreateDirectory(root, "states") ?: error("Cannot create states folder"))
        pushFolder(
            directoriesManager.getStatesPreviewDirectory(),
            getOrCreateDirectory(root, "state-previews") ?: error("Cannot create state-previews folder"),
        )
    }

    fun deleteTemporaryRom(game: Game) {
        File(
            context.cacheDir,
            "${StorageAccessFrameworkProvider.SAF_CACHE_SUBFOLDER}${File.separator}${game.systemId}",
        ).deleteRecursively()
    }

    private fun selectedTreeUri(): Uri? {
        val prefString = context.getString(com.swordfish.lemuroid.lib.R.string.pref_key_extenral_folder)
        val prefs = SharedPreferencesHelper.getLegacySharedPreferences(context)
        return prefs.getString(prefString, null)?.let(Uri::parse)
    }

    private fun getOrCreateSyncRoot(): DocumentFile? {
        val tree = selectedTreeUri()?.let { DocumentFile.fromTreeUri(context, it) } ?: return null
        if (!tree.canWrite()) return null
        return getOrCreateDirectory(tree, "Lemuroid Sync")
    }

    private fun getOrCreateDirectory(parent: DocumentFile, name: String): DocumentFile? {
        val existing = parent.findFile(name)
        if (existing?.isDirectory == true) return existing
        if (existing != null) existing.delete()
        return parent.createDirectory(name)
    }

    private fun pullFolder(remote: DocumentFile, local: File) {
        local.mkdirs()
        remote.listFiles().forEach { remoteEntry ->
            val name = remoteEntry.name ?: return@forEach
            val localEntry = File(local, name)
            if (remoteEntry.isDirectory) {
                pullFolder(remoteEntry, localEntry)
            } else if (remoteEntry.isFile && remoteEntry.length() > 0L) {
                val remoteModified = remoteEntry.lastModified()
                val shouldDownload =
                    !localEntry.exists() ||
                        (remoteModified > 0L && remoteModified > localEntry.lastModified() + CLOCK_SLOP_MS)
                if (shouldDownload) {
                    localEntry.parentFile?.mkdirs()
                    context.contentResolver.openInputStream(remoteEntry.uri)?.use { input ->
                        localEntry.outputStream().use { output -> input.copyTo(output) }
                    } ?: return@forEach
                    if (remoteModified > 0L) localEntry.setLastModified(remoteModified)
                }
            }
        }
    }

    private fun pushFolder(local: File, remote: DocumentFile) {
        if (!local.exists()) return
        local.listFiles()?.forEach { localEntry ->
            if (localEntry.isDirectory) {
                val remoteDir = getOrCreateDirectory(remote, localEntry.name) ?: return@forEach
                pushFolder(localEntry, remoteDir)
            } else if (localEntry.isFile && localEntry.length() > 0L) {
                var remoteFile = remote.findFile(localEntry.name)
                if (remoteFile?.isDirectory == true) {
                    remoteFile.delete()
                    remoteFile = null
                }

                val remoteModified = remoteFile?.lastModified() ?: 0L
                val shouldUpload =
                    remoteFile == null ||
                        remoteFile.length() != localEntry.length() ||
                        remoteModified == 0L ||
                        localEntry.lastModified() > remoteModified + CLOCK_SLOP_MS

                if (shouldUpload) {
                    if (remoteFile == null) {
                        remoteFile = remote.createFile("application/octet-stream", localEntry.name)
                    }
                    val destination = remoteFile ?: error("Cannot create remote file ${localEntry.name}")
                    context.contentResolver.openOutputStream(destination.uri, "wt")?.use { output ->
                        localEntry.inputStream().use { input -> input.copyTo(output) }
                    } ?: error("Cannot open remote file ${localEntry.name} for writing")
                }
            }
        }
    }

    companion object {
        private const val CLOCK_SLOP_MS = 1500L
    }
}
''')

activity_path = "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/game/BaseGameActivity.kt"
replace(
    activity_path,
    '''import com.swordfish.lemuroid.app.shared.coreoptions.LemuroidCoreOption
import com.swordfish.lemuroid.app.shared.game.viewmodel.GameViewModelSideEffects''',
    '''import com.swordfish.lemuroid.app.shared.coreoptions.LemuroidCoreOption
import com.swordfish.lemuroid.app.shared.drive.DriveSafGameSync
import com.swordfish.lemuroid.app.shared.game.viewmodel.GameViewModelSideEffects'''
)
replace(
    activity_path,
    '''    private lateinit var baseGameScreenViewModel: BaseGameScreenViewModel

    private val startGameTime = System.currentTimeMillis()''',
    '''    private lateinit var baseGameScreenViewModel: BaseGameScreenViewModel
    private val driveSafGameSync by lazy { DriveSafGameSync(applicationContext) }

    private val startGameTime = System.currentTimeMillis()'''
)
replace(
    activity_path,
    '''        lifecycleScope.launch {
            baseGameScreenViewModel.loadGame(
                applicationContext,
                game,
                systemCoreConfig,
                gameLoader,
                intent.getBooleanExtra(EXTRA_LOAD_SAVE, false),
            )
        }''',
    '''        lifecycleScope.launch {
            runCatching { driveSafGameSync.pullBeforeGame() }
                .onFailure { Timber.w(it, "Could not pull saves from Drive/SAF") }

            baseGameScreenViewModel.loadGame(
                applicationContext,
                game,
                systemCoreConfig,
                gameLoader,
                intent.getBooleanExtra(EXTRA_LOAD_SAVE, false),
            )
        }'''
)
replace(
    activity_path,
    '''    private fun performSuccessfulActivityFinish() {
        val resultIntent =
            Intent().apply {
                putExtra(PLAY_GAME_RESULT_SESSION_DURATION, System.currentTimeMillis() - startGameTime)
                putExtra(PLAY_GAME_RESULT_GAME, intent.getSerializableExtra(EXTRA_GAME))
                putExtra(PLAY_GAME_RESULT_LEANBACK, intent.getBooleanExtra(EXTRA_LEANBACK, false))
            }

        setResult(RESULT_OK, resultIntent)
        finishAndExitProcess()
    }''',
    '''    private fun performSuccessfulActivityFinish() {
        lifecycleScope.launch {
            val syncSucceeded =
                runCatching {
                    driveSafGameSync.pushAfterGame()
                }.onFailure {
                    Timber.w(it, "Could not push saves to Drive/SAF; keeping temporary ROM cache")
                }.isSuccess

            if (syncSucceeded) {
                driveSafGameSync.deleteTemporaryRom(game)
            }

            val resultIntent =
                Intent().apply {
                    putExtra(PLAY_GAME_RESULT_SESSION_DURATION, System.currentTimeMillis() - startGameTime)
                    putExtra(PLAY_GAME_RESULT_GAME, intent.getSerializableExtra(EXTRA_GAME))
                    putExtra(PLAY_GAME_RESULT_LEANBACK, intent.getBooleanExtra(EXTRA_LEANBACK, false))
                }

            setResult(RESULT_OK, resultIntent)
            finishAndExitProcess()
        }
    }'''
)

print("Lemuroid Drive patch applied.")
