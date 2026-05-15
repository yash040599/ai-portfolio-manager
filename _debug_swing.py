"""Add console.log probes to debug the swing page JS."""
with open('_swing_debug.html', 'r', encoding='utf-8') as f:
    h = f.read()

# Add console.log probes between each script block
import re

def add_probe(match):
    idx = match.start()
    return f'<script>console.log("PROBE: script block starting at HTML offset ~{idx}");</script>\n' + match.group()

h = re.sub(r'<script>', add_probe, h)

# Also add try/catch wrapper around the biggest block
# Find the main _js() block (the one with _swingBanner)
big_start = h.find('console.log("PROBE: script block starting at HTML offset ~93')
if big_start > 0:
    # Find the actual <script> after the probe
    actual_start = h.find('<script>', big_start + 10)
    actual_end = h.find('</script>', actual_start)
    if actual_start > 0 and actual_end > 0:
        inner = h[actual_start+8:actual_end]
        wrapped = f'<script>try {{\n{inner}\nconsole.log("MAIN BLOCK: loaded OK");\n}} catch(e) {{ console.error("MAIN BLOCK ERROR:", e.message, "at line", e.lineNumber); }}</script>'
        h = h[:actual_start] + wrapped + h[actual_end+9:]
        print("Wrapped main block in try/catch")

with open('_swing_debug3.html', 'w', encoding='utf-8') as f:
    f.write(h)
print(f"Saved debug page ({len(h)} chars)")

import os
os.startfile('_swing_debug3.html')
print("Opened in browser — check F12 console")
