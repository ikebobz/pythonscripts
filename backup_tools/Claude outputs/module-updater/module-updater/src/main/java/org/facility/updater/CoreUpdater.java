package org.facility.updater;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.logging.Logger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Updates the core application JAR and loose files (e.g. query.yml) in the core folder.
 *
 * Core: copies lamisplus-NEW.jar next to the current one (the old JAR is kept), then rewrites the
 * launch batch file so it points at the new version, restarts the application and checks it comes back.
 * If it does not, the batch file and replaced files are restored from their backups and the app is
 * restarted again.
 *
 * Files: any file listed in the manifest's "files" section is copied into the core folder when its
 * SHA-256 differs from the copy already there (the previous copy is kept as name.bak).
 */
public final class CoreUpdater {

    private static final Logger LOG = Logger.getLogger(CoreUpdater.class.getName());
    static final String CORE_KEY = "Core";

    /** What needs doing for this release list. */
    public static final class Plan {
        public ReleaseManifest.ModuleRelease core;     // null if core is current
        public String currentCore;
        public final List<ReleaseManifest.ModuleRelease> files = new ArrayList<ReleaseManifest.ModuleRelease>();
        public boolean isEmpty() { return core == null && files.isEmpty(); }
        boolean urgent() {
            if (core != null && core.urgent) return true;
            for (ReleaseManifest.ModuleRelease f : files) if (f.urgent) return true;
            return false;
        }
    }

    private final Config cfg;
    private final Downloader downloader;
    private final AppClient app;

    public CoreUpdater(Config cfg, Downloader downloader, AppClient app) {
        this.cfg = cfg;
        this.downloader = downloader;
        this.app = app;
    }

    public boolean enabled() { return cfg.get("core.dir") != null; }

    private Path dir() { return cfg.path("core.dir", null); }

    private String prefix() { return cfg.get("core.jar-prefix", "lamisplus-"); }

    private Path batchFile() { return dir().resolve(cfg.require("core.batch-file")); }

    // ------------------------------------------------------------------ current version

    /**
     * The core version the batch file launches ("lamisplus-3.0.0.jar" inside it). If the batch file uses
     * a variable instead, falls back to the highest lamisplus-*.jar in the folder.
     */
    public String currentVersion() throws IOException {
        Path bat = batchFile();
        if (!Files.isRegularFile(bat)) throw new IOException("Core batch file not found: " + bat);
        Matcher m = jarPattern().matcher(read(bat));
        if (m.find()) return m.group(1);
        String best = null;
        File[] jars = dir().toFile().listFiles();
        if (jars != null) {
            for (File f : jars) {
                Matcher jm = jarPattern().matcher(f.getName());
                if (jm.matches() && (best == null || Version.of(jm.group(1)).isNewerThan(Version.of(best)))) {
                    best = jm.group(1);
                }
            }
        }
        if (best == null) throw new IOException("Cannot tell the current core version: no " + prefix()
                + "x.x.x.jar named in " + bat.getFileName() + " or present in " + dir());
        return best;
    }

    public String currentVersionQuiet() {
        try { return currentVersion(); } catch (Exception e) { return null; }
    }

    private Pattern jarPattern() {
        return Pattern.compile(Pattern.quote(prefix()) + "(\\d[0-9A-Za-z.\\-_]*?)\\.jar", Pattern.CASE_INSENSITIVE);
    }

    // ------------------------------------------------------------------ plan

    public Plan plan(ReleaseManifest m, State state) throws IOException {
        Plan p = new Plan();
        if (!enabled()) return p;
        int maxAttempts = cfg.getInt("install.max-attempts", 3);

        if (m.core != null) {
            p.currentCore = currentVersion();
            Version cur = Version.of(p.currentCore);
            boolean needed = m.core.forceVersion ? !m.core.version.equals(cur) : m.core.version.isNewerThan(cur);
            Long attempts = state.failedAttempts.get(CORE_KEY + "@" + m.core.version);
            if (needed && attempts != null && attempts >= maxAttempts) {
                LOG.warning("Skipping core " + m.core.version + ": failed " + attempts + " times, waiting for a newer release");
            } else if (needed) {
                m.core.cacheName = prefix() + m.core.version + ".jar";
                p.core = m.core;
            }
        }
        for (ReleaseManifest.ModuleRelease f : m.files) {
            Path target = dir().resolve(f.name);
            boolean same = Files.isRegularFile(target) && f.sha256.equals(Signatures.sha256(target));
            Long attempts = state.failedAttempts.get(f.name + "@" + f.sha256.substring(0, 12));
            if (!same && (attempts == null || attempts < maxAttempts)) p.files.add(f);
        }
        return p;
    }

    /** @return false if anything could not be downloaded */
    public boolean download(Plan p, ReleaseManifest m, State state) {
        boolean ok = true;
        List<ReleaseManifest.ModuleRelease> all = new ArrayList<ReleaseManifest.ModuleRelease>(p.files);
        if (p.core != null) all.add(0, p.core);
        for (ReleaseManifest.ModuleRelease r : all) {
            try {
                downloader.ensure(r, m);
            } catch (IOException e) {
                ok = false;
                state.lastError = e.getMessage();
                state.event("download-failed", r.name, label(r), e.getMessage());
                LOG.warning("Could not obtain " + r.name + " " + label(r) + ": " + e.getMessage());
            }
        }
        return ok;
    }

    // ------------------------------------------------------------------ apply

    /**
     * Installs the planned core and files, restarts and verifies.
     * @return "updated", "updated-restart-required", "failed" or "nothing"
     */
    public String apply(Plan p, State state) throws IOException {
        Path dir = dir();
        List<Path[]> restore = new ArrayList<Path[]>();   // {backup, target}
        List<ReleaseManifest.ModuleRelease> applied = new ArrayList<ReleaseManifest.ModuleRelease>();
        String oldCore = p.currentCore;

        try {
            // Loose files first (e.g. query.yml)
            for (ReleaseManifest.ModuleRelease f : p.files) {
                Path cached = downloader.cached(f);
                if (!Files.isRegularFile(cached)) continue;              // download failed earlier
                Path target = dir.resolve(f.name);
                if (Files.isRegularFile(target)) {
                    Path bak = dir.resolve(f.name + ".bak");
                    Files.copy(target, bak, StandardCopyOption.REPLACE_EXISTING);
                    restore.add(new Path[]{bak, target});
                }
                copyAtomic(cached, target);
                applied.add(f);
                LOG.info("Replaced " + target);
            }

            // Core JAR + batch file
            if (p.core != null && Files.isRegularFile(downloader.cached(p.core))) {
                String newVer = p.core.version.toString();
                Path bat = batchFile();
                String original = read(bat);
                String updated = rewriteBatch(original, oldCore, newVer);  // throws if nothing to replace

                Path newJar = dir.resolve(prefix() + newVer + ".jar");
                copyAtomic(downloader.cached(p.core), newJar);
                Path bak = bat.resolveSibling(bat.getFileName() + ".bak");
                Files.copy(bat, bak, StandardCopyOption.REPLACE_EXISTING);
                restore.add(new Path[]{bak, bat});
                write(bat, updated);
                applied.add(p.core);
                LOG.info("Core " + oldCore + " -> " + newVer + ": copied " + newJar.getFileName()
                        + " and updated " + bat.getFileName() + " (old JAR kept, backup " + bak.getFileName() + ")");
            }
        } catch (IOException e) {
            LOG.severe("Core/file update failed before restart: " + e.getMessage());
            restoreAll(restore);
            for (ReleaseManifest.ModuleRelease r : p.isEmpty() ? new ArrayList<ReleaseManifest.ModuleRelease>() : all(p)) {
                fail(state, r, e.getMessage());
            }
            return "failed";
        }
        if (applied.isEmpty()) return "nothing";

        if (cfg.get("app.restart.command") == null) {
            for (ReleaseManifest.ModuleRelease r : applied) {
                state.event("awaiting-restart", r.name, label(r), "in place; takes effect when the application restarts");
            }
            LOG.warning("Core/files are in place but take effect only after the application restarts"
                    + " - set app.restart.command to automate this");
            return "updated-restart-required";
        }

        boolean healthy = app.restartAndWait();
        String reported = healthy ? app.coreVersion(java.util.Collections.<String, AppClient.InstalledModule>emptyMap()) : null;
        boolean coreOk = p.core == null || !applied.contains(p.core) || reported == null
                || Version.of(reported).equals(p.core.version);
        if (healthy && coreOk) {
            for (ReleaseManifest.ModuleRelease r : applied) {
                state.failedAttempts.remove(failKey(r));
                state.event("installed", r.name, label(r),
                        r == p.core ? "was " + oldCore : "replaced in " + dir.getFileName());
            }
            return "updated";
        }

        String why = !healthy ? "application did not come back healthy after restart"
                              : "application reports core " + reported + " after restart";
        for (ReleaseManifest.ModuleRelease r : applied) fail(state, r, why);
        if (cfg.getBool("rollback.enabled", true)) {
            restoreAll(restore);
            state.event("rolled-back", CORE_KEY, oldCore, "restored " + restore.size() + " file(s) after: " + why);
            LOG.severe("Rolled back core/files (" + why + "); restarting again");
            if (!app.restartAndWait()) {
                state.lastError = "Application unhealthy after core rollback - needs attention";
                LOG.severe(state.lastError);
            }
        }
        return "failed";
    }

    /**
     * Replaces "lamisplus-OLD.jar" with "lamisplus-NEW.jar". If the batch file doesn't name the JAR
     * directly (e.g. lamisplus-%VERSION%.jar), replaces the bare old version number instead.
     */
    String rewriteBatch(String content, String oldVer, String newVer) throws IOException {
        Pattern jar = Pattern.compile(Pattern.quote(prefix() + oldVer + ".jar"), Pattern.CASE_INSENSITIVE);
        Matcher m = jar.matcher(content);
        if (m.find()) return m.replaceAll(Matcher.quoteReplacement(prefix() + newVer + ".jar"));
        Pattern bare = Pattern.compile("(?<![\\w.])" + Pattern.quote(oldVer) + "(?![\\w]|\\.\\d)");
        Matcher b = bare.matcher(content);
        int count = 0;
        while (b.find()) count++;
        if (count == 0) {
            throw new IOException("Batch file " + cfg.get("core.batch-file") + " contains neither "
                    + prefix() + oldVer + ".jar nor the version " + oldVer + " - not changed");
        }
        LOG.info("Batch file has no " + prefix() + oldVer + ".jar; replacing " + count + " occurrence(s) of " + oldVer);
        return bare.matcher(content).replaceAll(Matcher.quoteReplacement(newVer));
    }

    // ------------------------------------------------------------------ helpers

    private static List<ReleaseManifest.ModuleRelease> all(Plan p) {
        List<ReleaseManifest.ModuleRelease> l = new ArrayList<ReleaseManifest.ModuleRelease>(p.files);
        if (p.core != null) l.add(p.core);
        return l;
    }

    private void fail(State state, ReleaseManifest.ModuleRelease r, String why) {
        String k = failKey(r);
        Long n = state.failedAttempts.get(k);
        state.failedAttempts.put(k, n == null ? 1L : n + 1);
        state.lastError = r.name + " " + label(r) + ": " + why;
        state.event("install-failed", r.name, label(r), why);
    }

    private static String failKey(ReleaseManifest.ModuleRelease r) {
        return r.isFile ? r.name + "@" + r.sha256.substring(0, 12) : CORE_KEY + "@" + r.version;
    }

    static String label(ReleaseManifest.ModuleRelease r) {
        return r.isFile ? "sha256 " + r.sha256.substring(0, 12) : r.version.toString();
    }

    private static void restoreAll(List<Path[]> restore) {
        for (Path[] pr : restore) {
            try {
                Files.copy(pr[0], pr[1], StandardCopyOption.REPLACE_EXISTING);
            } catch (IOException e) {
                LOG.severe("Could not restore " + pr[1] + " from " + pr[0] + ": " + e.getMessage());
            }
        }
    }

    private static void copyAtomic(Path src, Path target) throws IOException {
        Path tmp = target.resolveSibling(target.getFileName() + ".updating");
        Files.copy(src, tmp, StandardCopyOption.REPLACE_EXISTING);
        Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING);
    }

    // Byte-preserving: ISO-8859-1 maps every byte to one char, so encoding and CRLF line endings survive.
    private static String read(Path p) throws IOException {
        return new String(Files.readAllBytes(p), StandardCharsets.ISO_8859_1);
    }

    private static void write(Path p, String s) throws IOException {
        Path tmp = p.resolveSibling(p.getFileName() + ".updating");
        Files.write(tmp, s.getBytes(StandardCharsets.ISO_8859_1));
        Files.move(tmp, p, StandardCopyOption.REPLACE_EXISTING);
    }
}
