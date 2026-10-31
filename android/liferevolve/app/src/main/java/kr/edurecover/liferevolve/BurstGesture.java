package kr.edurecover.liferevolve;

/** 연속 모드 손짓 규칙(서버 liferevolve_burst.classify_tap 과 같음). Android 없이 시험할 수 있습니다.
 *  두 번 톡(400ms·40px 이내) → 연속 시작 + 1번 화면, 그 뒤 한 번 톡마다 다음 번호 화면,
 *  120ms 안의 겹친 톡은 버림, 길게 누르기 또는 5초 동안 톡이 없으면 종료. */
public final class BurstGesture {
    public static final long DOUBLE_TAP_MS = 400, DEDUPE_MS = 120, IDLE_CLOSE_MS = 5_000;
    public static final float DOUBLE_TAP_PX = 40f;
    public enum Action { NONE, OPEN, FRAME, DUPLICATE, CLOSE }

    private boolean open = false, havePrev = false;
    private long prevMs, lastAcceptedMs; private float prevX, prevY; private int seq = 0;

    public boolean isOpen() { return open; }
    public int seq() { return seq; }

    /** 시간 경과만으로 닫힐지 확인합니다. 닫혔으면 CLOSE. */
    public Action tick(long nowMs) {
        if (open && nowMs - lastAcceptedMs > IDLE_CLOSE_MS) { open = false; havePrev = false; return Action.CLOSE; }
        return Action.NONE;
    }

    public Action onTap(long tMs, float x, float y, boolean longPress) {
        tick(tMs); // 5초 넘게 쉬었으면 닫힌 상태에서 새로 셉니다
        if (longPress) { boolean was = open; open = false; havePrev = false; return was ? Action.CLOSE : Action.NONE; }
        if (open) {
            if (tMs - lastAcceptedMs <= DEDUPE_MS) return Action.DUPLICATE;
            seq++; lastAcceptedMs = tMs; return Action.FRAME;
        }
        if (havePrev) {
            long dt = tMs - prevMs; double dist = Math.hypot(x - prevX, y - prevY);
            if (dt <= DEDUPE_MS) return Action.DUPLICATE;
            if (dt <= DOUBLE_TAP_MS && dist <= DOUBLE_TAP_PX) {
                open = true; havePrev = false; seq = 1; lastAcceptedMs = tMs; return Action.OPEN;
            }
        }
        havePrev = true; prevMs = tMs; prevX = x; prevY = y;
        return Action.NONE;
    }
}
