#!/usr/bin/env python3
"""Google 공식 배포 목록과 체크섬을 사용하여 최소 Android 빌드 도구를 받습니다."""
from pathlib import Path
import concurrent.futures, hashlib, json, urllib.request, zipfile, xml.etree.ElementTree as ET
BASE='https://dl.google.com/android/repository/'
ROOT=Path(__file__).resolve().parents[1]/'.android-sdk'
ROOT.mkdir(exist_ok=True)
data=urllib.request.urlopen(BASE+'repository2-1.xml',timeout=45).read()
root=ET.fromstring(data)
packages={e.attrib['path']:e for e in root if e.tag.endswith('remotePackage')}
def name(e):return e.tag.rsplit('}',1)[-1]
def child(e,key):return next(x for x in e if name(x)==key)
def install(key):
 p=packages[key]
 archives=child(p,'archives')
 for a in archives:
  host=next((x.text for x in a if name(x)=='host-os'),None)
  if host not in (None,'linux'):continue
  comp=child(a,'complete'); u=child(comp,'url').text; check=child(comp,'checksum'); typ=check.attrib.get('type','sha1')
  dest=ROOT/u
  if not dest.exists() or hashlib.new(typ,dest.read_bytes()).hexdigest()!=check.text:
   print('download',key,u,flush=True)
   with urllib.request.urlopen(BASE+u,timeout=120) as response, open(dest,'wb') as f:
    while block:=response.read(1048576):f.write(block)
  if hashlib.new(typ,dest.read_bytes()).hexdigest()!=check.text:raise RuntimeError('checksum mismatch '+key)
  target=ROOT/key.replace(';','-')
  if not target.exists():
   target.mkdir()
   with zipfile.ZipFile(dest) as z:z.extractall(target)
  return str(target)
 raise RuntimeError('linux archive missing '+key)
keys=['platforms;android-35','build-tools;35.0.0']
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=dict(zip(keys,pool.map(install,keys)))
(ROOT/'installed.json').write_text(json.dumps(results,indent=2))
for p in ROOT.rglob('*'):
 if p.is_file() and (p.name in {'aapt2','zipalign','apksigner','d8','aapt'} or p.suffix=='.so'):p.chmod(0o755)
print(json.dumps(results),flush=True)
