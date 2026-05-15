"""Extract and validate the swing page JS."""
import re
from modes.dashboard.swing_page import render_swing_page

h = render_swing_page()

# Extract ALL script blocks
blocks = re.findall(r'<script>(.*?)</script>', h, re.DOTALL)
print(f"Found {len(blocks)} script blocks")

for i, block in enumerate(blocks):
    lines = block.split('\n')
    print(f"\nBlock {i}: {len(lines)} lines, {len(block)} chars")
    # Show first/last meaningful line
    first = next((l.strip() for l in lines if l.strip()), '?')
    last = next((l.strip() for l in reversed(lines) if l.strip()), '?')
    print(f"  First: {first[:80]}")
    print(f"  Last:  {last[:80]}")
    
    # Check brace balance
    depth = 0
    for li, line in enumerate(lines, 1):
        for ch in line:
            if ch == '{': depth += 1
            elif ch == '}': depth -= 1
        if depth < 0:
            print(f"  BRACE ERROR at line {li}: depth went to {depth}")
            print(f"  Line: {line.strip()[:100]}")
            break
    else:
        if depth != 0:
            print(f"  BRACE MISMATCH: final depth = {depth}")
        else:
            print(f"  Braces: OK")
    
    # Check for common JS killers
    # 1. Unescaped backtick or template literal in non-template string
    for li, line in enumerate(lines, 1):
        s = line.strip()
        # Check for Python f-string artifacts that leaked into JS
        if '{html.escape' in s or '{cand.' in s or '{a.' in s:
            print(f"  PYTHON LEAK at line {li}: {s[:100]}")
        # Check for unescaped single quotes inside single-quoted strings
        # (very rough - just flag suspicious patterns)

    # Check for unclosed parentheses
    paren_depth = 0
    for li, line in enumerate(lines, 1):
        for ch in line:
            if ch == '(': paren_depth += 1
            elif ch == ')': paren_depth -= 1
        if paren_depth < 0:
            print(f"  PAREN ERROR at line {li}: {line.strip()[:100]}")
            break
    else:
        if paren_depth != 0:
            print(f"  PAREN MISMATCH: final depth = {paren_depth}")

print("\n--- Checking full page encode ---")
try:
    h.encode('utf-8')
    print("UTF-8: OK")
except Exception as e:
    print(f"UTF-8 ERROR: {e}")

# Write the biggest block to a file for manual inspection
biggest = max(blocks, key=len)
with open('_biggest_js.js', 'w', encoding='utf-8') as f:
    f.write(biggest)
print(f"\nWrote biggest block ({len(biggest)} chars) to _biggest_js.js")
