package org.facility.updater;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * End-to-end tests with an in-process fake release server and fake Spring Boot module API.
 * Run: java -cp target/classes:target/test-classes org.facility.updater.SelfTest
 */
public final class SelfTest {

    static int passed = 0, failed = 0;

    // ---- fake application state
    static final Map<String, String> appModules = new LinkedHashMap<String, String>();
    static final Set<String> brokenReleases = new LinkedHashSet<String>();
    static final List<String> uploads = new ArrayList<String>();
    static volatile boolean appUp = true;
    static volatile int healthStatusWhenUp = 200;
    static volatile String rawModuleList = null;
    static volatile boolean requireInstallCall = false;
    static volatile boolean holdUntilRestart = false;
    static volatile Path downMarker = null;
    static volatile String heldVersion = null;
    static final Map<String, String> uploadedPending = new LinkedHashMap<String, String>();

    // ---- fake release server state
    static Path releaseDir;
    static volatile boolean releaseServerUp = true;
    static volatile int cutNextDownloadAt = -1;
    static final List<String> rangeRequests = new ArrayList<String>();

    public static void main(String[] args) {
        try {
            run();
        } catch (Throwable t) {
            t.printStackTrace();
            failed++;
        }
        System.out.println();
        System.out.println(passed + " passed, " + failed + " failed");
        System.exit(failed == 0 ? 0 : 1);
    }

    static void run() throws Exception {
        Path tmp = Files.createTempDirectory("updater-test");
        releaseDir = Files.createDirectories(tmp.resolve("release"));
        Path agentDir = Files.createDirectories(tmp.resolve("agent"));
        Path priv = tmp.resolve("private.pem");
        Signatures.generateKeyPair(priv, agentDir.resolve("release-public.pem"));

        HttpServer srv = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        srv.createContext("/release/", SelfTest::release);
        srv.createContext("/actuator/health", ex -> {
            if (!appUp || (downMarker != null && Files.exists(downMarker))) { ex.close(); return; }
            respond(ex, healthStatusWhenUp, "{\"status\":\"UP\"}");
        });
        srv.createContext("/api/v1/authenticate", ex -> respond(ex, 200, "{\"id_token\":\"tok123\"}"));
        srv.createContext("/api/v1/modules/upload", SelfTest::upload);
        srv.createContext("/api/v1/modules/install", SelfTest::installWithBody);
        srv.createContext("/api/v1/modules", SelfTest::list);
        srv.start();
        String base = "http://127.0.0.1:" + srv.getAddress().getPort();

        Properties p = new Properties();
        p.setProperty("manifest.url", base + "/release/manifest.json");
        p.setProperty("app.base-url", base);
        p.setProperty("app.auth.username", "admin");
        p.setProperty("app.auth.password", "secret");
        p.setProperty("app.restart.settle-seconds", "0");
        p.setProperty("app.restart.command", "true");   // no-op restart; the fake app applies versions immediately
        p.setProperty("app.restart.timeout-seconds", "5");
        p.setProperty("download.retries", "3");
        p.setProperty("download.retry-initial-seconds", "0");
        p.setProperty("offline.scan-drive-roots", "false");
        p.setProperty("install.window", "20:00-06:00");
        Config cfg = new Config(p, agentDir);
        UpdateAgent agent = new UpdateAgent(cfg);

        // ------------------------------------------------------------- unit: versions
        check("1.4.10 > 1.4.9", Version.of("1.4.10").isNewerThan(Version.of("1.4.9")));
        check("2.0.0 > 2.0.0-RC1", Version.of("2.0.0").isNewerThan(Version.of("2.0.0-RC1")));
        check("v1.4 == 1.4.0", Version.of("v1.4").equals(Version.of("1.4.0")));
        check("1.0.0-RC2 > 1.0.0-RC1", Version.of("1.0.0-RC2").isNewerThan(Version.of("1.0.0-RC1")));
        check("window spans midnight", cfg.inInstallWindow(LocalTime.of(23, 0)) && cfg.inInstallWindow(LocalTime.of(5, 59))
                && !cfg.inInstallWindow(LocalTime.of(12, 0)));

        // Facility starts on old versions
        appModules.put("hiv", "1.4.0");
        appModules.put("laboratory", "2.1.0");
        appModules.put("prep", "1.0.0");          // not in the release list: must be left alone

        // ------------------------------------------------------------- 1. daytime: download only
        jar("hiv", "1.4.2", 300_000);
        jar("laboratory", "2.2.0", 50_000);
        jar("pharmacy", "1.0.0", 1000);
        publish(priv, mod("hiv", "1.4.2", false), mod("laboratory", "2.2.0", false), mod("pharmacy", "1.0.0", false));
        cutNextDownloadAt = 120_000;               // connection drops mid-way through the first JAR
        agent.setClock(LocalTime.of(10, 0));
        State s = agent.runOnce(false);
        check("daytime: waits for window", "downloaded-waiting-for-window".equals(s.lastResult));
        check("daytime: nothing uploaded", uploads.isEmpty());
        check("resume used HTTP Range after drop", !rangeRequests.isEmpty());
        check("JARs cached and verified", Files.isRegularFile(agentDir.resolve("cache/hiv-1.4.2.jar"))
                && Files.isRegularFile(agentDir.resolve("cache/laboratory-2.2.0.jar")));
        check("module not installed at facility is skipped", !Files.exists(agentDir.resolve("cache/pharmacy-1.0.0.jar")));
        check("pending lists both", s.pending.size() == 2);

        // ------------------------------------------------------------- 2. night: install + verify
        agent.setClock(LocalTime.of(22, 0));
        s = agent.runOnce(false);
        check("night: updated", "updated".equals(s.lastResult));
        check("hiv now 1.4.2", "1.4.2".equals(appModules.get("hiv")));
        check("laboratory now 2.2.0", "2.2.0".equals(appModules.get("laboratory")));
        check("prep untouched", "1.0.0".equals(appModules.get("prep")));
        check("nothing pending", s.pending.isEmpty());
        uploads.clear();
        s = agent.runOnce(false);
        check("second run: up-to-date, no uploads", "up-to-date".equals(s.lastResult) && uploads.isEmpty());

        // ------------------------------------------------------------- 3. tampered manifest rejected
        String good = new String(Files.readAllBytes(releaseDir.resolve("manifest.json")), StandardCharsets.UTF_8);
        jar("hiv", "9.9.9", 1000);
        Files.write(releaseDir.resolve("manifest.json"), good.replace("\"1.4.2\"", "\"9.9.9\"").getBytes(StandardCharsets.UTF_8));
        s = agent.runOnce(false);
        check("tampered manifest ignored", "up-to-date".equals(s.lastResult) && uploads.isEmpty());

        // ------------------------------------------------------------- 4. broken release -> rollback
        jar("hiv", "1.5.0", 2000);
        publish(priv, mod("hiv", "1.5.0", false), mod("laboratory", "2.2.0", false));
        brokenReleases.add("hiv@1.5.0");
        s = agent.runOnce(false);
        check("broken release reported", "partially-updated".equals(s.lastResult));
        check("rolled back to 1.4.2", "1.4.2".equals(appModules.get("hiv")));
        check("rollback event recorded", hasEvent(s, "rolled-back"));
        agent.runOnce(false);
        agent.runOnce(false);
        uploads.clear();
        s = agent.runOnce(false);
        check("gives up after 3 failed attempts", uploads.isEmpty() && s.failedAttempts.get("hiv@1.5.0") == 3L);

        // ------------------------------------------------------------- 5. urgent fix installs in daytime
        brokenReleases.clear();
        jar("hiv", "1.5.1", 2000);
        publish(priv, mod("hiv", "1.5.1", true), mod("laboratory", "2.2.0", false));
        agent.setClock(LocalTime.of(11, 0));
        s = agent.runOnce(false);
        check("urgent release installed outside window", "1.5.1".equals(appModules.get("hiv")));

        // ------------------------------------------------------------- 6. offline USB bundle
        releaseServerUp = false;
        Path usb = Files.createDirectories(tmp.resolve("usb/module-updates"));
        Path lab = jarAt(usb, "laboratory", "2.3.0", 4000);
        Map<String, Object> m = manifest(mod("hiv", "1.5.1", false), modAt("laboratory", "2.3.0", lab));
        writeSigned(priv, usb, m);
        Properties p2 = new Properties();
        p2.putAll(p);
        p2.setProperty("offline.scan-dirs", tmp.resolve("usb").toString());
        UpdateAgent agent2 = new UpdateAgent(new Config(p2, agentDir));
        agent2.setClock(LocalTime.of(21, 0));
        s = agent2.runOnce(false);
        check("USB bundle installed while server offline", "2.3.0".equals(appModules.get("laboratory")));

        // ------------------------------------------------------------- 7. downgrade attempt via old manifest
        releaseServerUp = true;
        Files.write(releaseDir.resolve("manifest.json"), good.getBytes(StandardCharsets.UTF_8));
        // restore the original (valid but older-sequence) signature
        publishRaw(priv, good);
        uploads.clear();
        s = agent.runOnce(false);
        check("older-sequence manifest rejected (no downgrade)", uploads.isEmpty() && "2.3.0".equals(appModules.get("laboratory")));

        // ------------------------------------------------------------- 8. app down: no install, no crash
        appUp = false;
        jar("hiv", "1.6.0", 2000);
        publish(priv, mod("hiv", "1.6.0", false));
        uploads.clear();
        s = agent.runOnce(false);
        check("app down: downloads but does not install", "app-unavailable".equals(s.lastResult) && uploads.isEmpty()
                && Files.isRegularFile(agentDir.resolve("cache/hiv-1.6.0.jar")));
        appUp = true;

        // ------------------------------------------------------------- 9. real LAMIS+ module list, secured health URL
        rawModuleList = new String(Files.readAllBytes(java.nio.file.Paths.get("src/test/resources/modules_returned.json")),
                StandardCharsets.UTF_8);
        healthStatusWhenUp = 401;                  // /actuator/health secured or not exposed
        jar("hts", "2.6.2", 3000);
        jar("ndr", "2.5.4", 3000);
        Map<String, Object> hts = modAt("HtsModule", "2.6.2", releaseDir.resolve("hts-2.6.2.jar"));
        Map<String, Object> ndr = modAt("NDRModule", "2.5.4", releaseDir.resolve("ndr-2.5.4.jar"));
        publish(priv, hts, ndr);
        Path fresh = Files.createDirectories(tmp.resolve("agent-fresh"));
        Files.copy(agentDir.resolve("release-public.pem"), fresh.resolve("release-public.pem"));
        UpdateAgent agent3 = new UpdateAgent(new Config(p, fresh));
        s = agent3.runOnce(true);
        check("LAMIS+ list parsed (" + s.installed.size() + " modules)", s.installed.size() == 15
                && "2.6.0".equals(s.installed.get("HtsModule")));
        check("HtsModule + NDRModule detected as updates: " + s.pending,
                "2.6.2".equals(s.pending.get("HtsModule")) && "2.5.4".equals(s.pending.get("NDRModule")));

        // ------------------------------------------------------------- 10. app unreachable on first run is not "up to date"
        appUp = false;
        Path fresh2 = Files.createDirectories(tmp.resolve("agent-fresh2"));
        Files.copy(agentDir.resolve("release-public.pem"), fresh2.resolve("release-public.pem"));
        s = new UpdateAgent(new Config(p, fresh2)).runOnce(true);
        check("unreachable app reported as app-unavailable, not up-to-date", "app-unavailable".equals(s.lastResult)
                && s.lastError != null);
        appUp = true;
        rawModuleList = null;
        healthStatusWhenUp = 200;

        // ------------------------------------------------------------- 11. upload, then POST returned module to /install
        requireInstallCall = true;
        appModules.put("hiv", "1.6.0");
        jar("hiv", "1.7.0", 2000);
        publish(priv, mod("hiv", "1.7.0", true));
        Properties p3 = new Properties();
        p3.putAll(p);
        p3.setProperty("app.api.install", "/api/v1/modules/install");
        UpdateAgent withEmpty = new UpdateAgent(new Config(p3, agentDir));
        s = withEmpty.runOnce(false);
        check("install with empty body fails like LAMIS+ NPE (HTTP 500)", !"1.7.0".equals(appModules.get("hiv"))
                && s.lastError != null && s.lastError.contains("500"));
        p3.setProperty("app.api.install.body", "upload-response");
        s = new UpdateAgent(new Config(p3, agentDir)).runOnce(false);
        check("install with upload-response body succeeds", "1.7.0".equals(appModules.get("hiv")) && "updated".equals(s.lastResult));
        requireInstallCall = false;

        // (core tests follow scenario 12)
        // ------------------------------------------------------------- 12. no restart command: wait, don't roll back
        requireInstallCall = true;                     // upload + /install stores it, but list shows old until "restart"
        appModules.put("hiv", "1.7.0");
        jar("hiv", "1.8.0", 2000);
        publish(priv, mod("hiv", "1.8.0", true));
        Properties p4 = new Properties();
        p4.putAll(p3);
        p4.remove("app.restart.command");
        // simulate "visible only after restart": install endpoint stores pending without changing the list
        holdUntilRestart = true;
        uploads.clear();
        s = new UpdateAgent(new Config(p4, agentDir)).runOnce(false);
        check("no restart configured -> awaiting-restart, not failure", "updated-restart-required".equals(s.lastResult)
                && "1.8.0".equals(s.awaitingRestart.get("hiv")) && !hasEventSince(s, "rolled-back", 1));
        int before = uploads.size();
        s = new UpdateAgent(new Config(p4, agentDir)).runOnce(false);
        check("awaiting release is not re-uploaded every run", uploads.size() == before);
        appModules.put("hiv", heldVersion);           // admin restarts the app
        holdUntilRestart = false;
        s = new UpdateAgent(new Config(p4, agentDir)).runOnce(false);
        check("after restart the version is recognised", s.awaitingRestart.isEmpty() && "up-to-date".equals(s.lastResult));
        requireInstallCall = false;

        // ------------------------------------------------------------- 13. core JAR + query.yml
        Path coreDir = Files.createDirectories(tmp.resolve("LAMISPlus"));
        Files.write(coreDir.resolve("lamisplus-3.0.0.jar"), "old core".getBytes(StandardCharsets.UTF_8));
        String bat = "@echo off\r\ncd /d %~dp0\r\njava -Xmx2g -jar lamisplus-3.0.0.jar --server.port=9120\r\n";
        Files.write(coreDir.resolve("start.bat"), bat.getBytes(StandardCharsets.ISO_8859_1));
        Files.write(coreDir.resolve("query.yml"), "queries: old\n".getBytes(StandardCharsets.UTF_8));
        Path coreJar = jarAt(releaseDir, "lamisplus", "3.1.0", 5000);
        Path query = releaseDir.resolve("query.yml");
        Files.write(query, "queries: new\n".getBytes(StandardCharsets.UTF_8));
        Map<String, Object> m13 = manifest(mod("hiv", "1.8.0", false));
        Map<String, Object> coreEntry = modAt("core", "3.1.0", coreJar);
        coreEntry.remove("name");
        m13.put("core", coreEntry);
        Map<String, Object> q = modAt("query.yml", "0", query);
        q.remove("version");
        List<Object> files = new ArrayList<Object>();
        files.add(q);
        m13.put("files", files);
        writeSigned(priv, releaseDir, m13);
        Properties p5 = new Properties();
        p5.putAll(p);
        p5.setProperty("core.dir", coreDir.toString());
        p5.setProperty("core.batch-file", "start.bat");
        UpdateAgent coreAgent = new UpdateAgent(new Config(p5, agentDir));
        coreAgent.setClock(LocalTime.of(10, 0));
        s = coreAgent.runOnce(false);
        check("core + query.yml detected, wait for window", "downloaded-waiting-for-window".equals(s.lastResult)
                && "3.1.0".equals(s.pending.get("Core")) && s.pending.containsKey("query.yml")
                && "3.0.0".equals(s.installed.get("Core")));
        coreAgent.setClock(LocalTime.of(22, 0));
        s = coreAgent.runOnce(false);
        String batNow = new String(Files.readAllBytes(coreDir.resolve("start.bat")), StandardCharsets.ISO_8859_1);
        check("core JAR copied, old kept", Files.isRegularFile(coreDir.resolve("lamisplus-3.1.0.jar"))
                && Files.isRegularFile(coreDir.resolve("lamisplus-3.0.0.jar")));
        check("batch file now points at 3.1.0 (CRLF kept)", batNow.equals(bat.replace("3.0.0", "3.1.0")));
        check("batch backup written", Files.isRegularFile(coreDir.resolve("start.bat.bak")));
        check("query.yml overwritten, previous kept as .bak",
                "queries: new\n".equals(new String(Files.readAllBytes(coreDir.resolve("query.yml")), StandardCharsets.UTF_8))
                && Files.isRegularFile(coreDir.resolve("query.yml.bak")));
        check("result updated, Core 3.1.0 reported", "updated".equals(s.lastResult) && "3.1.0".equals(s.installed.get("Core"))
                && s.pending.isEmpty());
        s = coreAgent.runOnce(false);
        check("next run: everything up to date", "up-to-date".equals(s.lastResult));

        // ------------------------------------------------------------- 14. core that doesn't start -> rollback
        Path core32 = jarAt(releaseDir, "lamisplus", "3.2.0", 5000);
        Map<String, Object> m14 = manifest(mod("hiv", "1.8.0", false));
        Map<String, Object> ce14 = modAt("core", "3.2.0", core32);
        ce14.remove("name");
        m14.put("core", ce14);
        writeSigned(priv, releaseDir, m14);
        downMarker = tmp.resolve("app-down.flag");
        Properties p6 = new Properties();
        p6.putAll(p5);
        p6.setProperty("app.restart.command", "touch '" + downMarker + "'");   // "restart" -> app never comes back
        UpdateAgent badCore = new UpdateAgent(new Config(p6, agentDir));
        badCore.setClock(LocalTime.of(22, 0));
        s = badCore.runOnce(false);
        String batAfter = new String(Files.readAllBytes(coreDir.resolve("start.bat")), StandardCharsets.ISO_8859_1);
        check("failed core rolled back: batch points at 3.1.0 again", batAfter.contains("lamisplus-3.1.0.jar")
                && hasEvent(s, "rolled-back") && "core-failed".equals(s.lastResult));
        Files.deleteIfExists(downMarker);
        downMarker = null;

        // ------------------------------------------------------------- 15. batch file using a variable
        Path coreDir2 = Files.createDirectories(tmp.resolve("LAMISPlus2"));
        Files.write(coreDir2.resolve("lamisplus-3.0.0.jar"), "old".getBytes(StandardCharsets.UTF_8));
        String bat2 = "set CORE_VERSION=3.0.0\r\njava -jar lamisplus-%CORE_VERSION%.jar\r\n";
        Files.write(coreDir2.resolve("run.bat"), bat2.getBytes(StandardCharsets.ISO_8859_1));
        writeSigned(priv, releaseDir, m13Again(m13));
        Properties p7 = new Properties();
        p7.putAll(p5);
        p7.setProperty("core.dir", coreDir2.toString());
        p7.setProperty("core.batch-file", "run.bat");
        Path agentDir3 = Files.createDirectories(tmp.resolve("agent-core3"));
        Files.copy(agentDir.resolve("release-public.pem"), agentDir3.resolve("release-public.pem"));
        UpdateAgent varAgent = new UpdateAgent(new Config(p7, agentDir3));
        varAgent.setClock(LocalTime.of(22, 0));
        s = varAgent.runOnce(false);
        String bat2Now = new String(Files.readAllBytes(coreDir2.resolve("run.bat")), StandardCharsets.ISO_8859_1);
        check("variable-style batch: CORE_VERSION=3.1.0", bat2Now.equals(bat2.replace("3.0.0", "3.1.0"))
                && Files.isRegularFile(coreDir2.resolve("lamisplus-3.1.0.jar")) && Files.isRegularFile(coreDir2.resolve("query.yml")));

        srv.stop(0);
    }

    // ===================================================================== fake servers

    static void release(HttpExchange ex) throws IOException {
        if (!releaseServerUp) { ex.close(); return; }
        String name = ex.getRequestURI().getPath().substring("/release/".length());
        Path f = releaseDir.resolve(name);
        if (!Files.isRegularFile(f)) { respond(ex, 404, "not found"); return; }
        byte[] all = Files.readAllBytes(f);
        String range = ex.getRequestHeaders().getFirst("Range");
        int from = 0;
        if (range != null) {
            synchronized (rangeRequests) { rangeRequests.add(name + " " + range); }
            from = Integer.parseInt(range.replace("bytes=", "").replace("-", ""));
            ex.getResponseHeaders().add("Content-Range", "bytes " + from + "-" + (all.length - 1) + "/" + all.length);
        }
        int len = all.length - from;
        ex.sendResponseHeaders(range != null ? 206 : 200, len);
        OutputStream out = ex.getResponseBody();
        if (cutNextDownloadAt > 0 && name.endsWith(".jar") && len > cutNextDownloadAt) {
            out.write(all, from, cutNextDownloadAt);
            out.flush();
            cutNextDownloadAt = -1;
            ex.getHttpContext().getServer();
            ex.close(); // abrupt close -> client sees a short read
            return;
        }
        out.write(all, from, len);
        out.close();
    }

    static void list(HttpExchange ex) throws IOException {
        if (!"Bearer tok123".equals(ex.getRequestHeaders().getFirst("Authorization"))) { respond(ex, 401, "{}"); return; }
        if (rawModuleList != null) { respond(ex, 200, rawModuleList); return; }
        List<Object> l = new ArrayList<Object>();
        int id = 1;
        for (Map.Entry<String, String> e : appModules.entrySet()) {
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("id", id++);
            m.put("name", e.getKey());
            m.put("version", e.getValue());
            l.add(m);
        }
        respond(ex, 200, Json.write(l));
    }

    static void upload(HttpExchange ex) throws IOException {
        if (!"Bearer tok123".equals(ex.getRequestHeaders().getFirst("Authorization"))) { respond(ex, 401, "{}"); return; }
        String body = new String(Http.readAll(ex.getRequestBody()), StandardCharsets.ISO_8859_1);
        Matcher mm = Pattern.compile("filename=\"(.+)-(\\d[^\"]*)\\.jar\"").matcher(body);
        if (!mm.find()) { respond(ex, 400, "{\"error\":\"no file\"}"); return; }
        String name = mm.group(1), ver = mm.group(2);
        uploads.add(name + "@" + ver);
        if (requireInstallCall) {
            // LAMIS+-style: upload only stores the file; the returned module object must be posted to /install
            uploadedPending.put(name, ver);
            respond(ex, 200, "{\"id\": 7, \"name\": \"" + name + "\", \"version\": \"" + ver
                    + "\", \"artifact\": \"" + name.toLowerCase() + "\\\\" + name + "-" + ver + ".jar\"}");
            return;
        }
        if (!brokenReleases.contains(name + "@" + ver)) appModules.put(name, ver);
        respond(ex, 200, "{\"id\": 7, \"name\": \"" + name + "\"}");
    }

    static void installWithBody(HttpExchange ex) throws IOException {
        if (!"Bearer tok123".equals(ex.getRequestHeaders().getFirst("Authorization"))) { respond(ex, 401, "{}"); return; }
        String body = new String(Http.readAll(ex.getRequestBody()), StandardCharsets.UTF_8);
        Object artifact = body.trim().isEmpty() ? null : Json.path(Json.parse(body), "artifact");
        if (artifact == null) { respond(ex, 500, "{\"title\":\"Internal Server Error\",\"detail\":\"NullPointerException\"}"); return; }
        Map<String, Object> m = Json.parseObject(body);
        String name = Json.str(m.get("name"));
        String v = uploadedPending.remove(name);
        if (holdUntilRestart) heldVersion = v; else appModules.put(name, v);
        respond(ex, 200, body);
    }

    static void respond(HttpExchange ex, int status, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, b.length);
        try (OutputStream o = ex.getResponseBody()) { o.write(b); }
    }

    // ===================================================================== release helpers

    static void jar(String name, String ver, int size) throws IOException { jarAt(releaseDir, name, ver, size); }

    static Path jarAt(Path dir, String name, String ver, int size) throws IOException {
        byte[] b = new byte[size];
        new java.util.Random((name + ver).hashCode()).nextBytes(b);
        Path f = dir.resolve(name + "-" + ver + ".jar");
        Files.write(f, b);
        return f;
    }

    static Map<String, Object> mod(String name, String ver, boolean urgent) throws IOException {
        Map<String, Object> m = modAt(name, ver, releaseDir.resolve(name + "-" + ver + ".jar"));
        m.put("urgent", urgent);
        return m;
    }

    static Map<String, Object> modAt(String name, String ver, Path jar) throws IOException {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("name", name);
        m.put("version", ver);
        m.put("url", jar.getFileName().toString());
        m.put("sha256", Signatures.sha256(jar));
        m.put("size", Files.size(jar));
        return m;
    }

    static long seq = 1000;

    @SafeVarargs
    static Map<String, Object> manifest(Map<String, Object>... mods) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("sequence", ++seq);
        m.put("channel", "stable");
        List<Object> l = new ArrayList<Object>();
        for (Map<String, Object> x : mods) l.add(x);
        m.put("modules", l);
        return m;
    }

    @SafeVarargs
    static void publish(Path priv, Map<String, Object>... mods) throws Exception {
        writeSigned(priv, releaseDir, manifest(mods));
    }

    static void writeSigned(Path priv, Path dir, Map<String, Object> m) throws Exception {
        byte[] b = Json.write(m).getBytes(StandardCharsets.UTF_8);
        Files.write(dir.resolve("manifest.json"), b);
        Files.write(dir.resolve("manifest.json.sig"), Signatures.sign(priv, b));
    }

    static void publishRaw(Path priv, String json) throws Exception {
        byte[] b = json.getBytes(StandardCharsets.UTF_8);
        Files.write(releaseDir.resolve("manifest.json"), b);
        Files.write(releaseDir.resolve("manifest.json.sig"), Signatures.sign(priv, b));
    }

    static Map<String, Object> m13Again(Map<String, Object> m13) {
        Map<String, Object> m = new LinkedHashMap<String, Object>(m13);
        m.put("sequence", ++seq);
        return m;
    }

    static boolean hasEventSince(State s, String type, int lastN) {
        for (int i = 0; i < Math.min(lastN, s.history.size()); i++) if (type.equals(s.history.get(i).get("event"))) return true;
        return false;
    }

    static boolean hasEvent(State s, String type) {
        for (Map<String, Object> e : s.history) if (type.equals(e.get("event"))) return true;
        return false;
    }

    static void check(String name, boolean ok) {
        System.out.println((ok ? "PASS  " : "FAIL  ") + name);
        if (ok) passed++; else failed++;
    }
}
