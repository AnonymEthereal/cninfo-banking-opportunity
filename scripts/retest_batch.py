import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.scanner import ScanOrchestrator
from core.storage import atomic_write_json
from core.models import Announcement
from collectors.cninfo import CninfoCollector
from parsers.pdf_parser import parse_pdf
from agents.live import live_agents

def retest(root, source_id):
    collector = CninfoCollector()
    runner = ScanOrchestrator(collector, root)
    source = runner.load(source_id)
    old = root/'results'/source_id
    announcements = json.loads((old/'announcements.json').read_text(encoding='utf-8'))['items']
    original = json.loads((old/'opportunities.json').read_text(encoding='utf-8'))['items']
    failed = {e.announcement_id for e in source.errors if e.announcement_id}
    scan = runner.create(source.companies, source.date_range)
    folder = root/'results'/scan.scan_id
    print('retest', scan.scan_id, flush=True)
    scan.counts.announcements = len(announcements)
    scan.counts.collection_failed = source.counts.collection_failed
    scan.errors = [e for e in source.errors if not e.announcement_id]
    for item in announcements:
        aid = item['announcement_id']
        if aid not in failed:
            atomic_write_json(folder/'agents'/aid/'result.json', {'status':'completed','items':[x for x in original if x['announcement_id']==aid]})
        elif not item.get('text_path'):
            try:
                a = Announcement.parse_obj(item)
                pdf = folder/'documents'/aid/'source.pdf'
                collector.download(a, pdf)
                text, pages = parse_pdf(pdf)
                target = pdf.with_suffix('.txt')
                target.write_text(text,encoding='utf-8')
                item.update(content_path=str(pdf),text_path=str(target))
                atomic_write_json(pdf.with_suffix('.json'), {'pages':pages})
                print('document recovered',aid,flush=True)
            except Exception as exc:
                from core.models import ScanError
                scan.counts.document_failed += 1
                scan.errors.append(ScanError(scope='announcement',announcement_id=aid,code='DOCUMENT_PARSE_FAILED',message='正文复测失败',details={'error_type':type(exc).__name__}))
                print('document failed',aid,type(exc).__name__,flush=True)
    atomic_write_json(folder/'announcements.json', {'items':announcements})
    atomic_write_json(folder/'provenance.json',{'source_scan_id':source_id,'retry_ids':sorted(failed)})
    scan.status='analyzing'
    runner._save(scan)
    client, agents = live_agents()
    try:
        runner.run_agents(scan.scan_id,*agents)
        final = runner.run_export(scan.scan_id)
        rows=[]
        for aid in sorted(failed):
            p=folder/'agents'/aid/'result.json'
            result=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'status':'document_failed'}
            rows.append({'announcement_id':aid,'before':[e.code for e in source.errors if e.announcement_id==aid],'after':result.get('status'),'stage':result.get('stage'),'code':result.get('code')})
        atomic_write_json(folder/'comparison.json',{'source_scan_id':source_id,'scan_id':scan.scan_id,'counts':final.counts.dict(),'items':rows})
        print('final',scan.scan_id,final.counts.dict(),flush=True)
    finally:
        client.close()
        collector.client.close()

if __name__=='__main__':
    retest(Path('data/real_batch_acceptance'),sys.argv[1])
