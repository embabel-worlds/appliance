"use strict";
var EmbabelCodeSurface = (() => {
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

  // src/code-surface/index.ts
  var index_exports = {};
  __export(index_exports, {
    gatewayPathAt: () => gatewayPathAt,
    membersOf: () => membersOf,
    methodAt: () => methodAt,
    parseSurface: () => parseSurface
  });

  // src/code-surface/surface.ts
  var METHOD = /^\s*(\w+)\((.*)\):\s*(.+);\s*$/;
  var NAMESPACE_OPEN = /^\s*(\w+):\s*\{\s*$/;
  function parseSurface(interfacesTs) {
    const lines = interfacesTs.split("\n");
    const rootName = lines.map((l) => l.match(/^export type GatewayContext = (\w+);/)).find(Boolean)?.[1] ?? "WorldTools";
    const start = lines.findIndex((l) => l.startsWith(`export interface ${rootName} {`));
    if (start < 0) return null;
    const namespaces = [];
    const methods = [];
    let current = null;
    let doc = null;
    let inDoc = false;
    for (const line of lines.slice(start + 1)) {
      const trimmed = line.trim();
      if (inDoc) {
        doc = `${doc} ${trimmed.replace(/^\*\s?/, "").replace(/\*\/$/, "")}`.trim();
        if (trimmed.endsWith("*/")) inDoc = false;
        continue;
      }
      if (trimmed.startsWith("/**")) {
        doc = trimmed.replace(/^\/\*\*\s?/, "").replace(/\s?\*\/$/, "");
        inDoc = !trimmed.endsWith("*/");
        continue;
      }
      let m;
      if ((m = trimmed.match(METHOD)) && m[1]) {
        const method = { name: m[1], signature: trimmed.replace(/;\s*$/, ""), doc };
        (current ? current.methods : methods).push(method);
        doc = null;
        continue;
      }
      if (!current && (m = line.match(NAMESPACE_OPEN)) && m[1]) {
        current = { name: m[1], methods: [] };
        namespaces.push(current);
        doc = null;
        continue;
      }
      if (current && trimmed === "};") {
        current = null;
        continue;
      }
      if (!current && trimmed === "}") break;
      doc = null;
    }
    return { namespaces, methods };
  }
  var byName = (a, b) => a.name.localeCompare(b.name);
  function membersOf(surface, path) {
    if (!surface) return [];
    if (path.length === 0) {
      return [
        ...surface.namespaces.map((n) => ({ name: n.name, kind: "namespace", signature: null, doc: null })),
        ...surface.methods.map((m) => ({ name: m.name, kind: "method", signature: m.signature, doc: m.doc }))
      ].sort(byName);
    }
    if (path.length === 1) {
      const ns = surface.namespaces.find((n) => n.name === path[0]);
      return (ns?.methods ?? []).map((m) => ({ name: m.name, kind: "method", signature: m.signature, doc: m.doc })).sort(byName);
    }
    return [];
  }
  function gatewayPathAt(before) {
    const m = before.match(/\bgateway\.((?:\w+\.)*)(\w*)$/);
    if (!m) return null;
    const path = (m[1] ?? "").split(".").filter(Boolean);
    return { path, stem: m[2] ?? "" };
  }
  function methodAt(surface, path) {
    if (!surface) return null;
    if (path.length === 1) return surface.methods.find((m) => m.name === path[0]) ?? null;
    if (path.length === 2) {
      return surface.namespaces.find((n) => n.name === path[0])?.methods.find((m) => m.name === path[1]) ?? null;
    }
    return null;
  }
  return __toCommonJS(index_exports);
})();
