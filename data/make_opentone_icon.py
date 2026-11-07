import math
cx, cy, r, w = 64, 66, 39, 10
cols = ["#7f93b8","#26a69a","#f0883e","#e5534b","#b17c55","#4caf50","#a371f7","#4a8ff0","#2fb9d0"]
start, sweep = 135.0, 270.0             # degrees, 0 = +x, clockwise (SVG y down)
gap = 4.0; seg = (sweep - gap * (len(cols) - 1)) / len(cols)
def pt(a, rad): return cx + rad * math.cos(math.radians(a)), cy + rad * math.sin(math.radians(a))
arcs = []
for i, c in enumerate(cols):
    a0 = start + i * (seg + gap); a1 = a0 + seg
    x0, y0 = pt(a0, r); x1, y1 = pt(a1, r)
    arcs.append(f'<path d="M{x0:.2f},{y0:.2f} A{r},{r} 0 0 1 {x1:.2f},{y1:.2f}" stroke="{c}" stroke-width="{w}" stroke-linecap="round" fill="none"/>')
pa = start + sweep * 0.72                 # pointer angle
px, py = pt(pa, 15); qx, qy = pt(pa, 5)
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="plate" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3a3f48"/><stop offset="1" stop-color="#1c1f24"/>
    </linearGradient>
    <radialGradient id="knob" cx="0.38" cy="0.32" r="0.8">
      <stop offset="0" stop-color="#4a505a"/><stop offset="0.6" stop-color="#22262c"/><stop offset="1" stop-color="#111316"/>
    </radialGradient>
    <radialGradient id="cap" cx="0.4" cy="0.35" r="0.75">
      <stop offset="0" stop-color="#2c3037"/><stop offset="1" stop-color="#15171b"/>
    </radialGradient>
    <radialGradient id="led" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#fff2c4"/><stop offset="0.35" stop-color="#ffb02e"/><stop offset="1" stop-color="#ffb02e" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect x="8" y="12" width="112" height="108" rx="22" fill="#0e1013"/>
  <rect x="8" y="8" width="112" height="108" rx="22" fill="url(#plate)"/>
  <rect x="8.75" y="8.75" width="110.5" height="106.5" rx="21.25" fill="none" stroke="#ffffff" stroke-opacity="0.08" stroke-width="1.5"/>
  {"".join(arcs)}
  <circle cx="{cx}" cy="{cy+2}" r="25" fill="#000" opacity="0.35"/>
  <circle cx="{cx}" cy="{cy}" r="25" fill="url(#knob)" stroke="#6b727e" stroke-opacity="0.55" stroke-width="1.5"/>
  <circle cx="{cx}" cy="{cy}" r="19" fill="url(#cap)" stroke="#000" stroke-opacity="0.4" stroke-width="1"/>
  <line x1="{qx:.2f}" y1="{qy:.2f}" x2="{px:.2f}" y2="{py:.2f}" stroke="#f4f4f4" stroke-width="4" stroke-linecap="round"/>
  <circle cx="101" cy="25" r="10" fill="url(#led)"/>
  <circle cx="101" cy="25" r="3.2" fill="#fff6d8"/>
</svg>
'''
open(__import__("os").path.join(__import__("os").path.dirname(__file__), "io.github.barni.OpenTone.svg"), "w").write(svg)
