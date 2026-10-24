"""Run from the host: check, run, or inspect the bounded daily paper update."""
import argparse,json
from .corpus import ROOT
from .update_runner import run_update,requeue_paper,update_status

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['check','run','status','retry'])
    parser.add_argument('--scheduled',action='store_true');parser.add_argument('--paper-id');args=parser.parse_args()
    if args.action=='status':
        print(json.dumps(update_status(),ensure_ascii=False,indent=2));return
    if args.action=='retry':
        if not args.paper_id:parser.error('retry requires --paper-id')
        print(json.dumps(requeue_paper(ROOT,args.paper_id)));return
    report=run_update(execute=args.action=='run',scheduled=args.scheduled)
    # Only controlled status fields reach scheduled-task output; no raw provider diagnostics.
    print('Status:',report['status'],'New titles:',report.get('new_titles',0),'Added to index:',report.get('added_to_index',0))
    if report.get('report_file'):print('Report:',report['report_file'])
    if report['status']=='failed':raise SystemExit(1)
    if report['status']=='retry_limit_reached':raise SystemExit(2)

if __name__=='__main__':main()
