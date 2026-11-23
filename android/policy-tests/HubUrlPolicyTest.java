package ru.owrthub.app;

import java.net.URI;

/** Standalone regression tests; no Android runtime, network, or test dependencies. */
public final class HubUrlPolicyTest {
    private static int checks;

    public static void main(String[] args) {
        normalizeOrigins();
        rejectUnsafeConfiguration();
        debugHttpIsLocalOnly();
        compareOrigins();
        routeNavigation();
        matchOAuthRoutesExactly();
        System.out.println("HubUrlPolicy: " + checks + " checks passed");
    }

    private static void normalizeOrigins() {
        normalized("hub.example", null, "https://hub.example");
        normalized(" HTTPS://Hub.Example/ ", " 443 ", "https://hub.example");
        normalized("hub.example:8443", "8443", "https://hub.example:8443");
        normalized("hub.example", "65535", "https://hub.example:65535");
        normalized("https://hub.example:443/", "", "https://hub.example");
        normalized("192.168.2.1", "443", "https://192.168.2.1");
        normalized("192.168.2.1:8443", "", "https://192.168.2.1:8443");
        normalized("[2001:DB8:0:0:0:0:0:1]", "8443", "https://[2001:db8::1]:8443");
        normalized("https://[2001:db8::1]:8443/", null, "https://[2001:db8::1]:8443");
        normalized("[0:0:0:0:0:0:0:0]", "", "https://[::]");
        normalized("[1:0:0:2:0:0:3:4]", "", "https://[1::2:0:0:3:4]");
        normalized("[2001:db8:0:1:0:0:0:0]", "", "https://[2001:db8:0:1::]");
        normalized("[::ffff:192.0.2.1]", "", "https://[::ffff:c000:201]");
        normalized("xn--e1afmkfd.xn--p1ai", "", "https://xn--e1afmkfd.xn--p1ai");
    }

    private static void rejectUnsafeConfiguration() {
        String[] invalidHosts = {
            null, "", "   ", "http://hub.example", "ftp://hub.example", "javascript:alert(1)",
            "https:hub.example", "https:///hub.example", "//hub.example", "hub.example/app",
            "hub.example//", "hub.example/.", "hub.example/%2f", "hub.example?x=1",
            "hub.example?", "hub.example#fragment", "hub.example#", "hub.example:0",
            "hub.example:65536", "hub.example:", "hub.example:-1", "hub.example:+443",
            "hub.example:443:44", "user@hub.example", "user:secret@hub.example",
            "hub.example@elsewhere.example", "hub.example%2eelsewhere.example",
            "%68ub.example", "hub.example%00.elsewhere.example", "hub.example\\@elsewhere.example",
            "hub.\texample", "hub.example\r\n", "https://hub.example\n/",
            "hub example", "hub.example\u00a0", "hub.example.", "пример.рф",
            "-hub.example", "hub-.example", "hub..example", "hub_example", "[::1%25eth0]",
            "2001:db8::1", "[2001:::1]", "[1:2:3:4:5:6:7]", "[1:2:3:4:5:6:7:8:9]",
            "[1:2:3:4:5:6:7:8::]", "[1::2::3]", "[::gggg]", "[::1]extra",
            "[::1]:", "[::1]:65536", "127.1", "2130706433", "0177.0.0.1",
            "0x7f000001", "0x7f.0.0.1", "127.0.00.1", "256.1.1.1", "host.123",
            "[::ffff:127.1]", "[192.0.2.1::]"
        };
        for (final String host : invalidHosts) {
            rejects(new Runnable() {
                @Override public void run() { HubUrlPolicy.normalizeHub(host, ""); }
            }, "unsafe configuration: " + host);
        }
        String[] invalidPorts = {"0", "65536", "-1", "+443", "4 43", "https", "1.5", "443/path"};
        for (final String port : invalidPorts) {
            rejects(new Runnable() {
                @Override public void run() { HubUrlPolicy.normalizeHub("hub.example", port); }
            }, "unsafe port: " + port);
        }
        rejects(new Runnable() {
            @Override public void run() { HubUrlPolicy.normalizeHub("hub.example:8443", "443"); }
        }, "conflicting ports");
    }

    private static void debugHttpIsLocalOnly() {
        String[] local = {"localhost", "127.0.0.1", "[::1]", "10.0.2.2"};
        for (final String host : local) {
            equal("http://" + host + ":8080",
                    HubUrlPolicy.normalizeHub("http://" + host, "8080", true).toString(),
                    "local HTTP requires explicit debug override");
            rejects(new Runnable() {
                @Override public void run() { HubUrlPolicy.normalizeHub("http://" + host, "8080"); }
            }, "production HTTP: " + host);
        }
        String[] remote = {"hub.example", "localhost.example", "192.168.2.1", "13.143.167.219",
            "127.0.0.2", "[::ffff:127.0.0.1]", "[2001:db8::1]"};
        for (final String host : remote) {
            rejects(new Runnable() {
                @Override public void run() {
                    HubUrlPolicy.normalizeHub("http://" + host, "8080", true);
                }
            }, "non-debug-local HTTP: " + host);
        }
        equal("http://localhost", HubUrlPolicy.normalizeHub("http://localhost:80", "", true).toString(),
                "default debug HTTP port");
        normalized("localhost", "8080", "https://localhost:8080");
    }

    private static void compareOrigins() {
        URI hub = URI.create("https://hub.example");
        String[] same = {"https://hub.example", "HTTPS://HUB.EXAMPLE:443/", "https://hub.example/a?b=c#d",
            "https://hub.example:00443/login"};
        for (String target : same) {
            check(HubUrlPolicy.sameOrigin(hub, target), "same origin: " + target);
        }
        String[] other = {"http://hub.example", "https://hub.example:8443", "https://sub.hub.example",
            "https://hub.example.elsewhere.example", "https://hub.example.", "https://elsewhere.example",
            "https://hub.example@elsewhere.example", "https://elsewhere.example@hub.example",
            "https://%68ub.example", "https://hub.example%2eelsewhere.example", "/login", "//hub.example/login",
            "https://hub.example\\@elsewhere.example", " https://hub.example", "javascript:alert(1)", null};
        for (String target : other) {
            check(!HubUrlPolicy.sameOrigin(hub, target), "different or invalid origin: " + target);
        }
        check(HubUrlPolicy.sameOrigin(URI.create("https://[2001:db8::1]:8443"),
                "https://[2001:0DB8:0:0:0:0:0:1]:8443/home"), "equivalent IPv6 forms");
        check(!HubUrlPolicy.sameOrigin(URI.create("https://[::1]"), "https://[::2]/"), "IPv6 host mismatch");
        check(HubUrlPolicy.sameOrigin(URI.create("https://[::ffff:c000:201]"),
                "https://[::ffff:192.0.2.1]/"), "equivalent IPv4-mapped IPv6 forms");
        check(!HubUrlPolicy.sameOrigin(null, "https://hub.example"), "missing base");
    }

    private static void routeNavigation() {
        URI hub = URI.create("https://hub.example:8443");
        decision(hub, "https://hub.example:8443/dashboard?router=1#status", HubUrlPolicy.Decision.INTERNAL);
        decision(hub, "https://hub.example/", HubUrlPolicy.Decision.EXTERNAL);
        decision(hub, "https://other.example/", HubUrlPolicy.Decision.EXTERNAL);
        decision(hub, "https://sub.hub.example:8443/", HubUrlPolicy.Decision.EXTERNAL);
        decision(hub, "http://other.example/", HubUrlPolicy.Decision.EXTERNAL);
        decision(hub, "http://hub.example:8443/", HubUrlPolicy.Decision.BLOCKED);
        decision(hub, "http://hub.example/", HubUrlPolicy.Decision.BLOCKED);
        String[] blocked = {"javascript:alert(1)", "JAVASCRIPT:alert(1)", "file:///etc/passwd",
            "content://settings/", "intent://hub.example/#Intent;end", "data:text/html,hello",
            "about:blank", "blob:https://hub.example/id", "ftp://hub.example/", "tel:123", "mailto:a@example.org",
            "https://user:password@hub.example:8443/", "https://user:password@other.example/",
            "https://hub.example\\@other.example/", "https://%68ub.example:8443/",
            "https://other.example:65536/", "https://other.example:/", "//other.example/", "/home",
            "https://127.1/", "https://2130706433/", "https://0177.0.0.1/", "", null};
        for (String target : blocked) {
            decision(hub, target, HubUrlPolicy.Decision.BLOCKED);
        }
        decision(null, "https://hub.example", HubUrlPolicy.Decision.BLOCKED);
        decision(URI.create("http://hub.example"), "http://hub.example/", HubUrlPolicy.Decision.BLOCKED);
        decision(HubUrlPolicy.normalizeHub("http://10.0.2.2", "8080", true),
                "http://10.0.2.2:8080/", HubUrlPolicy.Decision.INTERNAL);
    }

    private static void matchOAuthRoutesExactly() {
        URI hub = URI.create("https://hub.example");
        decision(hub, "https://hub.example/oauth/github/start", HubUrlPolicy.Decision.EXTERNAL_BROWSER);
        decision(hub, "https://hub.example/oauth/vk/start?return_to=%2Fhome", HubUrlPolicy.Decision.EXTERNAL_BROWSER);
        String[] internal = {"/oauth/github/callback?code=dummy", "/oauth/github/start/", "/oauth/github/starting",
            "/prefix/oauth/github/start", "/oauth//start", "/oauth/github/extra/start", "/oauth/../start",
            "/oauth/%67ithub/start", "/oauth/github%2fextra/start", "/oauth/github/%73tart", "/OAuth/github/start"};
        for (String path : internal) {
            decision(hub, "https://hub.example" + path, HubUrlPolicy.Decision.INTERNAL);
        }
        decision(hub, "https://other.example/oauth/github/start", HubUrlPolicy.Decision.EXTERNAL);
        decision(hub, "https://hub.example:8443/oauth/github/start", HubUrlPolicy.Decision.EXTERNAL);
        decision(hub, "http://hub.example/oauth/github/start", HubUrlPolicy.Decision.BLOCKED);
    }

    private static void normalized(String input, String port, String expected) {
        equal(expected, HubUrlPolicy.normalizeHub(input, port).toString(), "normalize: " + input);
    }

    private static void decision(URI hub, String target, HubUrlPolicy.Decision expected) {
        equal(expected, HubUrlPolicy.classify(hub, target), "classify: " + target);
    }

    private static void rejects(Runnable action, String label) {
        checks++;
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("Expected rejection: " + label);
    }

    private static void equal(Object expected, Object actual, String label) {
        check(expected.equals(actual), label + ": expected " + expected + ", got " + actual);
    }

    private static void check(boolean condition, String label) {
        checks++;
        if (!condition) {
            throw new AssertionError(label);
        }
    }
}
