package org.facility.updater;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.logging.Logger;

/**
 * One update cycle:
 *  1. Find the newest signed manifest (online, USB, or cached)
 *  2. Compare it with what the application reports as installed
 *  3. Download whatever is missing (any time of day; resumable)
 *  4. Inside the maintenance window (or immediately for urgent releases) install via the app's API,
 *     restart, verify the new version is live, and roll back if it is not
 *  5. Save state and report status to the central server
 */
public final class UpdateAgent {

    private static final Logger LOG = Logger.getLogger(UpdateAgent.class.getName());

    private final Config cfg;
    private final ManifestSource source;
    private final Downloader downloader;
    private final AppClient app;
    private final Reporter reporter;
    private final CoreUpdater coreUpdater;
    private final DbUpdater dbUpdater;
    private final Path stateFile;
    private LocalTime clock; // overridable for tests

    public UpdateAgent(Config cfg) throws Exception {
        this.cfg = cfg;
        Http http = new Http(cfg.getInt("http.connect-timeout-seconds", 20) * 1000,
                cfg.getInt("http.read-timeout-seconds", 120) * 1000);
        this.source = new ManifestSource(cfg, http);
        this.downloader = new Downloader(cfg, http);
        this.app = new AppClient(cfg, http);
        this.reporter = new Reporter(cfg, http);
        this.coreUpdater = new CoreUpdater(cfg, downloader, app);
        this.dbUpdater = new DbUpdater(cfg, downloader);
        this.stateFile = cfg.stateDir().resolve("state.json");
    }

    void setClock(LocalTime t) { this.clock = t; }

    public State runOnce(boolean dryRun) {
        State state = State.load(stateFile);
        state.lastRun = State.now();
        state.lastError = null;
        ReleaseManifest manifest = null;
        try {
            manifest = source.best(state.lastSequence, state);
            if (manifest == null) {
                state.lastResult = "no-manifest";
                LOG.warning("No verified release manifest available (offline and nothing cached yet)");
                return state;
            }
            if (manifest.sequence > state.lastSequence) {
                state.event("new-release-list", null, null,
                        "sequence " + manifest.sequence + " from " + (manifest.offline ? "USB " : "") + manifest.source);
                state.lastSequence = manifest.sequence;
            }

            // Database scripts: their own versioned stream, own window, own result - runs whether or not
            // the application is up and whatever happens with modules/core afterwards.
            dbUpdater.cycle(manifest, state, clock != null ? clock : LocalTime.now(), dryRun);

            boolean appUp = app.isHealthy();
            Map<String, AppClient.InstalledModule> installed = appUp ? app.listInstalled() : null;
            Map<String, String> known = installed != null ? lower(AppClient.versions(installed)) : lower(state.installed);
            if (installed != null) {
                state.installed = AppClient.versions(installed);
                if (installed.isEmpty()) {
                    LOG.warning("Module list returned no entries with both a name and a version - check "
                            + "app.api.list-modules.items-path / app.api.modules.name-field / version-field");
                }
            }
            boolean modulesUnknown = false;
            if (!appUp) {
                state.lastError = app.lastHealthProblem;
                LOG.warning(app.lastHealthProblem);
                if (known.isEmpty()) {
                    // Never seen the app's module list: we can't tell which modules need updating.
                    modulesUnknown = true;
                } else {
                    LOG.warning("Using last known module versions; will download but not install");
                }
            }

            // Core JAR + loose files (query.yml) - planned from the core folder, works even if the app is down
            CoreUpdater.Plan corePlan = new CoreUpdater.Plan();
            if (coreUpdater.enabled()) {
                try {
                    corePlan = coreUpdater.plan(manifest, state);
                } catch (IOException e) {
                    state.lastError = e.getMessage();
                    LOG.warning("Core check skipped: " + e.getMessage());
                }
            }

            List<ReleaseManifest.ModuleRelease> plan = modulesUnknown
                    ? new ArrayList<ReleaseManifest.ModuleRelease>() : plan(manifest, known, state);
            state.pending.clear();
            if (corePlan.core != null) state.pending.put(CoreUpdater.CORE_KEY, corePlan.core.version.toString());
            for (ReleaseManifest.ModuleRelease f : corePlan.files) state.pending.put(f.name, "new copy");
            for (ReleaseManifest.ModuleRelease r : plan) state.pending.put(r.name, r.version.toString());

            if (plan.isEmpty() && corePlan.isEmpty()) {
                state.lastResult = modulesUnknown ? "app-unavailable" : "up-to-date";
                if (!modulesUnknown) LOG.info("Everything up to date (release list " + manifest.sequence + ")");
                return state;
            }
            LOG.info("Updates needed: " + state.pending);
            if (dryRun) {
                state.lastResult = "dry-run";
                return state;
            }

            // ---- download (allowed any time)
            coreUpdater.download(corePlan, manifest, state);
            List<ReleaseManifest.ModuleRelease> ready = new ArrayList<ReleaseManifest.ModuleRelease>();
            for (ReleaseManifest.ModuleRelease r : plan) {
                try {
                    downloader.ensure(r, manifest);
                    ready.add(r);
                } catch (IOException e) {
                    state.lastError = e.getMessage();
                    state.event("download-failed", r.name, r.version.toString(), e.getMessage());
                    LOG.warning("Could not obtain " + r + ": " + e.getMessage());
                }
            }
            if (!appUp) { state.lastResult = "app-unavailable"; return state; }

            // ---- install: core + files first, then modules
            boolean inWindow = cfg.inInstallWindow(clock != null ? clock : LocalTime.now());
            int maxAttempts = cfg.getInt("install.max-attempts", 3);
            List<String> outcomes = new ArrayList<String>();


            // Core and files go first so modules that need the new core find it in place.
            String coreResult = "nothing";
            if (!corePlan.isEmpty() && (inWindow || corePlan.urgent())) {
                coreResult = coreUpdater.apply(corePlan, state);
                outcomes.add("failed".equals(coreResult) ? "core-failed" : coreResult);
                if ("failed".equals(coreResult) && !app.isHealthy()) {
                    state.lastResult = "core-failed";
                    return state;
                }
            }

            String core = app.coreVersion(installed);
            if (core == null && coreUpdater.enabled()) core = coreUpdater.currentVersionQuiet();
            List<ReleaseManifest.ModuleRelease> toInstall = new ArrayList<ReleaseManifest.ModuleRelease>();
            for (ReleaseManifest.ModuleRelease r : ready) {
                Long attempts = state.failedAttempts.get(r.name + "@" + r.version);
                if (attempts != null && attempts >= maxAttempts) {
                    LOG.warning("Skipping " + r + ": failed " + attempts + " times, waiting for a newer release");
                    continue;
                }
                if (r.minCoreVersion != null) {
                    if (core == null) {
                        LOG.warning(r + " needs core " + r.minCoreVersion + " but core version is unknown; proceeding");
                    } else if (Version.of(core).compareTo(r.minCoreVersion) < 0) {
                        state.event("blocked-needs-core", r.name, r.version.toString(),
                                "requires core " + r.minCoreVersion + ", facility has " + core);
                        continue;
                    }
                }
                if (inWindow || r.urgent) toInstall.add(r);
            }
            if (!toInstall.isEmpty()) {
                install(toInstall, known, state);
                outcomes.add(state.lastResult);
            }


            state.lastResult = combine(outcomes, inWindow);
            if (!inWindow && "downloaded-waiting-for-window".equals(state.lastResult)) {
                LOG.info("Updates downloaded; installation waits for the maintenance window ("
                        + cfg.get("install.window", "20:00-06:00") + ")");
            }
            refreshPending(state, corePlan);
        } catch (Exception e) {
            state.lastResult = "error";
            state.lastError = e.getClass().getSimpleName() + ": " + e.getMessage();
            LOG.severe("Update cycle failed: " + state.lastError);
        } finally {
            if (coreUpdater.enabled()) {
                String cv = coreUpdater.currentVersionQuiet();
                if (cv != null) state.installed.put(CoreUpdater.CORE_KEY, cv);
            }
            mergeDbStatus(state);
            if (!dryRun) {
                try { state.save(stateFile); } catch (IOException e) { LOG.severe("Could not save state: " + e.getMessage()); }
                reporter.report(state);
                downloader.prune(cfg.getInt("updater.cache-keep-versions", 2));
            }
        }
        return state;
    }

    /** Shows the DB stream in the shared status fields (installed / pending / result / error). */
    private void mergeDbStatus(State state) {
        if (!dbUpdater.enabled() || state.dbResult == null) return;
        if (state.dbVersion != null) state.installed.put("DB scripts", state.dbVersion);
        if (state.dbPending != null) state.pending.put("DB scripts", state.dbPending);
        else state.pending.remove("DB scripts");
        boolean dbFailed = "failed".equals(state.dbResult) || "error".equals(state.dbResult);
        if (dbFailed && state.lastError == null) state.lastError = state.dbError;
        String r = state.lastResult;
        boolean modulesIdle = "up-to-date".equals(r) || "downloaded-waiting-for-window".equals(r) || "blocked".equals(r)
                || "app-unavailable".equals(r) || "no-manifest".equals(r);
        if (dbFailed) {
            state.lastResult = modulesIdle ? "db-failed" : "updated".equals(r) ? "partially-updated" : r;
        } else if ("updated".equals(state.dbResult) && modulesIdle && !"app-unavailable".equals(r)) {
            state.lastResult = "updated";
        } else if ("downloaded-waiting-for-window".equals(state.dbResult) && "up-to-date".equals(r)) {
            state.lastResult = "downloaded-waiting-for-window";
        }
    }

    /** Overall result from the parts that ran ("nothing" = part had nothing to do this run). */
    static String combine(List<String> outcomes, boolean inWindow) {
        boolean ok = false, restart = false;
        List<String> failures = new ArrayList<String>();
        for (String o : outcomes) {
            if (o == null || "nothing".equals(o)) continue;
            if (o.contains("fail") || o.startsWith("partially")) failures.add(o);
            else ok = true;
            if (o.contains("restart-required")) restart = true;
        }
        if (!failures.isEmpty()) {
            if (ok || failures.contains("partially-updated")) return "partially-updated";
            return failures.size() == 1 ? failures.get(0) : "install-failed";
        }
        if (ok) return restart ? "updated-restart-required" : "updated";
        return inWindow ? "blocked" : "downloaded-waiting-for-window";
    }

    /** Recomputes "pending" for core/files after a run (module entries are set by install()). */
    private void refreshPending(State state, CoreUpdater.Plan corePlan) {
        if (corePlan.core != null) {
            String cv = coreUpdater.currentVersionQuiet();
            if (cv != null && Version.of(cv).equals(corePlan.core.version)) state.pending.remove(CoreUpdater.CORE_KEY);
            else state.pending.put(CoreUpdater.CORE_KEY, corePlan.core.version.toString());
        }
        for (ReleaseManifest.ModuleRelease f : corePlan.files) {
            try {
                Path target = cfg.path("core.dir", null).resolve(f.name);
                if (Files.isRegularFile(target) && f.sha256.equals(Signatures.sha256(target))) state.pending.remove(f.name);
                else state.pending.put(f.name, "new copy");
            } catch (IOException ignored) { }
        }
    }

    private List<ReleaseManifest.ModuleRelease> plan(ReleaseManifest m, Map<String, String> installed, State state) {
        boolean installNew = cfg.getBool("install.new-modules", false);
        List<ReleaseManifest.ModuleRelease> out = new ArrayList<ReleaseManifest.ModuleRelease>();
        // Forget "awaiting restart" entries once the application shows that version (or newer).
        for (java.util.Iterator<Map.Entry<String, String>> it = state.awaitingRestart.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<String, String> e = it.next();
            String cur = installed.get(e.getKey().toLowerCase());
            if (cur != null && Version.of(cur).compareTo(Version.of(e.getValue())) >= 0) {
                state.event("installed", e.getKey(), e.getValue(), "now live after application restart");
                it.remove();
            }
        }
        for (ReleaseManifest.ModuleRelease r : m.modules) {
            String waiting = state.awaitingRestart.get(r.name);
            if (waiting != null && Version.of(waiting).equals(r.version)) {
                LOG.info(r + " is already installed and waiting for the application to restart");
                continue;
            }
            String cur = installed.get(r.key());
            if (cur == null) {
                if (installNew) out.add(r);
                continue;
            }
            Version current = Version.of(cur);
            boolean needed = r.forceVersion ? !r.version.equals(current) : r.version.isNewerThan(current);
            if (needed) out.add(r);
        }
        return out;
    }

    private void install(List<ReleaseManifest.ModuleRelease> toInstall, Map<String, String> before, State state)
            throws IOException {
        List<ReleaseManifest.ModuleRelease> uploaded = new ArrayList<ReleaseManifest.ModuleRelease>();
        for (ReleaseManifest.ModuleRelease r : toInstall) {
            try {
                LOG.info("Installing " + r + " (currently " + before.get(r.key()) + ")");
                app.install(r, downloader.cached(r));
                uploaded.add(r);
            } catch (IOException e) {
                fail(state, r, e.getMessage());
            }
        }
        if (uploaded.isEmpty()) { state.lastResult = "install-failed"; return; }

        boolean restartConfigured = cfg.get("app.restart.command") != null;
        boolean healthy = app.restartAndWait();
        Map<String, String> after = healthy ? lower(AppClient.versions(app.listInstalled())) : new LinkedHashMap<String, String>();

        List<ReleaseManifest.ModuleRelease> failed = new ArrayList<ReleaseManifest.ModuleRelease>();
        for (ReleaseManifest.ModuleRelease r : uploaded) {
            String now = after.get(r.key());
            if (healthy && now != null && Version.of(now).equals(r.version)) {
                state.failedAttempts.remove(r.name + "@" + r.version);
                state.event("installed", r.name, r.version.toString(), "was " + before.get(r.key()));
                LOG.info("Verified " + r + " is live");
            } else if (healthy && !restartConfigured) {
                // Without a restart the app may keep reporting the old version: that's not a failure,
                // and rolling back here would undo a good update at the next manual restart.
                state.awaitingRestart.put(r.name, r.version.toString());
                state.failedAttempts.remove(r.name + "@" + r.version);
                state.event("awaiting-restart", r.name, r.version.toString(),
                        "uploaded; application still reports " + now + " until it is restarted");
                LOG.warning(r + " uploaded but the application still reports " + now
                        + ". It will show after the application restarts - set app.restart.command to automate this.");
            } else {
                fail(state, r, healthy ? "application reports version " + now + " after install"
                                       : "application did not come back healthy after install");
                failed.add(r);
            }
        }

        if (!failed.isEmpty() && cfg.getBool("rollback.enabled", true)) rollback(failed, before, state);

        if (healthy || app.isHealthy()) state.installed = AppClient.versions(app.listInstalled());
        state.pending.clear();
        for (ReleaseManifest.ModuleRelease r : toInstall) {
            if (!r.version.toString().equals(lower(state.installed).get(r.key()))
                    && !r.version.toString().equals(state.awaitingRestart.get(r.name))) state.pending.put(r.name, r.version.toString());
        }
        state.lastResult = !failed.isEmpty() || uploaded.size() != toInstall.size() ? "partially-updated"
                : state.awaitingRestart.isEmpty() ? "updated" : "updated-restart-required";
    }

    private void rollback(List<ReleaseManifest.ModuleRelease> failed, Map<String, String> before, State state)
            throws IOException {
        boolean any = false;
        for (ReleaseManifest.ModuleRelease r : failed) {
            String prev = before.get(r.key());
            Path jar = prev == null ? null : downloader.cachedVersion(r.name, prev);
            if (jar == null) {
                state.event("rollback-unavailable", r.name, prev, "no cached copy of the previous version");
                LOG.severe("Cannot roll back " + r.name + ": previous version " + prev + " not in cache");
                continue;
            }
            ReleaseManifest.ModuleRelease old = new ReleaseManifest.ModuleRelease();
            old.name = r.name;
            old.version = Version.of(prev);
            try {
                app.install(old, jar);
                any = true;
                state.event("rolled-back", r.name, prev, "after failed " + r.version);
            } catch (IOException e) {
                state.event("rollback-failed", r.name, prev, e.getMessage());
            }
        }
        if (any && !app.restartAndWait()) {
            state.lastError = "Application unhealthy after rollback - needs attention";
            LOG.severe(state.lastError);
        }
    }

    private void fail(State state, ReleaseManifest.ModuleRelease r, String why) {
        String k = r.name + "@" + r.version;
        Long n = state.failedAttempts.get(k);
        state.failedAttempts.put(k, n == null ? 1L : n + 1);
        state.lastError = r + ": " + why;
        state.event("install-failed", r.name, r.version.toString(), why);
        LOG.severe("Install of " + r + " failed: " + why);
    }

    private static Map<String, String> lower(Map<String, String> m) {
        Map<String, String> out = new LinkedHashMap<String, String>();
        for (Map.Entry<String, String> e : m.entrySet()) out.put(e.getKey().toLowerCase(), e.getValue());
        return out;
    }
}
