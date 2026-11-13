#!/usr/bin/env python3
"""准备 clew-maker 需要的 Python 环境，打印之后要用的 Python 路径。

用法：
    python3 scripts/setup.py          （Windows 上是 py scripts/setup.py 或 python scripts/setup.py）

在 ~/.clew-maker/venv 建一个独立的虚拟环境，装上 Pillow（处理图片）、pillow-heif（写 HEIC）
和 opencv-python-headless（找脸裁头像）。不碰系统自带的 Python，装过的再运行会直接跳过。
最后一行输出是虚拟环境里的 Python，后面的脚本都用它运行。
"""

import os
import subprocess
import sys
import venv
from pathlib import Path

# OpenCV 5 把 Haar 找脸挪出了主包，先固定在 4.x。
PACKAGES = ["pillow", "pillow-heif", "opencv-python-headless<5", "numpy"]
HOME = Path(os.environ.get("CLEW_MAKER_HOME", Path.home() / ".clew-maker"))
VENV = HOME / "venv"


def venv_python():
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ready(python):
    check = "import PIL, pillow_heif, numpy, cv2; cv2.CascadeClassifier"
    return python.exists() and subprocess.run([str(python), "-c", check], capture_output=True).returncode == 0


def main():
    if sys.version_info < (3, 9):
        sys.exit(f"✗ 需要 Python 3.9 或更新的版本，现在是 {sys.version.split()[0]}")
    python = venv_python()
    if ready(python):
        print("✓ 环境已经装好")
        print(python)
        return
    if not python.exists():
        print(f"… 在 {VENV} 建虚拟环境")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    print(f"… 安装 {'、'.join(PACKAGES)}（第一次需要几分钟）")
    result = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-U", *PACKAGES])
    if result.returncode != 0 or not ready(python):
        sys.exit("✗ 安装失败。检查网络后重试；国内网络可以加镜像：\n"
                 f"  {python} -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple {' '.join(PACKAGES)}")
    print("✓ 环境已经装好")
    print(python)


if __name__ == "__main__":
    main()
