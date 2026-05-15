"""Find ALL bare < and > inside <script> blocks in the swing page."""
import re
from modes.dashboard.swing_page import render_swing_page

h = render_swing_page()
blocks = list(re.finditer(r'<script>(.*?)</script>', h, re.DOTALL))
print(f"Found {len(blocks)} script blocks\n")

for bi, m in enumerate(blocks):
    content = m.group(1)
    lines = content.split('\n')
    block_start_line = h[:m.start()].count('\n') + 1
    
    for li, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith('//'):
            continue
        # Find bare < that's NOT part of <=, <<, </, or inside a regex
        # The dangerous pattern is '<' inside a JS string literal
        # where the HTML parser sees it as a tag opener
        for ci, ch in enumerate(s):
            if ch == '<':
                # Check context: is this inside a string?
                before = s[:ci]
                after = s[ci+1:ci+5] if ci+1 < len(s) else ''
                # Skip <= and << operators
                if after.startswith('=') or after.startswith('<'):
                    continue
                # Skip closing tags in string concat (</strong> etc) 
                # these are OK because </ is not a valid tag opener to the parser
                if after.startswith('/'):
                    continue
                # This is a bare < that could be interpreted as tag opener
                html_line = block_start_line + li
                print(f"Block {bi}, JS line {li+1}, HTML line ~{html_line}:")
                print(f"  {s[:120]}")
                print(f"  {'':>{ci}}^ bare < here")
                print()

print("Done")
