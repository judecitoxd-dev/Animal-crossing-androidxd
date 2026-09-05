#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

# 1) Make directory failures go to the END of the queue so one flaky Drive
# folder cannot block the remaining library. Keep retrying it later.
saf = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/storage/local/StorageAccessFrameworkProvider.kt"
text = saf.read_text()
old = '''                        pendingDirectories.add(
                            0,
                            currentDirectory.copy(retryCount = currentDirectory.retryCount + 1),
                        )
                        continue
'''
new = '''                        // Move a temporarily failing directory to the end of the queue.
                        // This lets the scan continue through the rest of Drive instead
                        // of appearing to stop at the same folder.
                        pendingDirectories.add(
                            currentDirectory.copy(retryCount = currentDirectory.retryCount + 1),
                        )
                        continue
'''
if old not in text:
    raise SystemExit("Could not find pendingDirectories retry block")
text = text.replace(old, new, 1)
text = text.replace(
    "private const val MAX_DIRECTORY_RETRIES = 5",
    "private const val MAX_DIRECTORY_RETRIES = 12",
    1,
)
text = text.replace(
    "private const val DIRECTORY_RETRY_DELAY_MS = 1_000L",
    "private const val DIRECTORY_RETRY_DELAY_MS = 800L",
    1,
)
saf.write_text(text)

# 2) The v1.2.1 safety patch preserved the DB on failure, but swallowed the
# exception. Propagate it after skipping cleanup so WorkManager can actually
# retry automatically.
library = root / "retrograde-app-shared/src/main/java/com/swordfish/lemuroid/lib/library/LemuroidLibrary.kt"
text = library.read_text()
old = '''    suspend fun indexLibrary() {
        val startedAtMs = System.currentTimeMillis()
        var completedSuccessfully = false

        try {
            indexProviders(startedAtMs)
            completedSuccessfully = true
        } catch (e: Throwable) {
            Timber.e("Library indexing stopped due to exception", e)
        } finally {
            if (completedSuccessfully) {
                cleanUp(startedAtMs)
            } else {
                Timber.w("Skipping library cleanup because indexing did not complete")
            }
        }

        val executionTime = System.currentTimeMillis() - startedAtMs
        Timber.i("Library indexing completed in: $executionTime ms")
    }
'''
new = '''    suspend fun indexLibrary() {
        val startedAtMs = System.currentTimeMillis()
        var completedSuccessfully = false
        var indexingFailure: Throwable? = null

        try {
            indexProviders(startedAtMs)
            completedSuccessfully = true
        } catch (e: Throwable) {
            indexingFailure = e
            Timber.e("Library indexing stopped due to exception", e)
        } finally {
            if (completedSuccessfully) {
                cleanUp(startedAtMs)
            } else {
                Timber.w("Skipping library cleanup because indexing did not complete")
            }
        }

        val executionTime = System.currentTimeMillis() - startedAtMs
        Timber.i("Library indexing completed in: $executionTime ms")

        // Let the Android worker know this pass was incomplete. Existing games
        // stay intact because cleanup above was skipped.
        indexingFailure?.let { throw it }
    }
'''
if old not in text:
    raise SystemExit("Could not find v1.2.1 indexLibrary block")
text = text.replace(old, new, 1)
library.write_text(text)

# 3) Retry transient Drive failures automatically inside the foreground worker
# before falling back to WorkManager's own retry mechanism.
worker = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/library/LibraryIndexWork.kt"
text = worker.read_text()
if "import kotlinx.coroutines.delay\n" not in text:
    text = text.replace(
        "import kotlinx.coroutines.Dispatchers\n",
        "import kotlinx.coroutines.Dispatchers\nimport kotlinx.coroutines.delay\n",
        1,
    )
old = '''        val result =
            withContext(Dispatchers.IO) {
                kotlin.runCatching {
                    lemuroidLibrary.indexLibrary()
                }
            }

        result.exceptionOrNull()?.let {
            Timber.e("Library indexing work terminated with an exception:", it)
        }

        LibraryIndexScheduler.scheduleCoreUpdate(applicationContext)

        return Result.success()
'''
new = '''        var lastFailure: Throwable? = null

        for (attempt in 1..MAX_AUTOMATIC_SCAN_ATTEMPTS) {
            val result =
                withContext(Dispatchers.IO) {
                    kotlin.runCatching {
                        lemuroidLibrary.indexLibrary()
                    }
                }

            if (result.isSuccess) {
                LibraryIndexScheduler.scheduleCoreUpdate(applicationContext)
                return Result.success()
            }

            lastFailure = result.exceptionOrNull()
            Timber.w(
                lastFailure,
                "Drive/library scan pass %d/%d failed; resuming automatically",
                attempt,
                MAX_AUTOMATIC_SCAN_ATTEMPTS,
            )

            if (attempt < MAX_AUTOMATIC_SCAN_ATTEMPTS) {
                delay(AUTOMATIC_SCAN_RETRY_DELAY_MS)
            }
        }

        Timber.e(
            lastFailure,
            "Library indexing still incomplete; asking WorkManager to retry automatically",
        )
        return Result.retry()
'''
if old not in text:
    raise SystemExit("Could not find LibraryIndexWork result block")
text = text.replace(old, new, 1)

# Add constants inside worker class before the Dagger module.
marker = '''    @dagger.Module(subcomponents = [Subcomponent::class])
'''
insert = '''    companion object {
        private const val MAX_AUTOMATIC_SCAN_ATTEMPTS = 8
        private const val AUTOMATIC_SCAN_RETRY_DELAY_MS = 2_000L
    }

    @dagger.Module(subcomponents = [Subcomponent::class])
'''
if marker not in text:
    raise SystemExit("Could not find LibraryIndexWork module marker")
text = text.replace(marker, insert, 1)
worker.write_text(text)

# 4) Explicit short linear backoff for the rare case all in-worker retries fail.
scheduler = root / "lemuroid-app/src/main/java/com/swordfish/lemuroid/app/shared/library/LibraryIndexScheduler.kt"
text = scheduler.read_text()
if "import androidx.work.BackoffPolicy\n" not in text:
    text = text.replace(
        "import android.content.Context\n",
        "import android.content.Context\nimport androidx.work.BackoffPolicy\n",
        1,
    )
if "import java.util.concurrent.TimeUnit\n" not in text:
    text = text.replace(
        "import androidx.work.WorkManager\n",
        "import androidx.work.WorkManager\nimport java.util.concurrent.TimeUnit\n",
        1,
    )
old = '''                OneTimeWorkRequestBuilder<LibraryIndexWork>().build(),
'''
new = '''                OneTimeWorkRequestBuilder<LibraryIndexWork>()
                    .setBackoffCriteria(BackoffPolicy.LINEAR, 10, TimeUnit.SECONDS)
                    .build(),
'''
if old not in text:
    raise SystemExit("Could not find library work request builder")
text = text.replace(old, new, 1)
scheduler.write_text(text)

# 5) Bump APK version over v1.2.2.
gradle = root / "lemuroid-app/build.gradle.kts"
g = gradle.read_text()
g = g.replace("versionCode = 255", "versionCode = 256", 1)
g = g.replace('versionNameSuffix = "-DRIVE-1.2.2"', 'versionNameSuffix = "-DRIVE-1.2.3"', 1)
if '-DRIVE-1.2.3' not in g:
    raise SystemExit("Could not bump version to 1.2.3")
gradle.write_text(g)

print("Lemuroid Drive v1.2.3 automatic scan resume patch applied")
