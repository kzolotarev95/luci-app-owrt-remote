package ru.owrthub.app;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Pure Java URL policy shared by configuration and WebView navigation. */
public final class HubUrlPolicy {
  public enum Decision {
    INTERNAL,
    EXTERNAL,
    EXTERNAL_BROWSER,
    BLOCKED
  }

  private HubUrlPolicy() {}

  /** Accepts an ASCII hostname, IPv4 address, bracketed IPv6 address, or HTTPS origin. */
  public static URI normalizeHub(String input, String port) {
    return normalizeHub(input, port, false);
  }

  /**
   * The caller must supply BuildConfig.DEBUG for allowLocalHttp. HTTP is then permitted only for
   * localhost, loopback, or the Android emulator host alias. The result contains only the canonical
   * origin, without a trailing slash.
   */
  public static URI normalizeHub(String input, String port, boolean allowLocalHttp) {
    if (input == null || input.trim().isEmpty()) {
      throw invalid("A Hub hostname is required");
    }
    for (int index = 0; index < input.length(); index++) {
      if (input.charAt(index) < 0x20 || input.charAt(index) == 0x7f) {
        throw invalid("Control characters are not allowed in the Hub address");
      }
    }
    String text = input.trim();
    if (!text.contains("://")) {
      text = "https://" + text;
    }
    NetworkUrl url = parseNetworkUrl(text);
    String path = url.uri.getRawPath();
    if ((path != null && !path.isEmpty() && !path.equals("/"))
        || url.uri.getRawQuery() != null
        || url.uri.getRawFragment() != null) {
      throw invalid("Enter only the Hub origin, without a path, query, or fragment");
    }
    if (url.scheme.equals("http") && !(allowLocalHttp && isLocalDebugHost(url.host))) {
      throw invalid("The Hub must use HTTPS");
    }
    int configuredPort = parseOptionalPort(port);
    if (configuredPort != -1 && url.explicitPort != -1 && configuredPort != url.explicitPort) {
      throw invalid("The URL port and port field must match");
    }
    int selectedPort = configuredPort != -1 ? configuredPort : url.explicitPort;
    return origin(url.scheme, url.host, selectedPort);
  }

  /** Relative references are not accepted: WebView callbacks provide absolute URLs. */
  public static boolean sameOrigin(URI hub, String target) {
    try {
      NetworkUrl base = parseNetworkUrl(hub == null ? null : hub.toString());
      NetworkUrl next = parseNetworkUrl(target);
      return sameOrigin(base, next);
    } catch (IllegalArgumentException exception) {
      return false;
    }
  }

  /**
   * A routing decision does not authorize launching an external application. EXTERNAL needs a
   * main-frame user gesture. EXTERNAL_BROWSER identifies only the Hub's exact OAuth start route;
   * the caller must also guard OAuth launches against repeats. Neither external decision may be
   * loaded in the WebView.
   */
  public static Decision classify(URI hub, String target) {
    try {
      NetworkUrl base = parseNetworkUrl(hub == null ? null : hub.toString());
      NetworkUrl next = parseNetworkUrl(target);
      if (base.scheme.equals("http") && !isLocalDebugHost(base.host)) {
        return Decision.BLOCKED;
      }
      if (sameOrigin(base, next)) {
        return isOAuthStart(next.uri.getRawPath()) ? Decision.EXTERNAL_BROWSER : Decision.INTERNAL;
      }
      // A Hub HTTP endpoint must never receive an HTTPS session navigation.
      if (base.scheme.equals("https")
          && next.scheme.equals("http")
          && base.host.equals(next.host)) {
        return Decision.BLOCKED;
      }
      return Decision.EXTERNAL;
    } catch (IllegalArgumentException exception) {
      return Decision.BLOCKED;
    }
  }

  private static boolean sameOrigin(NetworkUrl left, NetworkUrl right) {
    return left.scheme.equals(right.scheme)
        && left.host.equals(right.host)
        && left.effectivePort() == right.effectivePort();
  }

  private static boolean isOAuthStart(String rawPath) {
    return rawPath != null && rawPath.matches("/oauth/[A-Za-z0-9_-]+/start");
  }

  private static NetworkUrl parseNetworkUrl(String text) {
    if (text == null || text.isEmpty()) {
      throw invalid("An absolute HTTP or HTTPS URL is required");
    }
    for (int index = 0; index < text.length(); index++) {
      char character = text.charAt(index);
      if (character <= 0x20
          || character == 0x7f
          || character == '\\'
          || Character.isWhitespace(character)
          || Character.isSpaceChar(character)) {
        throw invalid("Whitespace, controls, and backslashes are not allowed in URLs");
      }
    }
    URI uri;
    try {
      uri = new URI(text);
    } catch (URISyntaxException exception) {
      throw invalid("The URL is malformed");
    }
    String scheme = uri.getScheme();
    if (scheme == null || uri.isOpaque()) {
      throw invalid("An absolute HTTP or HTTPS URL is required");
    }
    scheme = scheme.toLowerCase(Locale.ROOT);
    if (!scheme.equals("https") && !scheme.equals("http")) {
      throw invalid("Only HTTP and HTTPS URLs are allowed");
    }
    String authority = uri.getRawAuthority();
    if (authority == null
        || authority.isEmpty()
        || authority.indexOf('@') != -1
        || authority.indexOf('%') != -1) {
      throw invalid("URL credentials and encoded hostnames are not allowed");
    }
    String host;
    int explicitPort = -1;
    if (authority.startsWith("[")) {
      int close = authority.indexOf(']');
      if (close < 0) {
        throw invalid("IPv6 addresses must be enclosed in brackets");
      }
      host = "[" + canonicalIpv6(authority.substring(1, close)) + "]";
      String suffix = authority.substring(close + 1);
      if (!suffix.isEmpty()) {
        if (!suffix.startsWith(":")) {
          throw invalid("The host or port is malformed");
        }
        explicitPort = parsePort(suffix.substring(1));
      }
    } else {
      int colon = authority.indexOf(':');
      String hostText = colon == -1 ? authority : authority.substring(0, colon);
      host = canonicalDnsOrIpv4(hostText);
      if (colon != -1) {
        explicitPort = parsePort(authority.substring(colon + 1));
      }
    }
    return new NetworkUrl(uri, scheme, host, explicitPort);
  }

  private static String canonicalDnsOrIpv4(String text) {
    if (text.isEmpty() || text.length() > 253 || text.endsWith(".")) {
      throw invalid("The hostname is invalid");
    }
    String host = text.toLowerCase(Locale.ROOT);
    String[] labels = host.split("\\.", -1);
    for (String label : labels) {
      if (!label.matches("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")) {
        throw invalid("Use an ASCII hostname or its punycode form");
      }
    }
    String last = labels[labels.length - 1];
    // Browsers interpret short, octal, integer, and hexadecimal numeric hosts
    // as IPv4. Accept only the unambiguous four-part decimal representation.
    if (last.matches("[0-9]+") || last.matches("0x[0-9a-f]+")) {
      parseIpv4(host);
    }
    return host;
  }

  private static int[] parseIpv4(String text) {
    String[] octets = text.split("\\.", -1);
    if (octets.length != 4) {
      throw invalid("IPv4 addresses need four decimal octets");
    }
    int[] result = new int[4];
    for (int index = 0; index < octets.length; index++) {
      String octet = octets[index];
      if (!octet.matches("0|[1-9][0-9]{0,2}")) {
        throw invalid("IPv4 octets must be decimal without leading zeroes");
      }
      int value = Integer.parseInt(octet);
      if (value > 255) {
        throw invalid("IPv4 octets must be between 0 and 255");
      }
      result[index] = value;
    }
    return result;
  }

  /** Canonicalizes IPv6 without DNS resolution or any network access. */
  private static String canonicalIpv6(String text) {
    int compression = text.indexOf("::");
    if (text.isEmpty()
        || text.indexOf('%') != -1
        || (compression != -1 && text.indexOf("::", compression + 2) != -1)) {
      throw invalid("The IPv6 address is invalid");
    }
    List<Integer> left;
    List<Integer> right;
    if (compression == -1) {
      left = ipv6Groups(text, true);
      right = new ArrayList<Integer>();
      if (left.size() != 8) {
        throw invalid("The IPv6 address is invalid");
      }
    } else {
      left = ipv6Groups(text.substring(0, compression), false);
      right = ipv6Groups(text.substring(compression + 2), true);
      if (left.size() + right.size() >= 8) {
        throw invalid("The IPv6 address is invalid");
      }
    }
    int[] groups = new int[8];
    for (int index = 0; index < left.size(); index++) {
      groups[index] = left.get(index);
    }
    for (int index = 0; index < right.size(); index++) {
      groups[8 - right.size() + index] = right.get(index);
    }
    int zeroStart = -1;
    int zeroLength = 1;
    for (int index = 0; index < groups.length; ) {
      if (groups[index] != 0) {
        index++;
        continue;
      }
      int end = index;
      while (end < groups.length && groups[end] == 0) {
        end++;
      }
      if (end - index > zeroLength) {
        zeroStart = index;
        zeroLength = end - index;
      }
      index = end;
    }
    StringBuilder result = new StringBuilder();
    for (int index = 0; index < groups.length; index++) {
      if (index == zeroStart) {
        result.append("::");
        index += zeroLength - 1;
      } else {
        if (result.length() > 0 && result.charAt(result.length() - 1) != ':') {
          result.append(':');
        }
        result.append(Integer.toHexString(groups[index]));
      }
    }
    return result.toString();
  }

  private static List<Integer> ipv6Groups(String text, boolean allowIpv4Suffix) {
    List<Integer> result = new ArrayList<Integer>();
    if (text.isEmpty()) {
      return result;
    }
    String[] parts = text.split(":", -1);
    for (int index = 0; index < parts.length; index++) {
      String part = parts[index];
      if (part.indexOf('.') != -1 && allowIpv4Suffix && index == parts.length - 1) {
        int[] octets = parseIpv4(part);
        result.add((octets[0] << 8) | octets[1]);
        result.add((octets[2] << 8) | octets[3]);
      } else if (part.matches("[0-9a-fA-F]{1,4}")) {
        result.add(Integer.parseInt(part, 16));
      } else {
        throw invalid("The IPv6 address is invalid");
      }
    }
    return result;
  }

  private static int parseOptionalPort(String text) {
    return text == null || text.trim().isEmpty() ? -1 : parsePort(text.trim());
  }

  private static int parsePort(String text) {
    if (!text.matches("[0-9]{1,5}")) {
      throw invalid("The port must be between 1 and 65535");
    }
    int port = Integer.parseInt(text);
    if (port < 1 || port > 65535) {
      throw invalid("The port must be between 1 and 65535");
    }
    return port;
  }

  private static boolean isLocalDebugHost(String host) {
    return host.equals("localhost")
        || host.equals("127.0.0.1")
        || host.equals("[::1]")
        || host.equals("10.0.2.2");
  }

  private static URI origin(String scheme, String host, int port) {
    int defaultPort = scheme.equals("https") ? 443 : 80;
    String suffix = port == -1 || port == defaultPort ? "" : ":" + port;
    return URI.create(scheme + "://" + host + suffix);
  }

  private static IllegalArgumentException invalid(String reason) {
    return new IllegalArgumentException(reason);
  }

  private static final class NetworkUrl {
    final URI uri;
    final String scheme;
    final String host;
    final int explicitPort;

    NetworkUrl(URI uri, String scheme, String host, int explicitPort) {
      this.uri = uri;
      this.scheme = scheme;
      this.host = host;
      this.explicitPort = explicitPort;
    }

    int effectivePort() {
      return explicitPort != -1 ? explicitPort : scheme.equals("https") ? 443 : 80;
    }
  }
}
