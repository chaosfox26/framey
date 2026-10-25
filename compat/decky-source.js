/* Independently authored source-port helpers. Prepend inside a Framey plugin. */
const createDeckySourceAdapter = function (api) {
  "use strict";
  if (!api || typeof api.call !== "function" || typeof api.cleanup !== "function" ||
      !api.events || typeof api.events.on !== "function") {
    throw new TypeError("A plugin-owned Framey API with calls, events and cleanup is required");
  }

  const listeners = new Map();
  const pending = new Set();
  let disposed = false;
  let detachCleanup = function () {};
  const disposedError = () => Object.assign(new Error("Source adapter disposed"), { code: "adapter_disposed" });
  const live = () => { if (disposed) throw disposedError(); };
  const methodName = name => {
    if (typeof name !== "string" || !name.length) throw new TypeError("Method must be a nonempty string");
  };
  const eventName = name => {
    if (typeof name !== "string" || !/^[a-z0-9_.-]{1,40}$/.test(name)) {
      throw new TypeError("Event name must match [a-z0-9_.-]{1,40}");
    }
  };
  const report = (name, error) => {
    try {
      if (typeof api.log === "function") api.log("error", "source event " + name + ": " + String(error && error.message || error));
    } catch (_) {}
  };

  const call = async (method, ...args) => {
    live();
    methodName(method);
    return new Promise((resolve, reject) => {
      const request = { reject };
      pending.add(request);
      const fail = error => {
        if (pending.delete(request)) reject(error);
      };
      try {
        Promise.resolve(api.call(method, args)).then(value => {
          if (!pending.delete(request)) return;
          try {
            if (value && value.error) {
              const error = new Error(String(value.error));
              if (typeof value.code === "string") error.code = value.code;
              reject(error);
            } else resolve(value);
          } catch (error) { reject(error); }
        }, fail);
      } catch (error) { fail(error); }
    });
  };

  const callable = method => {
    live();
    methodName(method);
    return (...args) => call(method, ...args);
  };

  const addEventListener = (name, listener) => {
    live();
    eventName(name);
    if (typeof listener !== "function") throw new TypeError("Event listener must be a function");
    const existing = listeners.get(name);
    if (existing && existing.has(listener)) return listener;
    const record = { off: null };
    record.off = api.events.on(name, payload => {
      if (disposed || !listeners.get(name) || listeners.get(name).get(listener) !== record) return;
      try {
        Promise.resolve(listener(...(Array.isArray(payload) ? payload : [payload]))).catch(error => report(name, error));
      } catch (error) { report(name, error); }
    });
    if (typeof record.off !== "function") throw new TypeError("Framey event registration must return a remover");
    if (!listeners.has(name)) listeners.set(name, new Map());
    listeners.get(name).set(listener, record);
    return listener;
  };

  const removeEventListener = (name, listener) => {
    const entries = listeners.get(name);
    const record = entries && entries.get(listener);
    if (!record) return;
    entries.delete(listener);
    if (!entries.size) listeners.delete(name);
    record.off();
  };

  const definePlugin = factory => {
    live();
    if (typeof factory !== "function") throw new TypeError("Plugin factory must be a function");
    return (...args) => { live(); return factory(...args); };
  };

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    const registrations = [...listeners.entries()];
    listeners.clear();
    registrations.forEach(([name, entries]) => entries.forEach(record => {
      try { record.off(); } catch (error) { report(name, error); }
    }));
    pending.forEach(request => request.reject(disposedError()));
    pending.clear();
    detachCleanup();
  };
  const detach = api.cleanup(cleanup);
  if (typeof detach === "function") {
    detachCleanup = detach;
    if (disposed) detachCleanup();
  }
  return Object.freeze({ call, callable, addEventListener, removeEventListener, definePlugin, cleanup });
};

if (typeof module === "object" && module && module.exports) {
  module.exports = { createDeckySourceAdapter };
}
