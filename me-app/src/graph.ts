// The living-graph background, as this window's share of it.
//
// The animation itself is the kit's — one copy, drawn the same way behind the
// Worlds console and behind this app. What is left here is what is genuinely
// Me's: what the fragments say, and how loud it is.

/* global window, document */

import { startBackdrop } from '@embabel/appliance-kit/backdrop'

;(() => {
  const canvas = document.getElementById('graphbg')
  if (!canvas) return

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

  startBackdrop(canvas as HTMLCanvasElement, {
    snippets: SNIPPETS,
    // Quieter than the console's reference weight. The console is a control room
    // where the backdrop is most of what an empty screen shows; this window is a
    // dense panel of cards and checkboxes sitting right on top of it, and the
    // graph should be noticed on purpose rather than competing with them.
    brightness: 0.55,
    // Fewer fragments too, for the same reason: a smaller window at the same
    // density reads as busy rather than as alive.
    snippetCount: { wide: 6, narrow: 3 },
  })
})()
