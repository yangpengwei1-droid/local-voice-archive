"""Run real page JS in a simulated DOM against a fictional temporary HTTP service."""
import os,shutil,subprocess,tempfile,threading
from pathlib import Path
from http.server import HTTPServer
from r3_app import Workbench,handler
from r3_workfile import bind,sha

def main():
 root=Path(__file__).resolve().parents[1];node=os.environ.get('LOCAL_VOICE_ARCHIVE_NODE') or shutil.which('node')
 if not node:raise SystemExit('Node.js 18+ required; set LOCAL_VOICE_ARCHIVE_NODE to its executable')
 with tempfile.TemporaryDirectory(prefix='voice-archive-ui-') as t:
  p=Path(t)/'fictional-copy.xlsx';shutil.copy2(root/'examples/demo.xlsx',p);w=Workbench(Path(t)/'runtime');bind(w,str(p),2030);before=sha(p)
  server=HTTPServer(('127.0.0.1',0),handler(w));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   env=dict(os.environ,LOCAL_VOICE_ARCHIVE_TEST_BASE='http://127.0.0.1:'+str(server.server_port));r=subprocess.run([node,str(root/'tests/ui.cjs')],cwd=root,env=env,timeout=60);assert r.returncode==0,'UI assertions failed';assert sha(p)==before,'Fictional workbook unexpectedly changed'
  finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':main()
