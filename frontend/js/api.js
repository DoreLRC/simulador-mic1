// Cliente da API FastAPI. Erros viram ApiError com a mensagem e, na montagem, as linhas.

export class ApiError extends Error {
  constructor(status, detail, errors = null) {
    super(detail);
    this.status = status;
    this.errors = errors;
  }
}

async function request(method, url, body) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new ApiError(0, "sem conexão com o servidor");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, describe(data.detail, response.status), data.errors ?? null);
  }
  return data;
}

/** A validação do FastAPI devolve uma lista; os erros da nossa API, uma frase. */
function describe(detail, status) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => `${(item.loc ?? []).slice(1).join(".")}: ${item.msg}`).join("; ");
  }
  return `erro HTTP ${status}`;
}

export const api = {
  examples: () => request("GET", "/api/examples"),
  microprogram: (mode) => request("GET", `/api/microprogram?mode=${encodeURIComponent(mode)}`),
  state: () => request("GET", "/api/state"),
  assemble: (source, settings) => request("POST", "/api/assemble", { source, ...settings }),
  step: (granularity) => request("POST", "/api/step", { granularity }),
  reset: (settings) => request("POST", "/api/reset", settings),
};
