"""Explicit command-line binding for a browser-only session; use a working copy."""
import argparse,datetime
from zoneinfo import ZoneInfo
from r3_app import Workbench
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('working_copy');a=p.parse_args();w=Workbench();d=w.action('bind_workfile',dict(version=w.status()['version'],path=a.working_copy,year=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).year));print(d['message'])
