"""Collect upstream license texts for the bundled Windows portable dependencies."""
from pathlib import Path
from importlib import metadata
import json,sys,shutil
root=Path(__file__).resolve().parents[1]
out=root/'THIRD_PARTY_LICENSES'
out.mkdir(exist_ok=True)
names=['PyYAML','httpx','httpcore','anyio','certifi','idna','h11','sniffio','numpy','attrs','click','packaging','setuptools','PyInstaller','pyinstaller-hooks-contrib','altgraph','pefile','pywin32-ctypes','Pillow','charset-normalizer','requests','urllib3','colorama','python-dateutil','six','pydantic','rich']
results=[]
for name in names:
 try:d=metadata.distribution(name)
 except metadata.PackageNotFoundError:
  results.append({'package':name,'error':'not installed'});continue
 candidates=[]
 for f in d.files or []:
  s=str(f).replace('\\','/')
  if ('dist-info/' in s or '/licenses/' in s) and any(x in Path(s).name.lower() for x in ('license','copying','notice')):
   p=Path(d.locate_file(f))
   if p.is_file() and p.stat().st_size < 1_000_000:candidates.append(p)
 target=out/name
 target.mkdir(exist_ok=True)
 copied=[]
 for i,p in enumerate(candidates):
  dest=target/f'{i:02d}_{p.name}'
  shutil.copyfile(p,dest)
  copied.append(str(dest.relative_to(root)))
 results.append({'package':name,'version':d.version,'license_expression':d.metadata.get('License-Expression') or 'see license files','files':copied})
# Python license from installed runtime
p=Path(sys.base_prefix)/'LICENSE.txt'
if p.is_file():
 target=out/'Python';target.mkdir(exist_ok=True)
 shutil.copyfile(p,target/'LICENSE.txt')
 results.append({'package':'Python','version':sys.version.split()[0],'files':['THIRD_PARTY_LICENSES/Python/LICENSE.txt']})
else:results.append({'package':'Python','error':'Python LICENSE.txt not found'})
(root/'THIRD_PARTY_LICENSES'/'inventory.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
for r in results:print(r['package'],r.get('version',''),len(r.get('files',[])),r.get('error',''))
