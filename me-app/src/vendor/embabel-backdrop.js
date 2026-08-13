"use strict";
var EmbabelBackdrop = (() => {
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

  // src/backdrop.ts
  var backdrop_exports = {};
  __export(backdrop_exports, {
    startBackdrop: () => startBackdrop
  });
  var INDIGO = [98, 95, 255];
  var VIOLET = [167, 120, 255];
  var GREEN = [62, 207, 142];
  var ICE = [199, 210, 255];
  var PALETTE = [INDIGO, INDIGO, VIOLET, GREEN, ICE];
  var someColour = () => PALETTE[Math.random() * PALETTE.length | 0] ?? INDIGO;
  var LINK = 240;
  function startBackdrop(canvas, options) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return () => {
    };
    const snippets = options.snippets.filter((line2) => line2.trim().length > 0);
    const line = (n) => snippets.length === 0 ? "" : snippets[(n % snippets.length + snippets.length) % snippets.length] ?? "";
    const brightness = options.brightness ?? 1;
    const counts = options.snippetCount ?? { wide: 7, narrow: 4 };
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let nodes = [];
    let snips = [];
    const size = () => {
      const dpr = Math.min(devicePixelRatio, 2);
      canvas.width = innerWidth * dpr;
      canvas.height = innerHeight * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const target = Math.round(innerWidth * innerHeight / 14e3);
      snips = Array.from({ length: innerWidth > 1100 ? counts.wide : counts.narrow }, (_, i) => ({
        text: line(i + Math.floor(Math.random() * snippets.length)),
        x: Math.random() * innerWidth,
        y: Math.random() * innerHeight,
        vx: (Math.random() - 0.5) * 0.1,
        vy: -0.05 - Math.random() * 0.07,
        phase: Math.random() * Math.PI * 2
      }));
      nodes = Array.from({ length: Math.min(Math.max(target, 40), 150) }, () => ({
        x: Math.random() * innerWidth,
        y: Math.random() * innerHeight,
        vx: (Math.random() - 0.5) * 0.22,
        vy: (Math.random() - 0.5) * 0.22,
        r: 1.1 + Math.random() * 2.2,
        hub: Math.random() < 0.16,
        c: someColour()
      }));
    };
    const frame = () => {
      const w = innerWidth;
      const h = innerHeight;
      ctx.clearRect(0, 0, w, h);
      ctx.globalAlpha = brightness;
      for (const n of nodes) {
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < -20) n.x = w + 20;
        if (n.x > w + 20) n.x = -20;
        if (n.y < -20) n.y = h + 20;
        if (n.y > h + 20) n.y = -20;
      }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          if (!a || !b) continue;
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d = Math.hypot(dx, dy);
          if (d > LINK) continue;
          const strength = (1 - d / LINK) ** 2;
          const r = Math.round((a.c[0] + b.c[0]) / 2);
          const g = Math.round((a.c[1] + b.c[1]) / 2);
          const bl = Math.round((a.c[2] + b.c[2]) / 2);
          ctx.strokeStyle = `rgba(${r}, ${g}, ${bl}, ${0.75 * strength})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
      for (const n of nodes) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.hub ? n.r * 1.9 : n.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${n.c[0]}, ${n.c[1]}, ${n.c[2]}, ${n.hub ? 1 : 0.8})`;
        ctx.fill();
        if (n.hub) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r * 5.5, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${n.c[0]}, ${n.c[1]}, ${n.c[2]}, 0.22)`;
          ctx.fill();
        }
      }
      ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
      for (const sn of snips) {
        sn.x += sn.vx;
        sn.y += sn.vy;
        sn.phase += 35e-4;
        if (sn.y < -30) {
          sn.y = h + 30;
          sn.x = Math.random() * w;
          sn.text = line(Math.random() * snippets.length | 0);
        }
        if (sn.x < -320) sn.x = w + 20;
        if (sn.x > w + 320) sn.x = -20;
        const a = 0.16 + 0.14 * Math.sin(sn.phase);
        ctx.fillStyle = `rgba(167, 176, 255, ${Math.max(a, 0)})`;
        ctx.fillText(sn.text, sn.x, sn.y);
      }
      if (!reduced) raf = requestAnimationFrame(frame);
    };
    size();
    addEventListener("resize", size);
    if (reduced) frame();
    else raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      removeEventListener("resize", size);
    };
  }
  return __toCommonJS(backdrop_exports);
})();
