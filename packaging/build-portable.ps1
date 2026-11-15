$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
python -m PyInstaller --noconfirm --clean --onedir --console --contents-directory . --name HermesFreeModelManager --paths "$root\src" --add-data "$root\src;src" --add-data "$root\docs;docs" --add-data "$root\reports;reports" --hidden-import yaml --hidden-import httpx --distpath "$root\dist" --workpath "$root\build" --specpath "$root" "$root\src\portable_entry.py"
Copy-Item "$root\START-MANAGER.cmd" "$root\dist\HermesFreeModelManager\START-MANAGER.cmd"
Copy-Item "$root\README.md" "$root\dist\HermesFreeModelManager\README.md"
