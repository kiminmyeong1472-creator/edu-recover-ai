package kr.edurecover.liferevolve;

import java.net.URI;

/** Android 장치 없이도 시험할 수 있는 취득 범위·주소 검증 규칙입니다. */
public final class CapturePolicy {
    public static final int MAX_FRAMES = 40;
    public static final long MAX_SESSION_BYTES = 128L * 1024 * 1024;
    public static final long MAX_PIXELS = 8_000_000L;
    public static final long FRAME_INTERVAL_MS = 2_000L;
    private CapturePolicy() {}
    public static boolean acceptableSize(int width, int height) {
        return width > 0 && height > 0 && (long) width * height <= MAX_PIXELS;
    }
    public static boolean canSave(int count, long bytes, long newBytes) {
        return count >= 0 && count < MAX_FRAMES && bytes >= 0 && newBytes >= 0
                && newBytes <= MAX_SESSION_BYTES - bytes;
    }
    public static boolean trustedUrl(String url, String origin) {
        try {
            URI u = new URI(url), o = new URI(origin);
            return "https".equalsIgnoreCase(u.getScheme()) && u.getHost() != null
                    && u.getUserInfo() == null && u.getHost().equalsIgnoreCase(o.getHost())
                    && effectivePort(u) == effectivePort(o);
        } catch (Exception ex) { return false; }
    }
    private static int effectivePort(URI uri) { return uri.getPort() == -1 ? 443 : uri.getPort(); }
    public static boolean validFrameName(String name) {
        return name != null && name.matches("[0-9]{13}-[a-f0-9]{8}\\.png");
    }
}
