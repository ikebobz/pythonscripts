package org.facility.updater;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;

/**
 * Versioned db_updates tests against a REAL Postgres server.
 * Needs env: PGTEST_PSQL, PGTEST_HOST, PGTEST_PORT, PGTEST_DB, PGTEST_USER, PGTEST_PASSWORD.
 * Run: java -cp target/classes:target/test-classes org.facility.updater.DbTest
 */
public final class DbTest {

    static int passed = 0, failed = 0;
    static Path releaseDir;
    static Path priv;
    static long seq = 5000;
    static volatile boolean appUp = true;
    static volatile String moduleList = "[{\"id\":1,\"name\":\"HivModule\",\"version\":\"2.7.2\"}]";

    public static void main(String[] args) {
        if (System.getenv("PGTEST_PORT") == null) {
            System.out.println("DbTest skipped (PGTEST_* not set)");
            return;
        }
        try {
            run();
        } catch (Throwable t) {
            t.printStackTrace();
            failed++;
        }
        System.out.println();
        System.out.println("DbTest: " + passed + " passed, " + failed + " failed");
        System.exit(failed == 0 ? 0 : 1);
    }

    static void run() throws Exception {
        Path tmp = Files.createTempDirectory("updater-dbtest");
        releaseDir = Files.createDirectories(tmp.resolve("release"));
        Path agentDir = Files.createDirectories(tmp.resolve("agent"));
        priv = tmp.resolve("private.pem");
        Signatures.generateKeyPair(priv, agentDir.resolve("release-public.pem"));

        HttpServer srv = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        srv.createContext("/release/", DbTest::release);
        srv.createContext("/actuator/health", ex -> {
            if (!appUp) { ex.close(); return; }
            respond(ex, 200, "{\"status\":\"UP\"}");
        });
        srv.createContext("/api/v1/authenticate", ex -> respond(ex, 200, "{\"id_token\":\"t\"}"));
        srv.createContext("/api/v1/modules/upload", ex -> respond(ex, 500, "{\"error\":\"broken\"}"));
        srv.createContext("/api/v1/modules", ex -> respond(ex, 200, moduleList));
        srv.start();
        String base = "http://127.0.0.1:" + srv.getAddress().getPort();

        Properties p = new Properties();
        p.setProperty("manifest.url", base + "/release/manifest.json");
        p.setProperty("app.base-url", base);
        p.setProperty("app.auth.username", "u");
        p.setProperty("app.auth.password", "p");
        p.setProperty("app.api.install", "");
        p.setProperty("app.restart.command", "true");
        p.setProperty("app.restart.settle-seconds", "0");
        p.setProperty("app.restart.timeout-seconds", "3");
        p.setProperty("offline.scan-drive-roots", "false");
        p.setProperty("download.retry-initial-seconds", "0");
        p.setProperty("db.psql-path", System.getenv("PGTEST_PSQL"));
        p.setProperty("db.host", System.getenv("PGTEST_HOST"));
        p.setProperty("db.port", System.getenv("PGTEST_PORT"));
        p.setProperty("db.name", System.getenv("PGTEST_DB"));
        p.setProperty("db.user", System.getenv("PGTEST_USER"));
        p.setProperty("db.password", System.getenv("PGTEST_PASSWORD"));

        sql(p, "DROP TABLE IF EXISTS public.module_updater_db_scripts, public.dbt_patients, public.dbt_other, public.dbt_v3;");
        sql(p, "CREATE TABLE public.dbt_patients(id int primary key, name text);");

        Path v1 = script("db_updates-1.sql",
                "ALTER TABLE public.dbt_patients ADD COLUMN facility_code text;\n"
                + "INSERT INTO public.dbt_patients(id, name, facility_code) VALUES (1, 'O''Brien ✓', 'FAC-001');\n");
        Map<String, Object> e1 = db("1", v1);

        // ------------------------------------------------------------- 1. daytime: downloaded, waits
        publish(e1);
        UpdateAgent agent = new UpdateAgent(new Config(p, agentDir));
        agent.setClock(LocalTime.of(10, 0));
        State s = agent.runOnce(false);
        check("daytime: v1 waits for window (" + s.dbResult + ")", "downloaded-waiting-for-window".equals(s.dbResult)
                && "1".equals(s.dbPending) && s.dbVersion == null
                && "1".equals(s.pending.get("DB scripts")));

        // ------------------------------------------------------------- 2. night: v1 runs, version recorded
        agent.setClock(LocalTime.of(22, 0));
        s = agent.runOnce(false);
        check("night: v1 applied, database at version 1", "updated".equals(s.dbResult) && "1".equals(s.dbVersion)
                && s.dbPending == null && "1".equals(s.installed.get("DB scripts")) && "updated".equals(s.lastResult));
        check("data changed incl. quotes + UTF-8", "O'Brien ✓|FAC-001".equals(
                sql(p, "SELECT name || '|' || facility_code FROM public.dbt_patients WHERE id = 1;")));
        check("version stored in the database", "1".equals(sql(p, "SELECT version FROM public.module_updater_db_scripts;")));
        s = agent.runOnce(false);
        check("second run: nothing to do", "up-to-date".equals(s.dbResult) && "up-to-date".equals(s.lastResult));

        // ------------------------------------------------------------- 3. agent state wiped: still not re-run
        Files.deleteIfExists(agentDir.resolve("state/state.json"));
        Files.deleteIfExists(agentDir.resolve("state/manifest.json"));
        s = new UpdateAgent(new Config(p, agentDir)).runOnce(false);
        check("state.json deleted: version read from DB, no re-run", "up-to-date".equals(s.dbResult)
                && "1".equals(s.dbVersion) && "1".equals(sql(p, "SELECT count(*) FROM public.dbt_patients;")));

        // ------------------------------------------------------------- 4. v2 fails -> rolled back, v3 waits
        Path v2bad = script("db_updates-2.sql",
                "INSERT INTO public.dbt_patients(id, name) VALUES (2, 'should vanish');\n"
                + "UPDATE public.no_such_table SET x = 1;\n");
        Path v3 = script("db_updates-3.sql", "CREATE TABLE public.dbt_v3(id int);\n");
        publish(e1, db("2", v2bad), db("3", v3));
        s = agent.runOnce(false);
        check("v2 fails, reported (" + s.lastResult + ")", "failed".equals(s.dbResult) && "db-failed".equals(s.lastResult)
                && s.lastError != null && s.lastError.contains("no_such_table"));
        check("v2 rolled back completely", "0".equals(sql(p, "SELECT count(*) FROM public.dbt_patients WHERE id = 2;")));
        check("v3 NOT run after v2 failed (order kept)", "1".equals(s.dbVersion)
                && "0".equals(sql(p, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dbt_v3';")));
        agent.runOnce(false);
        agent.runOnce(false);
        s = agent.runOnce(false);
        check("after 3 failures v2 (and v3) wait for a fix", s.failedAttempts.containsValue(3L) && "up-to-date".equals(s.dbResult));

        // ------------------------------------------------------------- 5. corrected v2 under same version -> v2 + v3 run
        Path v2 = script("db_updates-2.sql", "UPDATE public.dbt_patients SET name = 'fixed' WHERE id = 1;\n");
        publish(e1, db("2", v2), db("3", v3));
        s = agent.runOnce(false);
        check("corrected v2 then v3 run in order -> version 3", "3".equals(s.dbVersion) && "updated".equals(s.dbResult)
                && "fixed".equals(sql(p, "SELECT name FROM public.dbt_patients WHERE id = 1;")));

        // ------------------------------------------------------------- 6. independence: app down, module install broken
        appUp = false;
        Path v4 = script("db_updates-4.sql", "CREATE TABLE public.dbt_other(id int);\n");
        publish(e1, db("2", v2), db("3", v3), db("4", v4));
        s = agent.runOnce(false);
        check("app DOWN: DB v4 still applied", "4".equals(s.dbVersion) && "updated".equals(s.dbResult));
        appUp = true;
        moduleList = "[{\"id\":1,\"name\":\"hiv\",\"version\":\"1.0.0\"}]";
        Path jar = releaseDir.resolve("hiv-2.0.0.jar");
        Files.write(jar, new byte[]{1, 2, 3});
        Path v5 = script("db_updates-5.sql", "INSERT INTO public.dbt_other VALUES (5);\n");
        publishWithModule(jar, e1, db("2", v2), db("3", v3), db("4", v4), db("5", v5));
        s = agent.runOnce(false);
        check("module install FAILS (" + s.lastResult + "), DB v5 still applied", "5".equals(s.dbVersion)
                && s.lastResult.contains("fail") && "1".equals(sql(p, "SELECT count(*) FROM public.dbt_other;")));
        moduleList = "[{\"id\":1,\"name\":\"HivModule\",\"version\":\"2.7.2\"}]";

        // ------------------------------------------------------------- 7. older / changed versions are not run
        Path old = script("db_updates-0.sql", "DROP TABLE public.dbt_patients;\n");
        Path v1changed = script("db_updates-1b.sql", "DROP TABLE public.dbt_patients;\n");
        publish(db("0.5", old), db("1", v1changed), db("2", v2), db("3", v3), db("4", v4), db("5", v5));
        s = agent.runOnce(false);
        check("version lower than DB version, and edited v1: neither run", "up-to-date".equals(s.dbResult)
                && "1".equals(sql(p, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dbt_patients';")));

        // ------------------------------------------------------------- 8. urgent in daytime + backup; db.window
        Path v6 = script("db_updates-6.sql", "INSERT INTO public.dbt_other VALUES (6);\n");
        Map<String, Object> e6 = db("6", v6);
        e6.put("urgent", true);
        publish(db("4", v4), db("5", v5), e6);
        Properties pb = new Properties();
        pb.putAll(p);
        pb.setProperty("db.backup-before-scripts", "true");
        pb.setProperty("db.backup-dir", tmp.resolve("dumps").toString());
        UpdateAgent withBackup = new UpdateAgent(new Config(pb, agentDir));
        withBackup.setClock(LocalTime.of(11, 0));
        s = withBackup.runOnce(false);
        String[] dumps = tmp.resolve("dumps").toFile().list();
        check("urgent v6 runs in daytime", "6".equals(s.dbVersion));
        check("pg_dump backup taken first", dumps != null && dumps.length == 1 && dumps[0].endsWith(".dump"));
        Path v7 = script("db_updates-7.sql", "INSERT INTO public.dbt_other VALUES (7);\n");
        publish(db("6", v6), db("7", v7));
        Properties pw7 = new Properties();
        pw7.putAll(p);
        pw7.setProperty("db.window", "always");          // DB scripts as soon as published; modules keep their window
        UpdateAgent always = new UpdateAgent(new Config(pw7, agentDir));
        always.setClock(LocalTime.of(11, 0));
        s = always.runOnce(false);
        check("db.window=always: v7 runs at 11:00", "7".equals(s.dbVersion));

        // ------------------------------------------------------------- 9. transaction:false
        Path v8 = script("db_updates-8.sql",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS dbt_patients_fac ON public.dbt_patients(facility_code);\n");
        Map<String, Object> e8 = db("8", v8);
        e8.put("transaction", false);
        publish(db("7", v7), e8);
        s = agent.runOnce(false);
        check("CREATE INDEX CONCURRENTLY with transaction:false", "8".equals(s.dbVersion)
                && "1".equals(sql(p, "SELECT count(*) FROM pg_indexes WHERE indexname = 'dbt_patients_fac';")));

        // ------------------------------------------------------------- 10. wrong password: modules unaffected
        Properties pw = new Properties();
        pw.putAll(p);
        pw.setProperty("db.password", "wrong");
        s = new UpdateAgent(new Config(pw, agentDir)).runOnce(true);
        check("wrong DB password: DB error reported, run continues", "error".equals(s.dbResult)
                && s.dbError.contains("authentication failed") && !"error".equals(s.lastResult));

        srv.stop(0);
    }

    // ===================================================================== helpers

    static String sql(Properties p, String q) throws Exception {
        ProcessBuilder pb = new ProcessBuilder(p.getProperty("db.psql-path"), "-X", "-q", "-t", "-A",
                "-h", p.getProperty("db.host"), "-p", p.getProperty("db.port"), "-U", p.getProperty("db.user"),
                "-d", p.getProperty("db.name"), "-c", q);
        pb.environment().put("PGPASSWORD", p.getProperty("db.password"));
        pb.environment().put("PGCLIENTENCODING", "UTF8");
        pb.redirectErrorStream(true);
        Process pr = pb.start();
        String out = new String(Http.readAll(pr.getInputStream()), StandardCharsets.UTF_8).trim();
        pr.waitFor();
        return out;
    }

    static Path script(String name, String body) throws IOException {
        Path f = releaseDir.resolve(name);
        Files.write(f, body.getBytes(StandardCharsets.UTF_8));
        return f;
    }

    /** Entry pointing at a content-addressed copy, so rewriting a script file never changes earlier entries. */
    static Map<String, Object> db(String version, Path file) throws IOException {
        String sha = Signatures.sha256(file);
        String unique = sha.substring(0, 8) + "-" + file.getFileName();
        Files.copy(file, releaseDir.resolve(unique), StandardCopyOption.REPLACE_EXISTING);
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("version", version);
        m.put("url", unique);
        m.put("sha256", sha);
        m.put("size", Files.size(file));
        return m;
    }

    @SafeVarargs
    static void publish(Map<String, Object>... db) throws Exception {
        write(new ArrayList<Object>(), db);
    }

    @SafeVarargs
    static void publishWithModule(Path jar, Map<String, Object>... db) throws Exception {
        Map<String, Object> mod = new LinkedHashMap<String, Object>();
        mod.put("name", "hiv");
        mod.put("version", "2.0.0");
        mod.put("url", jar.getFileName().toString());
        mod.put("sha256", Signatures.sha256(jar));
        mod.put("size", Files.size(jar));
        mod.put("urgent", true);
        List<Object> mods = new ArrayList<Object>();
        mods.add(mod);
        write(mods, db);
    }

    @SafeVarargs
    static void write(List<Object> modules, Map<String, Object>... db) throws Exception {
        List<Object> l = new ArrayList<Object>();
        for (Map<String, Object> e : db) l.add(e);
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("sequence", ++seq);
        m.put("modules", modules);
        m.put("db_updates", l);
        byte[] b = Json.write(m).getBytes(StandardCharsets.UTF_8);
        Files.write(releaseDir.resolve("manifest.json"), b);
        Files.write(releaseDir.resolve("manifest.json.sig"), Signatures.sign(priv, b));
    }

    static void release(HttpExchange ex) throws IOException {
        Path f = releaseDir.resolve(ex.getRequestURI().getPath().substring("/release/".length()));
        if (!Files.isRegularFile(f)) { respond(ex, 404, "nf"); return; }
        byte[] b = Files.readAllBytes(f);
        ex.sendResponseHeaders(200, b.length);
        try (OutputStream o = ex.getResponseBody()) { o.write(b); }
    }

    static void respond(HttpExchange ex, int status, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, b.length);
        try (OutputStream o = ex.getResponseBody()) { o.write(b); }
    }

    static void check(String name, boolean ok) {
        System.out.println((ok ? "PASS  " : "FAIL  ") + name);
        if (ok) passed++; else failed++;
    }
}
