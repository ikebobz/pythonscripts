package org.facility.updater;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.SimpleDateFormat;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.logging.Logger;

/**
 * Runs the release's versioned "db_updates" SQL scripts against the facility's local Postgres database
 * with psql - on its own schedule, independent of module and core updates.
 *
 * The database's script version is the highest version recorded in a tracking table inside that same
 * database (db.tracking-table). Every published script with a higher version is run once, in version
 * order. Keeping the record in the database means that deleting the agent's state folder, reinstalling
 * the agent, or restoring a database backup can never make a script run twice against the same data.
 *
 * By default the script and its tracking row run in one transaction (ON_ERROR_STOP), so a failing
 * script leaves the database exactly as it was. Scripts that can't run in a transaction
 * (e.g. CREATE INDEX CONCURRENTLY, VACUUM) set "transaction": false in the manifest.
 */
public final class DbUpdater {

    private static final Logger LOG = Logger.getLogger(DbUpdater.class.getName());

    private final Config cfg;
    private final Downloader downloader;

    public DbUpdater(Config cfg, Downloader downloader) {
        this.cfg = cfg;
        this.downloader = downloader;
    }

    public boolean enabled() { return cfg.get("db.name") != null; }

    private String table() {
        String t = cfg.get("db.tracking-table", "public.module_updater_db_scripts");
        if (!t.matches("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)?")) {
            throw new IllegalStateException("db.tracking-table must be a plain [schema.]table name");
        }
        return t;
    }

    // ------------------------------------------------------------------ independent cycle

    /**
     * One DB-update pass: check the database's script version, download newer scripts, run them in
     * version order (inside db.window, or straight away if the newest pending script is urgent).
     * Never touches modules/core, and module/core problems never stop it.
     */
    public void cycle(ReleaseManifest m, State state, LocalTime now, boolean dryRun) {
        state.dbError = null;
        if (!enabled()) return;
        try {
            Map<String, String> applied = applied();
            state.dbVersion = maxVersion(applied);
            List<ReleaseManifest.ModuleRelease> pending = plan(m, state, applied);
            state.dbPending = pending.isEmpty() ? null : pending.get(pending.size() - 1).version.toString();
            if (pending.isEmpty()) {
                state.dbResult = "up-to-date";
                return;
            }
            LOG.info("DB scripts to run: " + versions(pending) + " (database is at "
                    + (state.dbVersion == null ? "no scripts yet" : "version " + state.dbVersion) + ")");
            if (dryRun) {
                state.dbResult = "dry-run";
                return;
            }
            download(pending, m, state);
            boolean urgent = pending.get(pending.size() - 1).urgent;
            if (!cfg.inWindow("db.window", cfg.get("install.window", "20:00-06:00"), now) && !urgent) {
                state.dbResult = "downloaded-waiting-for-window";
                LOG.info("DB scripts downloaded; they run in the DB window ("
                        + cfg.get("db.window", cfg.get("install.window", "20:00-06:00")) + ")");
                return;
            }
            String r = run(pending, state);
            state.dbResult = "failed".equals(r) ? "failed" : "nothing".equals(r) ? "blocked" : "updated";
            state.dbVersion = maxVersion(applied());
            List<ReleaseManifest.ModuleRelease> left = plan(m, state, applied());
            state.dbPending = left.isEmpty() ? null : left.get(left.size() - 1).version.toString();
        } catch (Exception e) {
            state.dbResult = "error";
            state.dbError = e.getMessage();
            LOG.warning("DB updates skipped this run: " + e.getMessage());
        }
    }

    // ------------------------------------------------------------------ plan

    /**
     * Published scripts newer than the database's current script version, in version order.
     * Stops at a script that has failed too often: later versions may depend on it.
     */
    public List<ReleaseManifest.ModuleRelease> plan(ReleaseManifest m, State state, Map<String, String> applied) {
        List<ReleaseManifest.ModuleRelease> out = new ArrayList<ReleaseManifest.ModuleRelease>();
        if (!enabled() || m.dbUpdates.isEmpty()) return out;
        String cur = maxVersion(applied);
        int maxAttempts = cfg.getInt("install.max-attempts", 3);
        for (ReleaseManifest.ModuleRelease r : m.dbUpdates) {
            String appliedSha = findVersion(applied, r.version);
            if (appliedSha != null) {
                if (!appliedSha.isEmpty() && !appliedSha.equals(r.sha256)) {
                    LOG.warning("DB script version " + r.version + " was changed after it ran here; changed copy is NOT "
                            + "run again - publish changes under a new version");
                }
                continue;
            }
            if (cur != null && !r.version.isNewerThan(Version.of(cur))) {
                LOG.warning("DB script version " + r.version + " is older than this database's version " + cur
                        + " and was never run here - skipped (versions must only go up)");
                continue;
            }
            Long attempts = state.failedAttempts.get(failKey(r));
            if (attempts != null && attempts >= maxAttempts) {
                LOG.warning("DB script version " + r.version + " failed " + attempts
                        + " times; it and later versions wait until a corrected script is published");
                break;
            }
            out.add(r);
        }
        return out;
    }

    /**
     * Versions already applied to this database -> SHA-256 of the script that ran
     * (creates/upgrades the tracking table on first use).
     */
    public Map<String, String> applied() throws IOException {
        String t = table();
        String sql = "CREATE TABLE IF NOT EXISTS " + t + " ("
                + "sha256 varchar(64) PRIMARY KEY, name varchar(255) NOT NULL, "
                + "applied_at timestamptz NOT NULL DEFAULT now(), agent_version varchar(32), notes text); "
                + "ALTER TABLE " + t + " ADD COLUMN IF NOT EXISTS version varchar(64); "
                + "SELECT coalesce(version, '') || '|' || sha256 FROM " + t + " WHERE version IS NOT NULL;";
        Result r = psql(Arrays.asList("-t", "-A", "-c", sql), 120);
        if (r.exit != 0) throw new IOException("Cannot query database " + cfg.get("db.name") + ": " + r.brief());
        Map<String, String> out = new LinkedHashMap<String, String>();
        for (String line : r.output.split("\\r?\\n")) {
            int bar = line.indexOf('|');
            if (bar > 0) out.put(line.substring(0, bar).trim(), line.substring(bar + 1).trim());
        }
        return out;
    }

    static String maxVersion(Map<String, String> applied) {
        String best = null;
        for (String v : applied.keySet()) {
            if (best == null || Version.of(v).isNewerThan(Version.of(best))) best = v;
        }
        return best;
    }

    private static String findVersion(Map<String, String> applied, Version v) {
        for (Map.Entry<String, String> e : applied.entrySet()) {
            if (Version.of(e.getKey()).equals(v)) return e.getValue();
        }
        return null;
    }

    private static List<String> versions(List<ReleaseManifest.ModuleRelease> rs) {
        List<String> out = new ArrayList<String>();
        for (ReleaseManifest.ModuleRelease r : rs) out.add(r.version.toString());
        return out;
    }

    public void download(List<ReleaseManifest.ModuleRelease> scripts, ReleaseManifest m, State state) {
        for (ReleaseManifest.ModuleRelease r : scripts) {
            try {
                downloader.ensure(r, m);
            } catch (IOException e) {
                state.lastError = e.getMessage();
                state.event("download-failed", r.name, r.version.toString(), e.getMessage());
                LOG.warning("Could not obtain DB script " + r.name + ": " + e.getMessage());
            }
        }
    }

    // ------------------------------------------------------------------ run

    /** @return "updated", "failed" or "nothing" */
    public String run(List<ReleaseManifest.ModuleRelease> scripts, State state) {
        if (scripts.isEmpty()) return "nothing";
        List<ReleaseManifest.ModuleRelease> ready = new ArrayList<ReleaseManifest.ModuleRelease>();
        for (ReleaseManifest.ModuleRelease r : scripts) {
            if (!Files.isRegularFile(downloader.cached(r))) {
                LOG.warning("DB script version " + r.version + " not downloaded yet; it and later versions wait");
                break;                               // strictly in version order
            }
            ready.add(r);
        }
        if (ready.isEmpty()) return "nothing";

        if (cfg.getBool("db.backup-before-scripts", false)) {
            try {
                backup();
            } catch (IOException e) {
                fail(state, ready.get(0), "database backup before running scripts failed: " + e.getMessage());
                return "failed";
            }
        }

        boolean anyFailed = false, anyOk = false;
        for (ReleaseManifest.ModuleRelease r : ready) {
            try {
                // Re-check right before running, in case another run applied it meanwhile.
                if (findVersion(applied(), r.version) != null) continue;
                runOne(r);
                state.failedAttempts.remove(failKey(r));
                state.event("db-script-applied", r.name, r.version.toString(),
                        (r.transactional ? "in one transaction" : "without transaction")
                                + (r.notes != null ? " - " + r.notes : ""));
                LOG.info("Applied DB script version " + r.version + " (" + r.name + ")");
                anyOk = true;
            } catch (IOException e) {
                anyFailed = true;
                fail(state, r, e.getMessage());
                // Later scripts may depend on this one: stop here.
                LOG.severe("Stopping DB updates at version " + r.version + "; later versions wait for it");
                break;
            }
        }
        return anyFailed ? "failed" : anyOk ? "updated" : "nothing";
    }

    private void runOne(ReleaseManifest.ModuleRelease r) throws IOException {
        Path script = downloader.cached(r).toAbsolutePath();
        String record = "INSERT INTO " + table() + " (version, sha256, name, agent_version, notes) VALUES ("
                + lit(r.version.toString()) + ", " + lit(r.sha256) + ", " + lit(r.name) + ", " + lit(Main.VERSION) + ", "
                + (r.notes == null ? "NULL" : lit(r.notes)) + ");";
        int timeout = cfg.getInt("db.timeout-minutes", 30) * 60;

        if (r.transactional) {
            // One transaction: script + tracking row commit together or not at all.
            Path wrapper = cfg.stateDir().resolve("db-run.sql");
            Files.createDirectories(wrapper.getParent());
            String w = "\\set ON_ERROR_STOP on\n"
                    + "BEGIN;\n"
                    + "\\i " + lit(script.toString().replace('\\', '/')) + "\n"
                    + record + "\n"
                    + "COMMIT;\n";
            Files.write(wrapper, w.getBytes(StandardCharsets.UTF_8));
            try {
                Result res = psql(Arrays.asList("-f", wrapper.toString()), timeout);
                if (res.exit != 0) throw new IOException("psql failed (exit " + res.exit + "), rolled back: " + res.brief());
                warnIfScriptManagesTransactions(script, r);
            } finally {
                Files.deleteIfExists(wrapper);
            }
        } else {
            Result res = psql(Arrays.asList("-f", script.toString()), timeout);
            if (res.exit != 0) {
                throw new IOException("psql failed (exit " + res.exit + "); script ran WITHOUT a transaction, "
                        + "so earlier statements in it may have been applied: " + res.brief());
            }
            Result rec = psql(Arrays.asList("-c", record), 120);
            if (rec.exit != 0) throw new IOException("Script ran but could not be recorded: " + rec.brief());
        }
    }

    private static void warnIfScriptManagesTransactions(Path script, ReleaseManifest.ModuleRelease r) {
        try {
            String sql = new String(Files.readAllBytes(script), StandardCharsets.UTF_8).toUpperCase();
            if (sql.matches("(?s).*\\b(COMMIT|ROLLBACK)\\s*;.*")) {
                LOG.warning("DB script " + r.name + " contains its own COMMIT/ROLLBACK; for all-or-nothing behaviour "
                        + "remove them, or set \"transaction\": false in the manifest");
            }
        } catch (IOException ignored) { }
    }

    /** pg_dump -Fc of the whole database into db.backup-dir before scripts run. */
    private void backup() throws IOException {
        File psql = new File(psqlPath());
        String exe = psql.getName().toLowerCase().endsWith(".exe") ? "pg_dump.exe" : "pg_dump";
        File pgDump = psql.getParentFile() == null ? new File(exe) : new File(psql.getParentFile(), exe);
        Path dir = cfg.path("db.backup-dir", "db-backups");
        Files.createDirectories(dir);
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss").format(new Date());
        Path out = dir.resolve(cfg.get("db.name") + "-" + stamp + ".dump");
        List<String> cmd = new ArrayList<String>(Arrays.asList(pgDump.getPath(), "-Fc", "-f", out.toString()));
        cmd.addAll(connectionArgs());
        LOG.info("Backing up database to " + out + " before running scripts");
        Result r = exec(cmd, cfg.getInt("db.backup-timeout-minutes", 60) * 60);
        if (r.exit != 0) {
            Files.deleteIfExists(out);
            throw new IOException("pg_dump failed: " + r.brief());
        }
        LOG.info("Database backup written: " + out + " (" + Files.size(out) / 1024 + " KB)");
    }

    // ------------------------------------------------------------------ process plumbing

    static final class Result {
        final int exit;
        final String output;
        Result(int exit, String output) { this.exit = exit; this.output = output; }
        String brief() {
            String t = output.trim().replaceAll("\\s+", " ");
            return t.length() > 500 ? t.substring(0, 500) + "..." : t;
        }
    }

    private String psqlPath() { return cfg.get("db.psql-path", "psql"); }

    private List<String> connectionArgs() {
        return Arrays.asList(
                "-h", cfg.get("db.host", "localhost"),
                "-p", cfg.get("db.port", "5432"),
                "-U", cfg.require("db.user"),
                "-d", cfg.require("db.name"),
                "-w");                                 // never prompt for a password
    }

    private Result psql(List<String> args, int timeoutSeconds) throws IOException {
        List<String> cmd = new ArrayList<String>();
        cmd.add(psqlPath());
        cmd.add("-X");                                 // ignore any psqlrc
        cmd.add("-q");
        cmd.add("-v");
        cmd.add("ON_ERROR_STOP=1");
        cmd.addAll(connectionArgs());
        cmd.addAll(args);
        return exec(cmd, timeoutSeconds);
    }

    private Result exec(List<String> cmd, int timeoutSeconds) throws IOException {
        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.redirectErrorStream(true);
        Map<String, String> env = pb.environment();
        String pw = cfg.get("db.password");
        if (pw != null) env.put("PGPASSWORD", pw);    // via environment, never on the command line
        env.put("PGCLIENTENCODING", "UTF8");
        env.put("PGAPPNAME", "module-updater");
        final Process p;
        try {
            p = pb.start();
        } catch (IOException e) {
            throw new IOException("Cannot start " + cmd.get(0) + " - check db.psql-path (" + e.getMessage() + ")");
        }
        final ByteArrayOutputStream out = new ByteArrayOutputStream();
        Thread drain = new Thread(new Runnable() {
            @Override public void run() {
                try { out.write(Http.readAll(p.getInputStream())); } catch (IOException ignored) { }
            }
        }, "psql-output");
        drain.setDaemon(true);
        drain.start();
        try {
            if (!p.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                throw new IOException(cmd.get(0) + " timed out after " + timeoutSeconds + "s");
            }
            drain.join(5000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Interrupted");
        }
        return new Result(p.exitValue(), new String(out.toByteArray(), StandardCharsets.UTF_8));
    }

    // ------------------------------------------------------------------ helpers

    private static String lit(String s) { return "'" + s.replace("'", "''") + "'"; }

    static String short12(ReleaseManifest.ModuleRelease r) { return "sha256 " + r.sha256.substring(0, 12); }

    /** Per version AND content: a corrected script under the same version gets fresh attempts. */
    static String failKey(ReleaseManifest.ModuleRelease r) { return "db@" + r.version + "#" + r.sha256.substring(0, 12); }

    private void fail(State state, ReleaseManifest.ModuleRelease r, String why) {
        String k = failKey(r);
        Long n = state.failedAttempts.get(k);
        state.failedAttempts.put(k, n == null ? 1L : n + 1);
        state.dbError = "DB script version " + r.version + ": " + why;
        state.event("db-script-failed", r.name, r.version.toString(), why);
        LOG.severe("DB script version " + r.version + " failed: " + why);
    }
}
