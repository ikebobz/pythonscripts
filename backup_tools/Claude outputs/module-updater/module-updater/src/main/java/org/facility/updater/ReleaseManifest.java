package org.facility.updater;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/**
 * The signed list of current module releases published by the release team.
 *
 * <pre>
 * {
 *   "sequence": 42,                         // must increase with every publish (anti-rollback)
 *   "channel": "stable",
 *   "published_at": "2026-10-10T09:00:00Z",
 *   "modules": [
 *     { "name": "hiv", "version": "1.4.2", "url": "hiv-1.4.2.jar",
 *       "sha256": "...", "size": 1234567, "order": 20,
 *       "urgent": false, "force_version": false, "min_core_version": "3.1.0",
 *       "notes": "Fixes viral load report" }
 *   ]
 * }
 * </pre>
 */
public final class ReleaseManifest {

    public static final class ModuleRelease {
        public String name;
        public Version version;
        public String url;          // absolute, or relative to the manifest location / USB bundle folder
        public String sha256;
        public long size;
        public int order;           // install order (lower first, e.g. dependencies before dependants)
        public boolean urgent;      // install immediately, ignoring the maintenance window
        public boolean forceVersion;// install this exact version even if the facility has a newer one
        public Version minCoreVersion;
        public String notes;
        /** Overrides the cache file name (core JAR, loose files). */
        public String cacheName;
        /** A loose file (e.g. query.yml) rather than a versioned module/core. */
        public boolean isFile;
        /** DB scripts: run inside one transaction (default) - set "transaction": false for e.g. CREATE INDEX CONCURRENTLY. */
        public boolean transactional = true;

        public String key() { return name.toLowerCase(); }

        public String fileName() { return cacheName != null ? cacheName : name + "-" + version + ".jar"; }

        @Override
        public String toString() { return name + " " + version; }
    }

    public long sequence;
    public String channel;
    public String publishedAt;
    public final List<ModuleRelease> modules = new ArrayList<ModuleRelease>();
    /** Optional core application release ("core": {...}). */
    public ModuleRelease core;
    /** Optional loose files copied into the core folder ("files": [...]), e.g. query.yml. */
    public final List<ModuleRelease> files = new ArrayList<ModuleRelease>();
    /**
     * Optional versioned SQL scripts for the local Postgres database ("db_updates": {...} or [...]), sorted by
     * version. Each runs once; the database records the highest version applied.
     */
    public final List<ModuleRelease> dbUpdates = new ArrayList<ModuleRelease>();

    /** Where the manifest came from: an http(s) URL or a local folder (USB bundle). */
    public String source;
    public boolean offline;

    @SuppressWarnings("unchecked")
    public static ReleaseManifest parse(String json) {
        Map<String, Object> root = Json.parseObject(json);
        ReleaseManifest m = new ReleaseManifest();
        m.sequence = Json.lng(root.get("sequence"), -1);
        if (m.sequence < 0) throw new IllegalArgumentException("Manifest has no valid 'sequence'");
        m.channel = Json.str(root.get("channel"));
        m.publishedAt = Json.str(root.get("published_at"));
        Object mods = root.get("modules");
        if (!(mods instanceof List)) throw new IllegalArgumentException("Manifest has no 'modules' list");
        for (Object o : (List<Object>) mods) {
            Map<String, Object> mm = (Map<String, Object>) o;
            ModuleRelease r = new ModuleRelease();
            r.name = req(mm, "name");
            r.version = Version.of(req(mm, "version"));
            r.url = req(mm, "url");
            r.sha256 = req(mm, "sha256").toLowerCase();
            r.size = Json.lng(mm.get("size"), -1);
            r.order = (int) Json.lng(mm.get("order"), 100);
            r.urgent = Json.bool(mm.get("urgent"));
            r.forceVersion = Json.bool(mm.get("force_version"));
            String core = Json.str(mm.get("min_core_version"));
            r.minCoreVersion = core == null || core.isEmpty() ? null : Version.of(core);
            r.notes = Json.str(mm.get("notes"));
            if (!r.sha256.matches("[0-9a-f]{64}")) {
                throw new IllegalArgumentException("Module " + r.name + " has an invalid sha256");
            }
            m.modules.add(r);
        }
        if (root.get("core") instanceof Map) {
            Map<String, Object> c = (Map<String, Object>) root.get("core");
            ModuleRelease r = new ModuleRelease();
            r.name = CoreUpdater.CORE_KEY;
            r.version = Version.of(req(c, "version"));
            r.url = req(c, "url");
            r.sha256 = req(c, "sha256").toLowerCase();
            r.size = Json.lng(c.get("size"), -1);
            r.urgent = Json.bool(c.get("urgent"));
            r.forceVersion = Json.bool(c.get("force_version"));
            r.notes = Json.str(c.get("notes"));
            checkSha(r);
            m.core = r;
        }
        if (root.get("files") instanceof List) {
            for (Object o : (List<Object>) root.get("files")) {
                Map<String, Object> f = (Map<String, Object>) o;
                ModuleRelease r = new ModuleRelease();
                r.isFile = true;
                r.name = req(f, "name");
                if (!r.name.matches("[A-Za-z0-9._-]+") || r.name.startsWith(".")) {
                    throw new IllegalArgumentException("File name '" + r.name + "' must be a plain file name");
                }
                r.version = Version.of("0");
                r.url = req(f, "url");
                r.sha256 = req(f, "sha256").toLowerCase();
                r.size = Json.lng(f.get("size"), -1);
                r.urgent = Json.bool(f.get("urgent"));
                r.notes = Json.str(f.get("notes"));
                checkSha(r);
                r.cacheName = "file-" + r.sha256.substring(0, 12) + "-" + r.name;
                m.files.add(r);
            }
        }
        Object db = root.get("db_updates");
        List<Object> dbList = new ArrayList<Object>();
        if (db instanceof Map) dbList.add(db);
        else if (db instanceof List) dbList.addAll((List<Object>) db);
        for (Object o : dbList) {
            Map<String, Object> f = (Map<String, Object>) o;
            ModuleRelease r = new ModuleRelease();
            r.isFile = true;
            r.name = f.get("name") == null ? "db_updates" : req(f, "name");
            if (!r.name.matches("[A-Za-z0-9._-]+") || r.name.startsWith(".")) {
                throw new IllegalArgumentException("db_updates name '" + r.name + "' must be a plain file name");
            }
            r.version = Version.of(req(f, "version"));
            r.url = req(f, "url");
            r.sha256 = req(f, "sha256").toLowerCase();
            r.size = Json.lng(f.get("size"), -1);
            r.urgent = Json.bool(f.get("urgent"));
            r.notes = Json.str(f.get("notes"));
            r.transactional = f.get("transaction") == null || Json.bool(f.get("transaction"));
            checkSha(r);
            r.cacheName = "db-" + r.version + "-" + r.sha256.substring(0, 12) + ".sql";
            for (ModuleRelease other : m.dbUpdates) {
                if (other.version.equals(r.version)) {
                    throw new IllegalArgumentException("db_updates version " + r.version + " is listed twice");
                }
            }
            m.dbUpdates.add(r);
        }
        Collections.sort(m.dbUpdates, new Comparator<ModuleRelease>() {
            @Override
            public int compare(ModuleRelease a, ModuleRelease b) { return a.version.compareTo(b.version); }
        });
        Collections.sort(m.modules, new Comparator<ModuleRelease>() {
            @Override
            public int compare(ModuleRelease a, ModuleRelease b) { return Integer.compare(a.order, b.order); }
        });
        return m;
    }

    private static void checkSha(ModuleRelease r) {
        if (!r.sha256.matches("[0-9a-f]{64}")) throw new IllegalArgumentException(r.name + " has an invalid sha256");
    }

    private static String req(Map<String, Object> m, String k) {
        Object v = m.get(k);
        if (v == null || v.toString().trim().isEmpty()) {
            Object id = m.get("name") != null ? m.get("name")
                    : m.get("version") != null ? "version " + m.get("version") : m.get("url");
            throw new IllegalArgumentException("Manifest entry " + (id == null ? "" : "'" + id + "' ") + "is missing '" + k
                    + "'" + ("sha256".equals(k) || "size".equals(k)
                        ? " - sign the manifest with publish.bat using the CURRENT module-updater.jar so checksums are filled in"
                        : ""));
        }
        return v.toString().trim();
    }
}
