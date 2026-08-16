"use strict";
var EmbabelApplianceClient = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/client/index.ts
  var index_exports = {};
  __export(index_exports, {
    ApplianceClient: () => ApplianceClient,
    HttpTransport: () => HttpTransport,
    KgClient: () => KgClient,
    basicAuth: () => basicAuth,
    expect: () => expect,
    isBackgroundHandle: () => isBackgroundHandle,
    isOk: () => isOk
  });

  // src/client/outcome.ts
  var ok = (value) => ({ ok: true, value });
  var failure = (kind, message, status, body) => {
    const f = { ok: false, kind, message };
    if (status !== void 0) f.status = status;
    if (body !== void 0) f.body = body;
    return f;
  };
  var isOk = (outcome) => outcome.ok;
  function expect(outcome) {
    if (outcome.ok) return outcome.value;
    throw new Error(`${outcome.kind}: ${outcome.message}`);
  }

  // src/client/transport.ts
  function basicAuth(username, password) {
    const encoded = typeof globalThis.btoa === "function" ? globalThis.btoa(`${username}:${password}`) : (
      // Node before the global btoa, and the Electron main process.
      Buffer.from(`${username}:${password}`, "utf8").toString("base64")
    );
    return { Authorization: `Basic ${encoded}` };
  }
  function looksLikeUnmappedRoute(status, body) {
    if (status === 405) return true;
    if (status !== 404) return false;
    if (body === void 0 || body === null || typeof body !== "object") return true;
    const shape = body;
    if (typeof shape["error"] === "string" && !("timestamp" in shape) && !("path" in shape)) {
      return false;
    }
    return "timestamp" in shape || "path" in shape || !("error" in shape);
  }
  var HttpTransport = class {
    baseUrl;
    headers;
    doFetch;
    defaultTimeoutMs;
    constructor(config) {
      this.baseUrl = config.baseUrl.replace(/\/$/, "");
      this.headers = config.headers ?? (() => ({}));
      const injected = config.fetch ?? globalThis.fetch;
      if (typeof injected !== "function") {
        throw new Error("No fetch available: pass one in HttpTransportConfig.fetch");
      }
      this.doFetch = injected;
      this.defaultTimeoutMs = config.timeoutMs ?? 3e4;
    }
    url(spec) {
      const query = spec.query ?? {};
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) {
        if (value !== void 0) params.append(key, String(value));
      }
      const search = params.toString();
      return `${this.baseUrl}${spec.path}${search ? `?${search}` : ""}`;
    }
    async send(spec) {
      const controller = new AbortController();
      const timeoutMs = spec.timeoutMs ?? this.defaultTimeoutMs;
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      let response;
      try {
        const init = {
          method: spec.method,
          signal: controller.signal,
          headers: {
            Accept: "application/json",
            ...spec.body === void 0 ? {} : { "Content-Type": "application/json" },
            ...this.headers()
          }
        };
        if (spec.body !== void 0) init.body = JSON.stringify(spec.body);
        response = await this.doFetch(this.url(spec), init);
      } catch (cause) {
        const aborted = cause instanceof Error && cause.name === "AbortError";
        return failure(
          "unreachable",
          aborted ? `The appliance did not answer within ${timeoutMs}ms` : `Could not reach the appliance: ${cause instanceof Error ? cause.message : String(cause)}`
        );
      } finally {
        clearTimeout(timer);
      }
      const text = await response.text().catch(() => "");
      let body;
      try {
        body = text.length > 0 ? JSON.parse(text) : void 0;
      } catch {
        body = void 0;
      }
      if (response.ok) return ok(body);
      if (response.status === 401 || response.status === 403) {
        return failure("unauthorized", serverMessage(body) ?? "Not authorized", response.status, body);
      }
      if (looksLikeUnmappedRoute(response.status, body)) {
        return failure(
          "unsupported",
          `This appliance does not have ${spec.method} ${spec.path} \u2014 it is likely older than this client`,
          response.status,
          body
        );
      }
      if (response.status >= 500) {
        return failure("failed", serverMessage(body) ?? `The appliance failed (${response.status})`, response.status, body);
      }
      return failure("refused", serverMessage(body) ?? `Request refused (${response.status})`, response.status, body);
    }
  };
  function serverMessage(body) {
    if (body !== null && typeof body === "object") {
      const error = body["error"];
      if (typeof error === "string" && error.length > 0) return error;
    }
    return void 0;
  }

  // src/client/kg.ts
  var KG = "/api/v1/admin/kg";
  var TIMEOUTS = {
    execute: 18e4,
    generate: 12e4,
    saveView: 6e4
  };
  function isBackgroundHandle(outcome) {
    return !("rowCount" in outcome);
  }
  var KgClient = class {
    constructor(transport) {
      this.transport = transport;
    }
    /**
     * The acting user's reachable schema — the SAME snapshot the preflight validates against, so
     * what an editor offers for completion and what validation accepts can never disagree.
     */
    schema() {
      return this.transport.send({ method: "GET", path: `${KG}/schema` });
    }
    /** The strict preflight WITHOUT execution — the editor's as-you-type validator. */
    validate(cypher) {
      return this.transport.send({ method: "POST", path: `${KG}/validate`, body: { cypher } });
    }
    /**
     * Generate cypher and an explanation, without running it. Paired with `execute` so a console can
     * show what will run BEFORE paying for it — a caller that only sees the cypher when the whole
     * run returns looks hung.
     */
    generate(question) {
      return this.transport.send({
        method: "POST",
        path: `${KG}/generate`,
        body: { question },
        timeoutMs: TIMEOUTS.generate
      });
    }
    /** Answer a natural-language question: generate, then execute, scoped to the acting user. */
    ask(question) {
      return this.transport.send({
        method: "POST",
        path: `${KG}/ask`,
        body: { question },
        timeoutMs: TIMEOUTS.execute
      });
    }
    /**
     * Execute verbatim cypher through the virtual-cypher engine.
     *
     * With `background`, the result is a handle — use {@link isBackgroundHandle} to tell them apart.
     * With `waitSeconds`, a result whose `run` is set means "not yet": its `rows` are empty because
     * the run has not finished, NEVER because the graph is empty.
     */
    execute(cypher, options = {}) {
      const query = {};
      if (options.background) query["background"] = true;
      if (options.waitSeconds !== void 0) query["waitSeconds"] = options.waitSeconds;
      return this.transport.send({
        method: "POST",
        path: `${KG}/execute`,
        query,
        body: { cypher },
        timeoutMs: TIMEOUTS.execute
      });
    }
    /** The acting user's in-flight runs — for a listing, or a kill button. */
    runs() {
      return this.transport.send({ method: "GET", path: `${KG}/runs` });
    }
    /** State and, once settled, the result of a background run. */
    run(runId) {
      return this.transport.send({ method: "GET", path: `${KG}/runs/${encodeURIComponent(runId)}` });
    }
    /** Cancel an in-flight run. Committed graph-cache work survives the kill. */
    kill(runId) {
      return this.transport.send({ method: "POST", path: `${KG}/kill/${encodeURIComponent(runId)}` });
    }
    /** Answer a run parked awaiting input. */
    answer(runId, choice) {
      return this.transport.send({
        method: "POST",
        path: `${KG}/runs/${encodeURIComponent(runId)}/answer`,
        body: { choice }
      });
    }
    /** Saved views across the world and realm tiers, with their cache state. */
    views() {
      return this.transport.send({ method: "GET", path: `${KG}/views` });
    }
    /**
     * Save a query as a named view. The appliance validates and persists it — a console never edits
     * world YAML itself.
     */
    saveView(spec) {
      return this.transport.send({
        method: "POST",
        path: `${KG}/views`,
        body: spec,
        timeoutMs: TIMEOUTS.saveView
      });
    }
    deleteView(name) {
      return this.transport.send({ method: "DELETE", path: `${KG}/views/${encodeURIComponent(name)}` });
    }
    /**
     * The runnable cypher for a saved view invoked with these arguments. Put it in the editable
     * cypher box and run it through `execute`, so a view goes through the same engine as any other
     * query rather than a private path.
     */
    viewInvocation(name, args = {}) {
      return this.transport.send({
        method: "POST",
        path: `${KG}/views/${encodeURIComponent(name)}/invocation`,
        body: { args }
      });
    }
    /** Force-recompute a materialised view's cache now, ignoring its TTL. */
    refreshView(name) {
      return this.transport.send({
        method: "POST",
        path: `${KG}/views/${encodeURIComponent(name)}/refresh`
      });
    }
  };

  // src/client/index.ts
  var ApplianceClient = class _ApplianceClient {
    constructor(transport) {
      this.transport = transport;
      this.kg = new KgClient(transport);
    }
    kg;
    /** The console's configuration: relative URLs, same origin, ambient credentials. */
    static sameOrigin(config = {}) {
      return new _ApplianceClient(new HttpTransport({ ...config, baseUrl: "" }));
    }
    /** The Me main process's configuration: an explicit appliance URL and its credential. */
    static forAppliance(config) {
      return new _ApplianceClient(new HttpTransport(config));
    }
  };
  return __toCommonJS(index_exports);
})();
