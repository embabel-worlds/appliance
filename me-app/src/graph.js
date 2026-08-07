// The Worlds console's living-graph background, ported to a plain script for
// the sensor app: sparse nodes drift, edges appear between neighbours as they
// pass and fade as they part, so the window sits inside a world that is quietly
// thinking. Canvas because per-frame edge geometry is not a CSS job.
//
// Same restraint as the console: low alpha, no interaction, pointer-events
// none, and it stops entirely under prefers-reduced-motion.
//
// Loaded straight off index.html as a plain browser script, like renderer.js —
// no module system here, and nothing shared with the rest of the app.

;(() => {
  const canvas = document.getElementById('graphbg')
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  // [r, g, b] — indigo, violet, green, ice. Indigo twice: it should still read
  // as an Embabel surface, not a rainbow.
  const PALETTE = [
    [98, 95, 255], [98, 95, 255], [167, 120, 255], [62, 207, 142], [199, 210, 255],
  ]

  // What THIS app actually does, not lorem: the background is the sensor
  // narrating itself, the way the console's is the world talking to itself.
  const SNIPPETS = [
    'lsappinfo front → IntelliJ IDEA',
    'focus: in Google Chrome (theguardian.com)',
    'ioreg HIDIdleTime → 4s',
    'POST /api/v1/sensor/events',
    'plutil -convert xml1 com.apple.dock.plist',
    'now playing: Kind of Blue — Miles Davis',
    'MATCH (p:Proposition) WHERE p.userId = $me',
    'tell application "Spotify" to get player state',
    'Focus mode: work',
    'stored 5 · duplicate 1 · observed 1',
    'SELECT title FROM urls ORDER BY last_visit_time',
    'screen locked → presence: away',
  ]

  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
  const LINK = 240 // px at which two nodes acknowledge each other
  let raf = 0
  // Each node: { x, y, vx, vy, r, hub, c } — `hub` nodes render brighter, like
  // an entity everything references; `c` is an [r, g, b] from the palette.
  let nodes = []
  // Each snippet: { text, x, y, vx, vy, phase } — phase drives the breathing.
  let snips = []

  const size = () => {
    const dpr = Math.min(devicePixelRatio, 2)
    canvas.width = innerWidth * dpr
    canvas.height = innerHeight * dpr
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    // Node count scales with area so a laptop and a monitor feel the same.
    const target = Math.round((innerWidth * innerHeight) / 14000)
    // Sparse on purpose: these are glimpses, not a wall of code.
    snips = Array.from({ length: innerWidth > 1100 ? 6 : 3 }, (_, i) => ({
      text: SNIPPETS[(i + Math.floor(Math.random() * SNIPPETS.length)) % SNIPPETS.length],
      x: Math.random() * innerWidth,
      y: Math.random() * innerHeight,
      vx: (Math.random() - 0.5) * 0.1,
      vy: -0.05 - Math.random() * 0.07,
      phase: Math.random() * Math.PI * 2,
    }))
    nodes = Array.from({ length: Math.min(Math.max(target, 40), 150) }, () => ({
      x: Math.random() * innerWidth,
      y: Math.random() * innerHeight,
      vx: (Math.random() - 0.5) * 0.22,
      vy: (Math.random() - 0.5) * 0.22,
      r: 1.1 + Math.random() * 2.2,
      hub: Math.random() < 0.16,
      c: PALETTE[(Math.random() * PALETTE.length) | 0],
    }))
  }

  const frame = () => {
    const w = innerWidth
    const h = innerHeight
    ctx.clearRect(0, 0, w, h)

    for (const n of nodes) {
      n.x += n.vx
      n.y += n.vy
      if (n.x < -20) n.x = w + 20
      if (n.x > w + 20) n.x = -20
      if (n.y < -20) n.y = h + 20
      if (n.y > h + 20) n.y = -20
    }

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]
        const b = nodes[j]
        const dx = a.x - b.x
        const dy = a.y - b.y
        const d = Math.hypot(dx, dy)
        if (d > LINK) continue
        const strength = (1 - d / LINK) ** 2
        const mix = [0, 1, 2].map((k) => Math.round((a.c[k] + b.c[k]) / 2))
        ctx.strokeStyle = `rgba(${mix[0]}, ${mix[1]}, ${mix[2]}, ${0.55 * strength})`
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(a.x, a.y)
        ctx.lineTo(b.x, b.y)
        ctx.stroke()
      }
    }

    for (const n of nodes) {
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.hub ? n.r * 1.9 : n.r, 0, Math.PI * 2)
      ctx.fillStyle = `rgba(${n.c[0]}, ${n.c[1]}, ${n.c[2]}, ${n.hub ? 0.9 : 0.7})`
      ctx.fill()
      if (n.hub) {
        ctx.beginPath()
        ctx.arc(n.x, n.y, n.r * 5.5, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(${n.c[0]}, ${n.c[1]}, ${n.c[2]}, 0.18)`
        ctx.fill()
      }
    }

    // Fragments drift up through the graph, breathing in and out.
    ctx.font = '12px ui-monospace, SFMono-Regular, Menlo, monospace'
    for (const sn of snips) {
      sn.x += sn.vx
      sn.y += sn.vy
      sn.phase += 0.0035
      if (sn.y < -30) {
        sn.y = h + 30
        sn.x = Math.random() * w
        sn.text = SNIPPETS[(Math.random() * SNIPPETS.length) | 0]
      }
      if (sn.x < -320) sn.x = w + 20
      if (sn.x > w + 320) sn.x = -20
      const a = 0.14 + 0.12 * Math.sin(sn.phase)
      ctx.fillStyle = `rgba(167, 176, 255, ${Math.max(a, 0)})`
      ctx.fillText(sn.text, sn.x, sn.y)
    }

    raf = requestAnimationFrame(frame)
  }

  size()
  addEventListener('resize', size)
  if (reduced) frame() // one static frame — the constellation, not the drift
  else raf = requestAnimationFrame(frame)
  addEventListener('beforeunload', () => cancelAnimationFrame(raf))
})()
