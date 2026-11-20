import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "s4ccm", "assets")


def main() -> None:
    img = Image.open(os.path.join(ASSETS, "icon.png")).convert("RGBA")
    if img.size != (256, 256):
        img = img.resize((256, 256), Image.LANCZOS)
    img.save(os.path.join(ASSETS, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon.ico written to", ASSETS)


if __name__ == "__main__":
    main()
