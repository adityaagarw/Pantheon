"""Draws the app icon (icon.png, 1024 px): a temple of pillars on a dark tile.

Regenerate the platform icons from it with ``npx tauri icon icon/icon.png`` (in apps/desktop).
"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
BG, PANEL, ACCENT, ACCENT2 = "#0b0c10", "#171a22", "#8b7cff", "#6d5dfc"

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=PANEL, outline="#262a36", width=8)

# Pediment (the triangular roof), architrave, five pillars, and the steps.
d.polygon([(S / 2, 200), (840, 380), (184, 380)], fill=ACCENT)
d.rectangle([184, 392, 840, 436], fill=ACCENT)
for i in range(5):
    x = 222 + i * 128
    d.rectangle([x, 460, x + 68, 744], fill=ACCENT2 if i % 2 else ACCENT)
d.rectangle([164, 768, 860, 806], fill=ACCENT)
d.rectangle([134, 822, 890, 860], fill=ACCENT2)

out = Path(__file__).with_name("icon.png")
img.save(out)
print(out, img.size, BG)
