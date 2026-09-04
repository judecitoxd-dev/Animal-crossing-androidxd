#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()


def replace(path, old, new):
    p = root / path
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"Pattern not found in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new))


# Keep this fork installable next to stock Lemuroid.
replace(
    "lemuroid-app/build.gradle.kts",
    '''        getByName("debug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-DEBUG"
            resValue("string", "lemuroid_name", "LemuroiDebug")
        }''',
    '''        getByName("debug") {
            applicationIdSuffix = ".drive"
            versionNameSuffix = "-DRIVE-1.1"
            resValue("string", "lemuroid_name", "Lemuroid Drive")
        }'''
)

# Let Android's document picker expose cloud providers such as Google Drive,
# and retain both read and write grants.
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

# Shallow metadata for Drive avoids reading entire ROMs merely to scan the library.
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
    '''                if (mimeType == DocumentsContract.Document.MIME_TYPE_DIR) {
                    resultDirectories.add(documentId)
                } else {''',
    '''                if (mimeType == DocumentsContract.Document.MIME_TYPE_DIR) {
                    if (documentName !in RESERVED_REMOTE_DIRECTORIES) {
                        resultDirectories.add(documentId)
                    }
                } else {'''
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

    companion object {
        private val RESERVED_REMOTE_DIRECTORIES = setOf("Lemuroid Sync", "Lemuroid Covers")'''
)

# Game-specific Drive save sync. This replaces the v1 behavior that walked all
# saves/states before every launch.
sync_path = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/drive/DriveSafGameSync.kt"
sync_path.parent.mkdir(parents=True, exist_ok=True)
sync_path.write_text(r'''package com.swordfish.lemuroid.app.shared.drive

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import com.swordfish.lemuroid.lib.library.db.entity.DataFile
import com.swordfish.lemuroid.lib.library.db.entity.Game
import com.swordfish.lemuroid.lib.preferences.SharedPreferencesHelper
import com.swordfish.lemuroid.lib.storage.DirectoriesManager
import com.swordfish.lemuroid.lib.storage.local.StorageAccessFrameworkProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

class DriveSafGameSync(private val context: Context) {
    private val directoriesManager = DirectoriesManager(context)

    suspend fun pullBeforeGame(game: Game, coreName: String) = withContext(Dispatchers.IO) {
        val root = getSyncRoot(create = false) ?: return@withContext
        val saveName = "${game.fileName.substringBeforeLast(".")}.srm"

        root.findFile("saves")?.takeIf { it.isDirectory }?.let {
            pullNamedFile(it, directoriesManager.getSavesDirectory(), saveName)
        }

        root.findFile("states")?.findFile(coreName)?.takeIf { it.isDirectory }?.let { remoteCore ->
            stateFileNames(game).forEach { name ->
                pullNamedFile(remoteCore, File(directoriesManager.getStatesDirectory(), coreName), name)
            }
        }

        root.findFile("state-previews")?.findFile(coreName)?.takeIf { it.isDirectory }?.let { remoteCore ->
            previewFileNames(game).forEach { name ->
                pullNamedFile(remoteCore, File(directoriesManager.getStatesPreviewDirectory(), coreName), name)
            }
        }
    }

    suspend fun pushAfterGame(game: Game, coreName: String) = withContext(Dispatchers.IO) {
        val root = getSyncRoot(create = true) ?: error("No writable Drive/SAF folder is configured")
        val savesRemote = getOrCreateDirectory(root, "saves") ?: error("Cannot create saves folder")
        val statesRemote = getOrCreateDirectory(root, "states") ?: error("Cannot create states folder")
        val previewsRemote = getOrCreateDirectory(root, "state-previews") ?: error("Cannot create preview folder")
        val stateCoreRemote = getOrCreateDirectory(statesRemote, coreName) ?: error("Cannot create state core folder")
        val previewCoreRemote = getOrCreateDirectory(previewsRemote, coreName) ?: error("Cannot create preview core folder")

        val saveName = "${game.fileName.substringBeforeLast(".")}.srm"
        pushNamedFile(directoriesManager.getSavesDirectory(), savesRemote, saveName)

        val localStates = File(directoriesManager.getStatesDirectory(), coreName)
        stateFileNames(game).forEach { name -> pushNamedFile(localStates, stateCoreRemote, name) }

        val localPreviews = File(directoriesManager.getStatesPreviewDirectory(), coreName)
        previewFileNames(game).forEach { name -> pushNamedFile(localPreviews, previewCoreRemote, name) }
    }

    fun deleteTemporaryRom(game: Game, dataFiles: List<DataFile>) {
        val cacheDir = File(
            context.cacheDir,
            "${StorageAccessFrameworkProvider.SAF_CACHE_SUBFOLDER}${File.separator}${game.systemId}",
        )
        File(cacheDir, game.fileName).delete()
        dataFiles.forEach { File(cacheDir, it.fileName).delete() }
        if (cacheDir.listFiles()?.isEmpty() == true) cacheDir.delete()
    }

    private fun stateFileNames(game: Game): List<String> {
        val base = mutableListOf("${game.fileName}.state")
        (1..4).forEach { base.add("${game.fileName}.slot$it") }
        return base.flatMap { listOf(it, "$it.metadata") }
    }

    private fun previewFileNames(game: Game): List<String> =
        (1..4).map { "${game.fileName}.slot$it.jpg" }

    private fun selectedTreeUri(): Uri? {
        val prefString = context.getString(com.swordfish.lemuroid.lib.R.string.pref_key_extenral_folder)
        val prefs = SharedPreferencesHelper.getLegacySharedPreferences(context)
        return prefs.getString(prefString, null)?.let(Uri::parse)
    }

    private fun selectedTree(): DocumentFile? =
        selectedTreeUri()?.let { DocumentFile.fromTreeUri(context, it) }

    private fun getSyncRoot(create: Boolean): DocumentFile? {
        val tree = selectedTree() ?: return null
        val existing = tree.findFile("Lemuroid Sync")
        if (existing?.isDirectory == true) return existing
        if (!create || !tree.canWrite()) return null
        existing?.delete()
        return tree.createDirectory("Lemuroid Sync")
    }

    private fun getOrCreateDirectory(parent: DocumentFile, name: String): DocumentFile? {
        val existing = parent.findFile(name)
        if (existing?.isDirectory == true) return existing
        if (!parent.canWrite()) return null
        existing?.delete()
        return parent.createDirectory(name)
    }

    private fun pullNamedFile(remoteDir: DocumentFile, localDir: File, name: String) {
        val remote = remoteDir.findFile(name)?.takeIf { it.isFile && it.length() > 0L } ?: return
        localDir.mkdirs()
        val local = File(localDir, name)
        val remoteModified = remote.lastModified()
        val shouldDownload =
            !local.exists() ||
                local.length() != remote.length() ||
                (remoteModified > 0L && remoteModified > local.lastModified() + CLOCK_SLOP_MS)

        if (!shouldDownload) return
        context.contentResolver.openInputStream(remote.uri)?.use { input ->
            local.outputStream().use { output -> input.copyTo(output) }
        } ?: return
        if (remoteModified > 0L) local.setLastModified(remoteModified)
    }

    private fun pushNamedFile(localDir: File, remoteDir: DocumentFile, name: String) {
        val local = File(localDir, name)
        if (!local.isFile || local.length() <= 0L) return

        var remote = remoteDir.findFile(name)
        if (remote?.isDirectory == true) {
            remote.delete()
            remote = null
        }

        val remoteModified = remote?.lastModified() ?: 0L
        val shouldUpload =
            remote == null ||
                remote.length() != local.length() ||
                remoteModified == 0L ||
                local.lastModified() > remoteModified + CLOCK_SLOP_MS

        if (!shouldUpload) return
        if (remote == null) {
            remote = remoteDir.createFile("application/octet-stream", name)
        }
        val destination = remote ?: error("Cannot create remote file $name")
        context.contentResolver.openOutputStream(destination.uri, "wt")?.use { output ->
            local.inputStream().use { input -> input.copyTo(output) }
        } ?: error("Cannot open remote file $name for writing")
    }

    companion object {
        private const val CLOCK_SLOP_MS = 1500L
    }
}
''')

# Pull only the current game's saves before emulation. On quit, close the game
# process immediately and pass the core name back to the main process.
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
            runCatching {
                driveSafGameSync.pullBeforeGame(game, systemCoreConfig.coreID.coreName)
            }.onFailure {
                Timber.w(it, "Could not pull current-game saves from Drive/SAF")
            }

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
    '''                putExtra(PLAY_GAME_RESULT_GAME, intent.getSerializableExtra(EXTRA_GAME))
                putExtra(PLAY_GAME_RESULT_LEANBACK, intent.getBooleanExtra(EXTRA_LEANBACK, false))''',
    '''                putExtra(PLAY_GAME_RESULT_GAME, intent.getSerializableExtra(EXTRA_GAME))
                putExtra(PLAY_GAME_RESULT_CORE_NAME, systemCoreConfig.coreID.coreName)
                putExtra(PLAY_GAME_RESULT_LEANBACK, intent.getBooleanExtra(EXTRA_LEANBACK, false))'''
)
replace(
    activity_path,
    '''        const val PLAY_GAME_RESULT_GAME = "PLAY_GAME_RESULT_GAME"
        const val PLAY_GAME_RESULT_LEANBACK = "PLAY_GAME_RESULT_LEANBACK"''',
    '''        const val PLAY_GAME_RESULT_GAME = "PLAY_GAME_RESULT_GAME"
        const val PLAY_GAME_RESULT_CORE_NAME = "PLAY_GAME_RESULT_CORE_NAME"
        const val PLAY_GAME_RESULT_LEANBACK = "PLAY_GAME_RESULT_LEANBACK"'''
)

# Main process handles Drive upload after the game Activity has already closed,
# so Back/Quit is instant. ROM cache is removed only after a successful upload.
handler_path = "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/main/GameLaunchTaskHandler.kt"
replace(
    handler_path,
    '''import com.swordfish.lemuroid.app.shared.game.BaseGameActivity
import com.swordfish.lemuroid.app.shared.gamecrash.GameCrashActivity''',
    '''import com.swordfish.lemuroid.app.shared.drive.DriveSafGameSync
import com.swordfish.lemuroid.app.shared.game.BaseGameActivity
import com.swordfish.lemuroid.app.shared.gamecrash.GameCrashActivity'''
)
replace(
    handler_path,
    '''import kotlinx.coroutines.delay
''',
    '''import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import timber.log.Timber
'''
)
replace(
    handler_path,
    '''        val game = data?.extras?.getSerializable(BaseGameActivity.PLAY_GAME_RESULT_GAME) as Game

        updateGamePlayedTimestamp(game)
        if (enableRatingFlow) {''',
    '''        val game = data?.extras?.getSerializable(BaseGameActivity.PLAY_GAME_RESULT_GAME) as Game
        val coreName = data.getStringExtra(BaseGameActivity.PLAY_GAME_RESULT_CORE_NAME)

        updateGamePlayedTimestamp(game)
        if (coreName != null) {
            val context = activity.applicationContext
            driveSyncScope.launch {
                runCatching {
                    val dataFiles = retrogradeDb.dataFileDao().selectDataFilesForGame(game.id)
                    DriveSafGameSync(context).apply {
                        pushAfterGame(game, coreName)
                        deleteTemporaryRom(game, dataFiles)
                    }
                }.onFailure {
                    Timber.w(it, "Drive post-game sync failed; temporary ROM retained")
                }
            }
        }
        if (enableRatingFlow) {'''
)
replace(
    handler_path,
    '''    private suspend fun updateGamePlayedTimestamp(game: Game) {
        retrogradeDb.gameDao().update(game.copy(lastPlayedAt = System.currentTimeMillis()))
    }
}''',
    '''    private suspend fun updateGamePlayedTimestamp(game: Game) {
        retrogradeDb.gameDao().update(game.copy(lastPlayedAt = System.currentTimeMillis()))
    }

    companion object {
        private val driveSyncScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }
}'''
)

# Add an explicit "No filter" option. LibretroDroid documents Sharp as its raw,
# unfiltered image path, so the new value maps to the same raw renderer while
# keeping the original "Sharp" item intact.
keys_path = "lemuroid-app/src/main/res/values/keys.xml"
replace(
    keys_path,
    '''    <string-array translatable="false" name="pref_key_shader_filter_values">
        <item>auto</item>
        <item>sharp</item>''',
    '''    <string-array translatable="false" name="pref_key_shader_filter_values">
        <item>auto</item>
        <item>none</item>
        <item>sharp</item>'''
)
replace(
    keys_path,
    '''    <string-array translatable="false" name="pref_key_shader_filter_display_names">
        <item>@string/shader_filter_names_auto</item>
        <item>@string/shader_filter_names_sharp</item>''',
    '''    <string-array translatable="false" name="pref_key_shader_filter_display_names">
        <item>@string/shader_filter_names_auto</item>
        <item>@string/shader_filter_names_none</item>
        <item>@string/shader_filter_names_sharp</item>'''
)

for path, label in [
    ("lemuroid-app/src/main/res/values/strings.xml", "No filter"),
    ("lemuroid-app/src/main/res/values-es-rES/strings.xml", "Sin filtros"),
]:
    replace(
        path,
        '''    <string name="shader_filter_names_auto">''',
        f'''    <string name="shader_filter_names_none">{label}</string>\n    <string name="shader_filter_names_auto">'''
    )

shader_path = "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/game/ShaderChooser.kt"
replace(
    shader_path,
    '''                when (screenFilter) {
                    "crt" -> ShaderConfig.CRT''',
    '''                when (screenFilter) {
                    "none" -> ShaderConfig.Sharp
                    "crt" -> ShaderConfig.CRT'''
)

# Improve thumbnail reliability for Drive-indexed ROMs and use HTTPS.
metadata_path = "lemuroid-metadata-libretro-db/src/main/java/com/swordfish/lemuroid/metadata/libretrodb/LibretroDBMetadataProvider.kt"
replace(
    metadata_path,
    '''                thumbnail = null,
                system = it.id.dbname,''',
    '''                thumbnail = computeCoverUrl(it, file.extensionlessName),
                system = it.id.dbname,'''
)
# The previous replacement occurs in all three inferred-system metadata builders.
replace(
    metadata_path,
    '''        return "http://thumbnails.libretro.com/$systemName/$imageType/$thumbGameName.png"''',
    '''        return "https://thumbnails.libretro.com/$systemName/$imageType/$thumbGameName.png"'''
)

# Persist covers locally and mirror them into the selected Drive folder. The UI
# keeps using Coil, but repeat loads can use the local file and the Drive copy
# survives cache clearing / device changes.
cover_path = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/covers/CoverUtils.kt"
cover_path.write_text(r'''package com.swordfish.lemuroid.app.shared.covers

import android.content.Context
import android.net.Uri
import android.widget.ImageView
import androidx.documentfile.provider.DocumentFile
import coil.ImageLoader
import coil.disk.DiskCache
import coil.imageLoader
import coil.load
import coil.memory.MemoryCache
import coil.request.CachePolicy
import com.swordfish.lemuroid.common.drawable.TextDrawable
import com.swordfish.lemuroid.common.graphics.ColorUtils
import com.swordfish.lemuroid.lib.library.db.entity.Game
import com.swordfish.lemuroid.lib.preferences.SharedPreferencesHelper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.ConcurrentHashMap

object CoverUtils {
    private val coverScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val coverDownloads = ConcurrentHashMap.newKeySet<String>()
    private val coverClient = OkHttpClient.Builder().build()

    fun loadCover(
        game: Game,
        imageView: ImageView?,
    ) {
        if (imageView == null) return
        val context = imageView.context
        imageView.load(getCoverModel(context, game), context.imageLoader) {
            val fallbackDrawable = getFallbackDrawable(game)
            fallback(fallbackDrawable)
            error(fallbackDrawable)
        }
    }

    fun getCoverModel(context: Context, game: Game): Any? {
        val local = localCoverFile(context, game)
        if (local.isFile && local.length() > 0L) return local
        cacheCoverAsync(context.applicationContext, game)
        return game.coverFrontUrl
    }

    private fun cacheCoverAsync(context: Context, game: Game) {
        val key = coverFileName(game)
        if (!coverDownloads.add(key)) return
        coverScope.launch {
            try {
                val local = localCoverFile(context, game)
                if (local.isFile && local.length() > 0L) return@launch
                local.parentFile?.mkdirs()

                if (pullCoverFromDrive(context, game, local)) return@launch
                val url = game.coverFrontUrl ?: return@launch
                val request = Request.Builder().url(url).build()
                coverClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) return@use
                    val body = response.body ?: return@use
                    local.outputStream().use { output -> body.byteStream().use { it.copyTo(output) } }
                    if (local.length() > 0L) pushCoverToDrive(context, game, local)
                }
            } catch (_: Throwable) {
            } finally {
                coverDownloads.remove(key)
            }
        }
    }

    private fun localCoverFile(context: Context, game: Game): File =
        File(File(context.filesDir, "drive-cover-cache"), coverFileName(game))

    private fun coverFileName(game: Game): String {
        val base = "${game.systemId}_${game.fileName.substringBeforeLast(".")}".replace(Regex("[\\\\/:*?\"<>|]"), "_")
        return "${base.take(140)}.png"
    }

    private fun selectedTree(context: Context): DocumentFile? {
        val prefString = context.getString(com.swordfish.lemuroid.lib.R.string.pref_key_extenral_folder)
        val prefs = SharedPreferencesHelper.getLegacySharedPreferences(context)
        val uri = prefs.getString(prefString, null)?.let(Uri::parse) ?: return null
        return DocumentFile.fromTreeUri(context, uri)
    }

    private fun coversDirectory(context: Context, create: Boolean): DocumentFile? {
        val tree = selectedTree(context) ?: return null
        val existing = tree.findFile("Lemuroid Covers")
        if (existing?.isDirectory == true) return existing
        if (!create || !tree.canWrite()) return null
        existing?.delete()
        return tree.createDirectory("Lemuroid Covers")
    }

    private fun pullCoverFromDrive(context: Context, game: Game, local: File): Boolean {
        val dir = coversDirectory(context, create = false) ?: return false
        val baseName = game.fileName.substringBeforeLast(".")
        val remote =
            dir.findFile(coverFileName(game))
                ?: dir.findFile("$baseName.png")
                ?: dir.findFile("$baseName.jpg")
                ?: dir.findFile("$baseName.webp")
                ?: return false
        if (!remote.isFile || remote.length() <= 0L) return false
        context.contentResolver.openInputStream(remote.uri)?.use { input ->
            local.outputStream().use { output -> input.copyTo(output) }
        } ?: return false
        return local.length() > 0L
    }

    private fun pushCoverToDrive(context: Context, game: Game, local: File) {
        val dir = coversDirectory(context, create = true) ?: return
        var remote = dir.findFile(coverFileName(game))
        if (remote == null) remote = dir.createFile("image/png", coverFileName(game))
        val destination = remote ?: return
        context.contentResolver.openOutputStream(destination.uri, "wt")?.use { output ->
            local.inputStream().use { input -> input.copyTo(output) }
        }
    }

    fun buildImageLoader(applicationContext: Context): ImageLoader {
        return ImageLoader.Builder(applicationContext)
            .diskCache(
                DiskCache.Builder()
                    .directory(applicationContext.cacheDir.resolve("image_cache"))
                    .maxSizePercent(0.20)
                    .build(),
            )
            .memoryCache {
                MemoryCache.Builder(applicationContext)
                    .maxSizePercent(0.20)
                    .build()
            }
            .okHttpClient {
                OkHttpClient.Builder()
                    .addNetworkInterceptor(ThrottleFailedThumbnailsInterceptor)
                    .build()
            }
            .crossfade(true)
            .interceptorDispatcher(Dispatchers.IO)
            .diskCachePolicy(CachePolicy.ENABLED)
            .memoryCachePolicy(CachePolicy.ENABLED)
            .respectCacheHeaders(false)
            .build()
    }

    fun getFallbackDrawable(game: Game) = TextDrawable(computeTitle(game), computeColor(game))

    fun getFallbackRemoteUrl(game: Game): String {
        val color = Integer.toHexString(computeColor(game)).substring(2)
        val title = computeTitle(game)
        return "https://fakeimg.pl/512x512/$color/fff/?font=bebas&text=$title"
    }

    private fun computeTitle(game: Game): String {
        val sanitizedName = game.title.replace(Regex("\\(.*\\)"), "")
        return sanitizedName.asSequence()
            .filter { it.isDigit() or it.isUpperCase() or (it == '&') }
            .take(3)
            .joinToString("")
            .ifBlank { game.title.first().toString() }
            .capitalize()
    }

    private fun computeColor(game: Game): Int = ColorUtils.randomColor(game.title)
}
''')

for image_path in [
    "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/mobile/shared/compose/ui/LemuroidGameImage.kt",
    "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/mobile/shared/compose/ui/LemuroidSmallGameImage.kt",
]:
    replace(
        image_path,
        '''                .data(game.coverFrontUrl)''',
        '''                .data(CoverUtils.getCoverModel(LocalContext.current, game))'''
    )

print("Lemuroid Drive v1.1 patch applied.")
