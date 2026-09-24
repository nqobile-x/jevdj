"""Run the backend against a synthetic library, with its own database.

python dev/serve_demo.py             test tones (dev/demo_music, data/dev)
python dev/serve_demo.py originals   JevDJ Originals (dev/originals, data/originals)
Your real MUSIC_DIR and database are untouched.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIGINALS = len(sys.argv) > 1 and sys.argv[1] == "originals"
os.environ["MUSIC_DIR"] = str(HERE / ("originals" if ORIGINALS else "demo_music"))
os.environ["JEVDJ_DATA_DIR"] = str(HERE.parent / "data" / ("originals" if ORIGINALS else "dev"))
os.chdir(HERE.parent)
sys.path.insert(0, str(HERE.parent))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
