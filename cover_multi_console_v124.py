#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

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
import com.swordfish.lemuroid.lib.library.GameSystem
import com.swordfish.lemuroid.lib.library.SystemID
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
        val models = getCoverModels(context, game)
        loadImageViewCandidate(game, imageView, models, 0)
    }

    private fun loadImageViewCandidate(
        game: Game,
        imageView: ImageView,
        models: List<Any>,
        index: Int,
    ) {
        val model = models.getOrNull(index)
        imageView.load(model, imageView.context.imageLoader) {
            val fallbackDrawable = getFallbackDrawable(game)
            fallback(fallbackDrawable)
            error(fallbackDrawable)
            listener(
                onError = { _, _ ->
                    if (index + 1 < models.size) {
                        imageView.post {
                            loadImageViewCandidate(game, imageView, models, index + 1)
                        }
                    }
                },
            )
        }
    }

    fun getCoverModel(context: Context, game: Game): Any? =
        getCoverModels(context, game).firstOrNull()

    fun getCoverModels(context: Context, game: Game): List<Any> {
        val result = mutableListOf<Any>()
        val local = localCoverFile(context, game)
        if (local.isFile && local.length() > 0L) {
            result.add(local)
        }

        candidateUrls(game).forEach { url ->
            if (!result.contains(url)) result.add(url)
        }

        cacheCoverAsync(context.applicationContext, game)
        return result
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

                for (url in candidateUrls(game)) {
                    val ok = runCatching {
                        val request = Request.Builder().url(url).build()
                        coverClient.newCall(request).execute().use { response ->
                            if (!response.isSuccessful) return@use false
                            val body = response.body ?: return@use false
                            val type = body.contentType()?.type
                            if (type != null && type != "image") return@use false
                            local.outputStream().use { output ->
                                body.byteStream().use { input -> input.copyTo(output) }
                            }
                            local.length() > 0L
                        }
                    }.getOrDefault(false)

                    if (ok) {
                        pushCoverToDrive(context, game, local)
                        return@launch
                    } else {
                        local.delete()
                    }
                }
            } catch (_: Throwable) {
            } finally {
                coverDownloads.remove(key)
            }
        }
    }

    private fun candidateUrls(game: Game): List<String> {
        val urls = linkedSetOf<String>()

        game.coverFrontUrl
            ?.takeIf { it.isNotBlank() }
            ?.replace("http://", "https://")
            ?.let(urls::add)

        val system = runCatching { GameSystem.findById(game.systemId) }.getOrNull()
            ?: return urls.toList()

        var systemName = system.libretroFullName
        if (system.id == SystemID.MAME2003PLUS) systemName = "MAME"

        val names = candidateNames(game)
        val imageTypes = listOf("Named_Boxarts", "Named_Titles", "Named_Snaps")

        imageTypes.forEach { imageType ->
            names.forEach { name ->
                val safeName = sanitizeThumbnailName(name)
                if (safeName.isNotBlank()) {
                    urls.add(
                        "https://thumbnails.libretro.com/" +
                            Uri.encode(systemName) + "/" +
                            Uri.encode(imageType) + "/" +
                            Uri.encode(safeName) + ".png",
                    )
                }
            }
        }

        return urls.toList()
    }

    private fun candidateNames(game: Game): List<String> {
        val names = linkedSetOf<String>()
        val title = game.title.trim()
        val fileBase = game.fileName.substringBeforeLast(".").trim()

        if (title.isNotBlank()) names.add(title)
        if (fileBase.isNotBlank()) names.add(fileBase)

        listOf(title, fileBase)
            .filter { it.isNotBlank() }
            .forEach { original ->
                val withoutSquareTags = original.replace(Regex("\\s*\\[[^]]*]\\s*$"), "").trim()
                if (withoutSquareTags.isNotBlank()) names.add(withoutSquareTags)

                val withoutRegion =
                    original.replace(
                        Regex(
                            "\\s*\\((USA|Europe|World|Japan|Australia|Korea|Brazil|Canada|Asia|En[^)]*|Rev[^)]*|Beta[^)]*|Proto[^)]*)\\)\\s*$",
                            RegexOption.IGNORE_CASE,
                        ),
                        "",
                    ).trim()
                if (withoutRegion.isNotBlank()) names.add(withoutRegion)
            }

        return names.toList()
    }

    private fun sanitizeThumbnailName(name: String): String =
        name.replace(Regex("[&*/:`<>?\\\\|]"), "_")

    private fun localCoverFile(context: Context, game: Game): File =
        File(File(context.filesDir, "drive-cover-cache"), coverFileName(game))

    private fun coverFileName(game: Game): String {
        val base =
            "${game.systemId}_${game.fileName.substringBeforeLast(".")}".replace(
                Regex("[\\\\/:*?\"<>|]"),
                "_",
            )
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
        val systemDir = dir.findFile(game.systemId)?.takeIf { it.isDirectory }

        val candidates =
            listOfNotNull(
                dir.findFile(coverFileName(game)),
                dir.findFile("$baseName.png"),
                dir.findFile("$baseName.jpg"),
                dir.findFile("$baseName.jpeg"),
                dir.findFile("$baseName.webp"),
                systemDir?.findFile("$baseName.png"),
                systemDir?.findFile("$baseName.jpg"),
                systemDir?.findFile("$baseName.jpeg"),
                systemDir?.findFile("$baseName.webp"),
            )

        val remote = candidates.firstOrNull { it.isFile && it.length() > 0L } ?: return false
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
            .ifBlank { game.title.firstOrNull()?.toString() ?: "?" }
            .capitalize()
    }

    private fun computeColor(game: Game): Int = ColorUtils.randomColor(game.title)
}
''')

large = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/mobile/shared/compose/ui/LemuroidGameImage.kt"
large.write_text(r'''package com.swordfish.lemuroid.app.mobile.shared.compose.ui

import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.google.accompanist.drawablepainter.rememberDrawablePainter
import com.swordfish.lemuroid.app.shared.covers.CoverUtils
import com.swordfish.lemuroid.lib.library.db.entity.Game

@Composable
fun LemuroidGameImage(
    modifier: Modifier = Modifier,
    game: Game,
) {
    val context = LocalContext.current
    val fallbackDrawable = remember(game) { CoverUtils.getFallbackDrawable(game) }
    val fallbackPainter = rememberDrawablePainter(drawable = fallbackDrawable)
    val coverModels =
        remember(game.id, game.title, game.fileName, game.coverFrontUrl) {
            CoverUtils.getCoverModels(context, game)
        }
    var coverIndex by remember(game.id, game.coverFrontUrl) { mutableIntStateOf(0) }
    val model = coverModels.getOrNull(coverIndex)

    AsyncImage(
        model =
            ImageRequest.Builder(context)
                .data(model)
                .listener(
                    onError = { _, _ ->
                        if (coverIndex + 1 < coverModels.size) coverIndex += 1
                    },
                )
                .build(),
        contentDescription = game.title,
        modifier = modifier.fillMaxWidth().aspectRatio(1.0f),
        fallback = fallbackPainter,
        error = fallbackPainter,
        contentScale = ContentScale.Crop,
    )
}
''')

small = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/mobile/shared/compose/ui/LemuroidSmallGameImage.kt"
small.write_text(r'''package com.swordfish.lemuroid.app.mobile.shared.compose.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.surfaceColorAtElevation
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.google.accompanist.drawablepainter.rememberDrawablePainter
import com.swordfish.lemuroid.app.shared.covers.CoverUtils
import com.swordfish.lemuroid.lib.library.db.entity.Game

@Composable
fun LemuroidSmallGameImage(
    modifier: Modifier = Modifier,
    game: Game,
) {
    val context = LocalContext.current
    val fallbackDrawable = remember(game) { CoverUtils.getFallbackDrawable(game) }
    val fallbackPainter = rememberDrawablePainter(fallbackDrawable)
    val coverModels =
        remember(game.id, game.title, game.fileName, game.coverFrontUrl) {
            CoverUtils.getCoverModels(context, game)
        }
    var coverIndex by remember(game.id, game.coverFrontUrl) { mutableIntStateOf(0) }
    val model = coverModels.getOrNull(coverIndex)

    AsyncImage(
        model =
            ImageRequest.Builder(context)
                .data(model)
                .listener(
                    onError = { _, _ ->
                        if (coverIndex + 1 < coverModels.size) coverIndex += 1
                    },
                )
                .build(),
        contentDescription = game.title,
        modifier =
            modifier
                .fillMaxWidth()
                .aspectRatio(1.0f)
                .background(MaterialTheme.colorScheme.surfaceColorAtElevation(2.dp)),
        fallback = fallbackPainter,
        error = fallbackPainter,
        contentScale = ContentScale.Crop,
    )
}
''')

# Bump APK version over v1.2.3.
gradle = root / "lemuroid-app/build.gradle.kts"
g = gradle.read_text()
g = g.replace("versionCode = 256", "versionCode = 257", 1)
g = g.replace('versionNameSuffix = "-DRIVE-1.2.3"', 'versionNameSuffix = "-DRIVE-1.2.4"', 1)
if '-DRIVE-1.2.4' not in g:
    raise SystemExit("Could not bump version to 1.2.4")
gradle.write_text(g)

print("Lemuroid Drive v1.2.4 multi-console cover fallback patch applied")
