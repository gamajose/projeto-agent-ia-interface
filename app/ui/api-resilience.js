(() => {
  function detailMessage(value) {
    if (Array.isArray(value)) {
      return value.map((item) => {
        if (item && typeof item === "object") {
          const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
          return `${location ? `${location}: ` : ""}${item.msg || JSON.stringify(item)}`;
        }
        return String(item);
      }).join(" · ");
    }
    if (value && typeof value === "object") return value.message || JSON.stringify(value);
    return String(value || "");
  }

  async function resilientApi(path, options = {}) {
    const init = { ...options, headers: { ...(options.headers || {}) } };
    if (init.body && typeof init.body !== "string") {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(init.body);
    }
    if ((init.method || "GET").toUpperCase() !== "GET") init.headers["X-Agent-UI"] = "1";

    const response = await fetch(path, init);
    const raw = await response.text();
    const contentType = response.headers.get("content-type") || "";
    let payload = raw;

    if (raw && (contentType.includes("application/json") || /^[\s]*[\[{]/.test(raw))) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = raw;
      }
    } else if (!raw) {
      payload = null;
    }

    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? payload.detail ?? payload.message : payload;
      const rendered = detailMessage(detail).trim();
      throw new Error(rendered || `Erro HTTP ${response.status}`);
    }

    if (contentType.includes("application/json") && typeof payload === "string" && raw) {
      throw new Error(`O servidor devolveu uma resposta inválida em ${path}. Consulte os logs do agent-ia-web.`);
    }
    return payload;
  }

  window.api = resilientApi;
  api = resilientApi;
})();
