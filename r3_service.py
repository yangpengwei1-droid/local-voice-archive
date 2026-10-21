"""Start the existing localhost backend independently of a terminal/tool session.

No import, activation or numbering operation. No login/startup installation.
"""
import fcntl,json,os,socket,subprocess,sys,time,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RUNTIME=ROOT/'var/runtime'
BUILD='local-voice-archive-0.1.0-alpha'

def status():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8877/api/status',timeout=1) as r:return json.load(r)
    except Exception:return None

def ensure():
    RUNTIME.mkdir(parents=True,exist_ok=True)
    with (RUNTIME/'startup.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        current=status()
        if current:
            if current.get('build')!=BUILD:raise RuntimeError('8877 belongs to another build; not starting a duplicate')
            return dict(reused=True,build=current['build'],version=current['version'],count=current['count'])
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',8877))==0:raise RuntimeError('8877 is occupied but unhealthy; not starting a duplicate')
        logs=RUNTIME/'service_logs';logs.mkdir(exist_ok=True)
        with (logs/'stdout.log').open('ab') as stdout,(logs/'stderr.log').open('ab') as stderr:
            process=subprocess.Popen([sys.executable,'-u',str(ROOT/'r3_app.py')],cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True,close_fds=True)
        meta=dict(pid=process.pid,started_unix=time.time(),detached_session=True,stdout=str(logs/'stdout.log'),stderr=str(logs/'stderr.log'))
        (RUNTIME/'service.json').write_text(json.dumps(meta,indent=2))
        for _ in range(60):
            current=status()
            if current and current.get('build')==BUILD:return dict(**meta,reused=False,build=current['build'],version=current['version'],count=current['count'])
            if process.poll() is not None:raise RuntimeError('Backend exited; inspect '+str(logs/'stderr.log'))
            time.sleep(.2)
        raise RuntimeError('Backend did not become healthy; inspect '+str(logs/'stderr.log'))

if __name__=='__main__':
    if sys.argv[1:]!=['ensure']:raise SystemExit('Usage: r3_service.py ensure')
    print(json.dumps(ensure(),ensure_ascii=False))
