"""Run the backend against the synthetic demo library, with its own database in data/dev.

python dev/serve_demo.py   (your real MUSIC_DIR and database are untouched)
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ["MUSIC_DIR"] = str(HERE / "demo_music")
os.environ["JEVDJ_DATA_DIR"] = str(HERE.parent / "data" / "dev")
os.chdir(HERE.parent)
sys.path.insert(0, str(HERE.parent))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
