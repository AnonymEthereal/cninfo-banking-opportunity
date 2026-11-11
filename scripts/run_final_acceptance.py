import json,sys
from pathlib import Path
sys.path.insert(0,'.')
from openpyxl import load_workbook
from core.company_list import validate_company_file
from core.models import Company,DateRange
from core.calendar import default_date_range
from datetime import date
from core.scanner import ScanOrchestrator
from collectors.cninfo import CninfoCollector
from agents.live import live_agents
p=Path('data/inputs/official_20.xlsx'); v=validate_company_file(p.read_bytes()); companies=[Company.model_validate(x) for x in v['companies']]
rng=DateRange(start_date=date(2025,12,1), end_date=date(2025,12,5)); root=Path('data/final_acceptance'); runner=ScanOrchestrator(CninfoCollector(),root); scan=runner.create(companies,rng); print('SCAN',scan.scan_id,'RANGE',rng.model_dump(),flush=True)
runner.run_collection(scan.scan_id); print('COLLECT',runner.load(scan.scan_id).counts.model_dump(),flush=True)
runner.prepare_analysis(scan.scan_id); print('PARSE',runner.load(scan.scan_id).counts.model_dump(),flush=True)
client,agents=live_agents()
try:
 runner.run_agents(scan.scan_id,*agents,per_announcement_timeout=120,task_timeout=3600); final=runner.run_export(scan.scan_id)
 report=root/'reports'/scan.scan_id
 summary={'scan_id':scan.scan_id,'date_range':rng.model_dump(),'counts':final.counts.model_dump(),'status':str(final.status),'errors':[e.model_dump() for e in final.errors],'xlsx':str(report/'report.xlsx'),'html':str(report/'report.html')}
 (root/'acceptance_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); print(json.dumps(summary,ensure_ascii=False,indent=2,default=str),flush=True)
finally: client.close(); runner.collector.client.close()
