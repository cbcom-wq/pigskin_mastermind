#!/usr/bin/env python3
"""
Generate placeholder icon files for Air Planner builds.
This creates minimal valid icon files for Windows, macOS, and Linux.
"""

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Error: Pillow not installed. Install with: pip install Pillow")
    print("Or manually create icon files (icon.ico, icon.icns, icon.png)")
    exit(1)

import os

# Create assets directory if it doesn't exist
os.makedirs('assets', exist_ok=True)

# Create a simple icon with "AP" (Air Planner)
def create_icon(filename, size):
    """Create a simple icon with gradient background and 'AP' text."""
    img = Image.new('RGBA', (size, size), (70, 130, 180, 255))  # Steel blue
    draw = ImageDraw.Draw(img)
    
    # Draw a circle
    margin = size // 8
    draw.ellipse(
        [(margin, margin), (size - margin, size - margin)],
        fill=(100, 150, 200, 255),
        outline=(255, 255, 255, 255),
        width=2
    )
    
    # Save
    img.save(filename)
    print(f"✓ Created {filename}")

# Create icon files
print("Generating icon files...")
create_icon('assets/icon.png', 512)  # Linux

# For Windows .ico, we need multiple sizes
print("Creating Windows .ico file...")
sizes = [16, 32, 48, 64, 128, 256]
icons = []
for size in sizes:
    img = Image.new('RGBA', (size, size), (70, 130, 180, 255))
    draw = ImageDraw.Draw(img)
    margin = size // 8
    draw.ellipse(
        [(margin, margin), (size - margin, size - margin)],
        fill=(100, 150, 200, 255),
        outline=(255, 255, 255, 255),
        width=max(1, size // 32)
    )
    icons.append(img)

# Save as ICO
icons[0].save('assets/icon.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("✓ Created assets/icon.ico")

# For macOS, we need to create an ICNS (more complex)
# For now, create a larger PNG that can be converted
print("Creating macOS icon template...")
create_icon('assets/icon-macos.png', 512)
print("Note: To create proper .icns, convert icon-macos.png using:")
print("  png2icns assets/icon.icns assets/icon-macos.png")
print("  Or use: https://cloudconvert.com/png-to-icns")

print("\n✓ Icon files created!")
print("Note: The macOS .icns file needs to be generated separately.")
print("Copy icon.png to icon.icns for now, or properly convert with tool above.")

# Create a dummy ICNS by copying PNG (won't display properly but build will proceed)
import shutil
try:
    shutil.copy('assets/icon.png', 'assets/icon.icns')
    print("✓ Created placeholder icon.icns (copy of PNG)")
except:
    print("⚠ Could not create icon.icns placeholder")
