import os,runpy,sys
from pathlib import Path
import manager_ui  # Explicit import makes PyInstaller collect GUI and transitive dependencies.
root=Path(sys.executable).resolve().parent
os.chdir(root)
sys.path.insert(0,str(root/'src'))
runpy.run_path(str(root/'src'/'manager_ui.py'),run_name='__main__')