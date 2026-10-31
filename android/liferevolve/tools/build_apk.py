#!/usr/bin/env python3
"""Gradle 없이 공식 SDK 도구로 시험용 서명 APK를 만듭니다. 운영 주소는 필수입니다."""
from pathlib import Path
import argparse, base64, hashlib, json, os, shutil, subprocess, urllib.parse, zipfile
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--origin',default=os.environ.get('PLATFORM_ORIGIN'))
args=parser.parse_args()
u=urllib.parse.urlsplit(args.origin or '')
if u.scheme!='https' or not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
    parser.error('실제 운영 HTTPS origin을 --origin 또는 PLATFORM_ORIGIN으로 지정하십시오.')
origin=args.origin.rstrip('/')
if not (ROOT/'.android-sdk/installed.json').exists():
    raise SystemExit('먼저 python3 tools/setup_android_sdk.py 를 실행하십시오.')
installed=json.loads((ROOT/'.android-sdk/installed.json').read_text())
android=next(Path(installed['platforms;android-35']).rglob('android.jar'))
platform_tools=next(Path(installed['build-tools;35.0.0']).rglob('aapt2')).parent
build=ROOT/'build';shutil.rmtree(build,ignore_errors=True);build.mkdir()
for n in ['gen','classes','dex','test-classes']:(build/n).mkdir()
java=ROOT/'app/src/main/java';res=ROOT/'app/src/main/res'
# GitHub의 글자 파일 경로로도 동일 이미지 원본을 보존합니다.
asset=ROOT/'assets/companion.png.base64'
if asset.exists():
    raw=base64.b64decode(asset.read_text().strip(),validate=True)
    if hashlib.sha256(raw).hexdigest()!='b162001f66840541fb41692740a977b47d4262e6ba351456fe69c8ccd7c43865':raise SystemExit('캐릭터 이미지 확인값 불일치')
    image=res/'drawable-nodpi/companion.png';image.parent.mkdir(parents=True,exist_ok=True);image.write_bytes(raw)

def run(*cmd):
    cmd=list(map(str,cmd))
    if cmd[0]=='javac' and not shutil.which('javac'):cmd=['java','-m','jdk.compiler/com.sun.tools.javac.Main']+cmd[1:]
    if cmd[0]=='jar' and not shutil.which('jar'):cmd=['java','-m','jdk.jartool/sun.tools.jar.Main']+cmd[1:]
    print('+',*cmd,flush=True);subprocess.run(cmd,check=True,cwd=ROOT)
run('javac','-d',build/'test-classes',java/'kr/edurecover/liferevolve/CapturePolicy.java',java/'kr/edurecover/liferevolve/ImageBuffers.java',ROOT/'tests/PolicyTest.java')
run('java','-cp',build/'test-classes','kr.edurecover.liferevolve.PolicyTest')
config=build/'gen/kr/edurecover/liferevolve/BuildConfig.java';config.parent.mkdir(parents=True,exist_ok=True)
config.write_text('package kr.edurecover.liferevolve; public final class BuildConfig { public static final String PLATFORM_ORIGIN = '+json.dumps(origin)+'; public static final String VERSION_NAME = "0.3.0"; }\n')
run(platform_tools/'aapt2','compile','--dir',res,'-o',build/'resources.zip')
run(platform_tools/'aapt2','link','-o',build/'base.apk','--manifest',ROOT/'app/src/main/AndroidManifest.xml','-I',android,'--java',build/'gen','--min-sdk-version','34','--target-sdk-version','35','--version-code','1','--version-name','0.3.0',build/'resources.zip')
sources=list(java.rglob('*.java'))+list((build/'gen').rglob('*.java'))
run('javac','-source','17','-target','17','-encoding','UTF-8','-classpath',android,'-d',build/'classes',*sources)
run('jar','cf',build/'classes.jar','-C',build/'classes','.')
run(platform_tools/'d8','--release','--lib',android,'--min-api','34','--output',build/'dex',build/'classes.jar')
shutil.copyfile(build/'base.apk',build/'unsigned.apk')
with zipfile.ZipFile(build/'unsigned.apk','a',compression=zipfile.ZIP_DEFLATED) as out:
    for dex in (build/'dex').glob('*.dex'):out.write(dex,dex.name)
run(platform_tools/'zipalign','-f','4',build/'unsigned.apk',build/'aligned.apk')
key=ROOT/'.signing/debug.keystore';key.parent.mkdir(exist_ok=True);key.parent.chmod(0o700)
if not key.exists():
    run('keytool','-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias','androiddebugkey','-keyalg','RSA','-keysize','2048','-validity','10000','-dname','CN=LifeRevolve Test, O=EduRecover, C=KR')
    key.chmod(0o600)
output=ROOT/'LifeRevolve_0_3_0_20261022.apk'
run(platform_tools/'apksigner','sign','--ks',key,'--ks-pass','pass:android','--key-pass','pass:android','--out',output,build/'aligned.apk')
run(platform_tools/'apksigner','verify','--verbose',output)
run(platform_tools/'aapt2','dump','badging',output)
print('APK:',output)
