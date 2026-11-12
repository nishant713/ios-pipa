path = 'tools/mkadt.py'
text = open(path, 'r', encoding='utf-8').read()

old = "('canvas-width', u32(1080 if native_display else 1125)),\n            ('canvas-height', u32(2400 if native_display else display_height or 2436)),"
if old not in text:
    old = "('canvas-width', u32(1080 if native_display else 1125)),\r\n            ('canvas-height', u32(2400 if native_display else display_height or 2436)),"

assert old in text, "Target string not found!"

new = "('canvas-width', u32(1800)),\n            ('canvas-height', u32(2880)),"
if '\r\n' in old:
    new = "('canvas-width', u32(1800)),\r\n            ('canvas-height', u32(2880)),"

text = text.replace(old, new, 1)
open(path, 'w', encoding='utf-8', newline='').write(text)
print("Canvas size in mkadt.py updated to 1800x2880!")
