package kr.edurecover.liferevolve;

import android.util.Base64;
import android.webkit.CookieManager;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** 공식 플랫폼에 연속 화면을 올립니다. 로그인은 앱 안 웹 화면의 쿠키를 그대로 씁니다.
 *  AI·대화방·채팅으로 보내는 일은 여기서 하지 않습니다(플랫폼에서 받는 곳마다 따로 승인). */
final class BurstUploader {
    private final String origin = BuildConfig.PLATFORM_ORIGIN;

    String open(long tapMs) throws IOException {
        JSONObject r = post("/life/api/burst", json("tap_ms", tapMs));
        return r.optJSONObject("burst").optString("burst_id");
    }

    /** 돌려주는 값: 받아들였으면 서버가 계산한 SHA-256, 겹친 톡이라 버려졌으면 null */
    String frame(String burstId, int seq, long tapMs, byte[] png) throws IOException {
        JSONObject body = json("tap_ms", tapMs);
        try {
            body.put("seq", seq).put("name", "연속화면 " + seq + ".png")
                .put("base64", Base64.encodeToString(png, Base64.NO_WRAP));
        } catch (org.json.JSONException e) { throw new IOException(e); }
        JSONObject r = post("/life/api/burst/" + burstId + "/frames", body);
        return r.optBoolean("accepted") ? r.optString("sha256") : null;
    }

    /** 캡처 방지 화면: 그림은 보내지 않고 「막힘」 사실만 기록합니다. */
    void protectedScreen(String burstId, int seq, long tapMs) throws IOException {
        JSONObject body = json("tap_ms", tapMs);
        try { body.put("seq", seq).put("protected", true); } catch (org.json.JSONException e) { throw new IOException(e); }
        post("/life/api/burst/" + burstId + "/frames", body);
    }

    void close(String burstId) throws IOException { post("/life/api/burst/" + burstId + "/close", new JSONObject()); }

    private static JSONObject json(String k, long v) {
        try { return new JSONObject().put(k, v); } catch (org.json.JSONException e) { throw new IllegalStateException(e); }
    }

    private JSONObject post(String path, JSONObject body) throws IOException {
        String url = origin + path;
        if (!CapturePolicy.trustedUrl(url, origin)) throw new IOException("공식 플랫폼 주소가 아닙니다.");
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setRequestMethod("POST"); c.setDoOutput(true); c.setConnectTimeout(10_000); c.setReadTimeout(30_000);
            c.setRequestProperty("Content-Type", "application/json");
            c.setRequestProperty("X-LifeRevolve-Action", "screen-import");
            c.setRequestProperty("Origin", origin);
            String cookie = CookieManager.getInstance().getCookie(origin);
            if (cookie == null || cookie.isEmpty()) throw new IOException("플랫폼에 먼저 로그인하십시오.");
            c.setRequestProperty("Cookie", cookie);
            byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
            c.setFixedLengthStreamingMode(data.length);
            try (OutputStream o = c.getOutputStream()) { o.write(data); }
            int code = c.getResponseCode();
            InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
            String text = in == null ? "" : new String(in.readAllBytes(), StandardCharsets.UTF_8);
            if (code >= 400) throw new IOException("플랫폼 응답 " + code + " " + text);
            return text.isEmpty() ? new JSONObject() : new JSONObject(text);
        } catch (org.json.JSONException e) {
            throw new IOException("플랫폼 응답을 읽지 못했습니다.", e);
        } finally { c.disconnect(); }
    }
}
