#! python3.12
import sys

try:
    import PySide6
except ImportError:
    print(f"PySide6가 이 Python에 설치되어 있지 않습니다:\n  {sys.executable}\n\n"
          f"다음 중 하나로 해결하세요:\n"
          f"  1) python main.py 로 실행\n"
          f"  2) \"{sys.executable}\" -m pip install -r requirements.txt")
    sys.exit(1)

from s4ccm.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
