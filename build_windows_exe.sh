#!/usr/bin/env bash
set -euo pipefail

python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt

python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onefile \
  --name PowerTraderAI \
  --collect-all matplotlib \
  --hidden-import matplotlib.backends.backend_tkagg \
  pt_hub.py

echo "Build complete. EXE located at: dist/PowerTraderAI.exe"
