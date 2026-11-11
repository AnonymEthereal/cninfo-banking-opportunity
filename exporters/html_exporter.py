from pathlib import Path
from jinja2 import Environment

TEMPLATE = """<!doctype html><meta charset='utf-8'><title>商机日报</title><style>body{font-family:Arial,sans-serif;margin:2rem;color:#1f2937}header{background:#1f4e78;color:white;padding:1rem 1.5rem;border-radius:8px}.stats{display:flex;gap:1rem;flex-wrap:wrap}.stat{background:#eef4f8;padding:.7rem 1rem;border-radius:6px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d1d5db;padding:.6rem;text-align:left;vertical-align:top}th{background:#1f4e78;color:white}tr:nth-child(even){background:#f8fafc}.muted{color:#6b7280}</style><header><h1>上市公司商机日报</h1><p>扫描：{{ scan.scan_id }}　状态：{{ scan.status }}</p></header><div class='stats'><div class='stat'>公告 {{ scan.counts.announcements }}</div><div class='stat'>商机 {{ scan.counts.opportunities }}</div><div class='stat'>采集失败 {{ scan.counts.collection_failed }}</div><div class='stat'>解析失败 {{ scan.counts.document_failed }}</div><div class='stat'>分析失败 {{ scan.counts.analysis_failed }}</div></div><h2>类别统计</h2><ul>{% for category, count in category_counts.items() %}<li>{{ category }}：{{ count }}</li>{% endfor %}</ul>{% if not opportunities %}<p>本次没有识别到商机。</p>{% else %}<table><thead><tr><th>公司/类别</th><th>事件</th><th>金额</th><th>银行机会</th><th>证据与来源</th></tr></thead><tbody>{% for o in opportunities %}<tr><td><strong>{{ o.company_name }}</strong><br>{{ o.category }}{% if o.subcategory %} / {{ o.subcategory }}{% endif %}</td><td>{{ o.event_summary }}<br><span class='muted'>跟进：{{ o.suggested_action or '待补充' }}</span></td><td>金额：{% if o.amount.value is none %}未披露{% else %}{{ o.amount.value }}{% endif %} {{ o.amount.currency }}<br>{{ o.amount.type or '' }}</td><td>{{ o.bank_products|join(', ') or '待分析' }}<br>{{ o.marketing_direction or '' }}</td><td>{% for e in o.evidence %}{{ e.text }}{% if e.source_page %} [第{{ e.source_page }}页]{% endif %}<br>{% endfor %}<a href='{{ o.source_url }}'>查看原文</a></td></tr>{% endfor %}</tbody></table>{% endif %}{% if scan.errors %}<h2>异常明细</h2><ul>{% for e in scan.errors %}<li>{{ e.code }}：{{ e.message }}</li>{% endfor %}</ul>{% endif %}"""

def export_html(opportunities, scan, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    category_counts = {}
    for opportunity in opportunities:
        category_counts[opportunity.category] = category_counts.get(opportunity.category, 0) + 1
    content = Environment(autoescape=True).from_string(TEMPLATE).render(opportunities=opportunities, scan=scan, category_counts=category_counts)
    content = content.replace("<td>0.0 CNY", "<td>金额：0.0 CNY")
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)
    return path
