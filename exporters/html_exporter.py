from pathlib import Path
from uuid import uuid4

from jinja2 import Environment

from core.url_safety import safe_https_url


TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>商机日报</title>
<style>
body{font-family:Arial,sans-serif;margin:2rem;color:#1f2937}
header{background:#1f4e78;color:white;padding:1rem 1.5rem;border-radius:8px}
.stats{display:flex;gap:1rem;flex-wrap:wrap}
.stat{background:#eef4f8;padding:.7rem 1rem;border-radius:6px}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #d1d5db;padding:.6rem;text-align:left;vertical-align:top}
th{background:#1f4e78;color:white}
tr:nth-child(even){background:#f8fafc}
.muted{color:#6b7280}
</style>
<header><h1>上市公司商机日报</h1>
<p>扫描：{{ scan.scan_id }}　状态：{{ scan.status }}</p></header>
<div class="stats">
<div class="stat">公告 {{ scan.counts.announcements }}</div>
<div class="stat">商机 {{ scan.counts.opportunities }}</div>
<div class="stat">未形成商机 {{ scan.counts.non_opportunities }}</div>
<div class="stat">采集失败 {{ scan.counts.collection_failed }}</div>
<div class="stat">解析失败 {{ scan.counts.document_failed }}</div>
<div class="stat">分析失败 {{ scan.counts.analysis_failed }}</div>
</div>
<h2>类别统计</h2>
<ul>{% for category, count in category_counts.items() %}
<li>{{ category }}：{{ count }}</li>{% endfor %}</ul>
{% if not opportunities %}
<p>本次没有识别到商机。</p>
{% else %}
<table><thead><tr><th>公司/类别</th><th>事件</th><th>金额</th>
<th>银行机会</th><th>证据与来源</th></tr></thead><tbody>
{% for o in opportunities %}
<tr>
<td><strong>{{ o.company_name }}</strong><br>{{ o.category }}
{% if o.subcategory %} / {{ o.subcategory }}{% endif %}</td>
<td>{{ o.event_summary }}<br><span class="muted">分析：{{ o.analysis_summary or ('暂未识别明确切入点' if o.analysis_outcome == 'no_clear_entry_point' else '待补充') }}</span><br><span class="muted">跟进：{{ o.suggested_action or ('暂无明确跟进动作' if o.analysis_outcome == 'no_clear_entry_point' else '待补充') }}</span></td>
<td>金额：{% if o.amount.value is none %}{% if o.amount.verification_status == 'pending' %}数值待核实{% elif o.amount.undisclosed %}未披露{% else %}未提供{% endif %}{% else %}{{ o.amount.value }} {{ o.amount.currency or '币种未明确' }}{% endif %}
{% if o.amount.type %}<br>{{ o.amount.type }}{% endif %}
{% if o.amount.verification_status == 'pending' and o.amount.value is not none %}<br><span class="muted">金额数值待核实</span>
{% elif o.amount.verification_status == 'verified' %}<br><span class="muted">数值已与原文核对</span>
{% elif o.amount.value is not none %}<br><span class="muted">数值未核验</span>{% endif %}</td>
<td>{% if o.analysis_outcome == 'no_clear_entry_point' %}暂未识别明确切入点{% else %}{{ o.bank_products|join(', ') or '待分析' }}{% endif %}<br>{{ o.marketing_direction or '' }}</td>
<td>{% for e in o.evidence %}{{ e.text }}{% if e.source_page %} [第{{ e.source_page }}页]{% endif %}<br>{% endfor %}
{% set source_url = safe_https_url(o.source_url) %}
{% if source_url %}
<a href="{{ source_url }}" rel="noopener noreferrer">查看原文</a>
{% else %}
<span class="muted">原文链接不可用</span>
{% endif %}
</td></tr>{% endfor %}
</tbody></table>
{% endif %}
{% if non_opportunities %}
<h2>未形成商机的事项</h2>
<p class="muted">以下事项已完成分析，但当前没有明确的银行业务切入点；不计入商机数量。</p>
<table><thead><tr><th>公司/类别</th><th>事项</th><th>分析原因</th><th>来源</th></tr></thead><tbody>
{% for o in non_opportunities %}
<tr>
<td><strong>{{ o.company_name }}</strong><br>{{ o.category }}{% if o.subcategory %} / {{ o.subcategory }}{% endif %}</td>
<td>{{ o.event_summary }}</td>
<td><strong>暂未识别明确切入点</strong><br>{{ o.analysis_summary or '未提供分析说明' }}{% if o.pending_verification %}<br>待核实：{{ o.pending_verification|join('；') }}{% endif %}</td>
<td>{% set source_url = safe_https_url(o.source_url) %}{% if source_url %}<a href="{{ source_url }}" rel="noopener noreferrer">查看原文</a>{% else %}原文链接不可用{% endif %}
{% if o.related_announcement_ids %}<br><span class="muted">可能关联公告：{{ o.related_announcement_ids|join('、') }}</span>{% endif %}</td>
</tr>{% endfor %}
</tbody></table>
{% endif %}
{% if scan.errors %}
<h2>异常明细</h2><ul>
{% for e in scan.errors %}
<li>{{ e.code }}：{{ e.message }}（{{ e.announcement_id or '' }}）</li>
{% endfor %}
</ul>
{% endif %}
</html>"""


def export_html(
    opportunities,
    scan,
    path: Path,
    lease_guard=None,
    non_opportunities=None,
):
    if lease_guard is not None:
        lease_guard.assert_owned()
    path.parent.mkdir(parents=True, exist_ok=True)
    opportunity_items = []
    excluded_items = list(non_opportunities or [])
    for item in opportunities or []:
        if item.analysis_outcome == "no_clear_entry_point":
            excluded_items.append(item)
        else:
            opportunity_items.append(item)
    opportunities = list(
        {item.opportunity_id: item for item in opportunity_items}.values()
    )
    non_opportunities = list(
        {item.opportunity_id: item for item in excluded_items}.values()
    )
    category_counts = {}
    for opportunity in opportunities:
        category_counts[opportunity.category] = (
            category_counts.get(opportunity.category, 0) + 1
        )
    content = Environment(autoescape=True).from_string(TEMPLATE).render(
        opportunities=opportunities,
        scan=scan,
        non_opportunities=non_opportunities,
        category_counts=category_counts,
        safe_https_url=safe_https_url,
    )
    content = content.replace("<td>0.0 CNY", "<td>金额：0.0 CNY")
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        if lease_guard is not None:
            lease_guard.assert_owned()
        temp_path.write_text(content, encoding="utf-8")
        if lease_guard is not None:
            with lease_guard.protect_write():
                temp_path.replace(path)
        else:
            temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return path
