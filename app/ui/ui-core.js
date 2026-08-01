(() => {
  const listeners = new Map();
  const store = new Map();

  function on(event, handler) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(handler);
    return () => listeners.get(event)?.delete(handler);
  }

  function emit(event, payload) {
    (listeners.get(event) || []).forEach((handler) => {
      try { handler(payload); } catch (error) { console.error(`AgentUI event ${event}`, error); }
    });
  }

  function set(key, value) {
    store.set(key, value);
    emit(`store:${key}`, value);
    return value;
  }

  function get(key, fallback = null) {
    return store.has(key) ? store.get(key) : fallback;
  }

  function storage(key, fallback) {
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "null");
      return parsed ?? fallback;
    } catch {
      return fallback;
    }
  }

  function persist(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
    emit(`storage:${key}`, value);
    return value;
  }

  window.AgentUI = Object.freeze({ on, emit, set, get, storage, persist });
})();
