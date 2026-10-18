"""Browser-only unsent text. This module never sends messages or credentials."""
from __future__ import annotations

import hashlib
import json
import os


_SCRIPT = r'''
globalThis.EduRecoverDraftStore = (() => {
  const ownerScope = __OWNER_SCOPE__;
  function create({workspace, onStatus = () => {}}) {
    const prefix = 'edurecover:unsent:v1:' + ownerScope + ':' + workspace + ':';
    const memory = new Map(), known = new Map(), failures = new Set();
    function report(ok, restored = false, key = '') {
      if(ok)failures.delete(key);else failures.add(key);
      if(ok && key === '_selection_')return;
      onStatus(ok ? (restored ? '작성 중이던 문장을 복원했습니다.' : '작성 중 문장을 이 브라우저에 보관했습니다.')
        : '브라우저에 문장을 보관하지 못했습니다. 다른 곳으로 옮기기 전에 문장을 복사하십시오.', !ok);
    }
    function read(key) {
      const raw = globalThis.localStorage.getItem(prefix + key);
      if (raw === null) return '';
      if (raw.length > 16000000) throw new Error('Invalid draft size');
      const data = JSON.parse(raw);
      if (!data || data.version !== 1 || typeof data.text !== 'string') throw new Error('Invalid draft');
      return data.text;
    }
    function get(key) {
      if(failures.has(key) && memory.has(key)){const text=memory.get(key);set(key,text);return text;}
      try {
        const text = read(key); memory.set(key, text); known.set(key, text);
        failures.delete(key);if (text) report(true, true, key);
        return text;
      } catch (_) { report(false, false, key); return memory.get(key) || ''; }
    }
    function set(key, text) {
      if (typeof text !== 'string') throw new TypeError('Draft text must be a string');
      memory.set(key, text);
      // Lifecycle events must not resurrect a sent draft or overwrite a newer
      // draft from another tab when this tab has not edited its own text.
      if (known.has(key) && known.get(key) === text && !failures.has(key)) return true;
      try {
        if (text) globalThis.localStorage.setItem(prefix + key, JSON.stringify({version: 1, text}));
        else globalThis.localStorage.removeItem(prefix + key);
        known.set(key, text); report(true, false, key); return true;
      } catch (_) { report(false, false, key); return false; }
    }
    function remove(key, expected) {
      try {
        if (expected !== undefined && read(key) !== expected) {
          memory.delete(key); known.set(key, ''); return false;
        }
        globalThis.localStorage.removeItem(prefix + key);
        memory.delete(key); known.set(key, ''); report(true, false, key); return true;
      } catch (_) { report(false, false, key); return false; }
    }
    return {get, set, delete: remove, get persisted() { return failures.size === 0; }};
  }
  return {create};
})();
'''


def draft_store_script() -> str:
    """Account namespace uses usernames only; it is not an encryption key."""
    users = [os.getenv(name, "") for name in
             ("EDURECOVER_OWNER_USER", "EDURECOVER_OWNER_USER ")]
    scope = hashlib.sha256(json.dumps(users, ensure_ascii=False).encode()).hexdigest()
    return _SCRIPT.replace("__OWNER_SCOPE__", json.dumps(scope))


def with_draft_store(html: str) -> str:
    """Prepend inside the existing script so existing UI checks stay valid."""
    return html.replace("<script>", "<script>\n" + draft_store_script(), 1)
