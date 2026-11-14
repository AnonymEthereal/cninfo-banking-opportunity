"""Offline metadata snapshot used by the "filter by category" company picker.

The snapshot is produced by ``scripts/refresh_company_meta.py`` from Eastmoney's
public company profile report and committed to ``resources/company_meta.json``.
Keeping it in the repository makes the picker fast, deterministic in tests and
usable without network access; refresh it deliberately instead of fetching on
every page load.

Business rules encoded here:

* Only A shares listed on SSE/SZSE/BSE are kept. B shares, CDRs, NEEQ (新三板)
  and delisted OTC (老三板) names are excluded, and pseudo codes such as
  ``A04004`` (pre-IPO placeholders) are dropped by requiring six digits.
* 上市板块 is derived from the stock code prefix, because Eastmoney reports
  "风险警示板" as its own board and would otherwise lose the real board.
* 监管状态 is ``风险警示`` when the security type says so or the short name
  carries an ST marker, otherwise ``正常``.
* 城市 is parsed from the registered address; direct-controlled municipalities
  fall back to the province name, and unparsable addresses stay empty.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "resources" / "company_meta.json"
FIELDS = ("stock_code", "company_name", "board", "industry", "province", "city", "status")
STATUS_NORMAL = "正常"
STATUS_RISK = "风险警示"
MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}

_BOARD_BY_PREFIX = (
    (("600", "601", "603", "605"), "上交所主板"),
    (("688", "689"), "上交所科创板"),
    (("000", "001", "002", "003", "004"), "深交所主板"),
    (("300", "301"), "创业板"),
    (("43", "83", "87", "88", "92"), "北交所"),
)
_CITY_MARK = "市"
_ADDRESS_CUTS = ("特别行政区", "自治区", "自治州", "自治县", "省", "区")
_PROVINCE_NAMES = (
    "黑龙江", "内蒙古", "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东",
    "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾", "广西", "西藏",
    "宁夏", "新疆", "香港", "澳门",
)
# Leading words that describe a zone rather than a place, stripped before the
# city name is read ("自由贸易试验区成都高新区…" -> "成都高新区…").
_NOISE_PREFIXES = (
    "自由贸易试验区", "自贸试验区", "自贸区", "经济技术开发区", "高新技术产业开发区",
    "高新开发区", "经济开发区", "工业园区", "科技园区", "开发区", "保税区", "试验区",
    "实验区", "新区",
)
# Where a place name stops in an address that never spells out "xx市".
_BOUNDARY_TOKENS = (
    "经济技术", "高新技术", "高新区", "自治州", "地区", "经济", "高新", "开发", "工业",
    "科技", "产业园", "保税", "临港", "金融", "商务", "中央", "国际", "旅游", "县城",
    "街道", "镇", "路", "街", "号", "区", "县", "新区", "新城", "示范区", "盟", "旗",
)
_REJECTED_CITY_SUFFIXES = ("县", "区", "镇", "路", "街", "号", "省", "市")
_ETHNIC_SUFFIXES = (
    "回族自治", "蒙古族自治", "藏族自治", "维吾尔自治", "哈萨克自治", "壮族", "回族",
    "蒙古族", "藏族", "维吾尔族", "哈萨克族", "布依族", "苗族", "土家族", "彝族",
    "侗族", "瑶族", "白族", "傣族", "傈僳族", "哈尼族", "朝鲜族", "满族", "自治",
)
_ST_MARKERS = ("ST", "*ST", "SST", "S*ST", "PT")
_EXCLUDED_TYPE_TOKENS = ("B股", "存托", "三板")
# Older Eastmoney/CSRC labels still present on a few hundred rows. Folding them
# into the current level-1 names avoids near-duplicate filter options such as
# "电子" vs "电子设备" that would silently split a selection.
_INDUSTRY_ALIASES = {
    "电子设备": "电子",
    "电气设备": "电力设备",
    "建筑": "建筑装饰",
    "化石能源": "石油石化",
    "交运设备": "机械设备",
    "金融": "非银金融",
    "文化传媒": "传媒",
    "纺织服装": "纺织服饰",
    "家电": "家用电器",
    "建材": "建筑材料",
    "国防与装备": "国防军工",
    "休闲、生活及专业服务": "社会服务",
    "信息技术": "计算机",
}


class MetaSnapshotError(RuntimeError):
    """Raised when the committed snapshot is missing or malformed."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class CompanyMeta:
    stock_code: str
    company_name: str
    board: str
    industry: str
    province: str
    city: str
    status: str

    def as_company(self) -> dict:
        return {"stock_code": self.stock_code, "company_name": self.company_name}


def board_for(stock_code: str, security_type: str = "") -> str:
    """Map a stock code (falling back to the reported type) to a listing board."""
    for prefixes, label in _BOARD_BY_PREFIX:
        if stock_code.startswith(prefixes):
            return label
    if "科创板" in security_type:
        return "上交所科创板"
    if "创业板" in security_type:
        return "创业板"
    if "北京证券交易所" in security_type:
        return "北交所"
    if "上海证券交易所" in security_type:
        return "上交所主板"
    if "深圳证券交易所" in security_type:
        return "深交所主板"
    return "其他"


def status_for(company_name: str, security_type: str = "") -> str:
    if "风险警示" in security_type or "退市" in security_type:
        return STATUS_RISK
    name = (company_name or "").strip().upper()
    if name.startswith(_ST_MARKERS) or "退市" in company_name or name.endswith("退"):
        return STATUS_RISK
    return STATUS_NORMAL


def _strip_admin_prefix(text: str) -> str:
    """Drop country, province and zone wording in front of the actual place."""
    text = re.sub(r"[（(][^）)]*[）)]", "", text).strip()
    for prefix in ("中华人民共和国", "中国"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    for name in sorted(_PROVINCE_NAMES, key=len, reverse=True):
        if text.startswith(name) and len(text) > len(name):
            text = text[len(name):]
            break
    for marker in ("特别行政区", "自治区", "省", "市"):
        if text.startswith(marker):
            text = text[len(marker):]
            break
    while True:
        for prefix in _NOISE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        else:
            return text


def _trim_locality(name: str) -> str:
    """Drop ethnic-autonomy wording left at the end of a prefecture name."""
    changed = True
    while changed and name:
        changed = False
        for suffix in _ETHNIC_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[: -len(suffix)]
                changed = True
                break
    return name


def _city_before_mark(text: str) -> str:
    """City name in front of the first ``市`` (e.g. "吉林省长春市..." -> 长春)."""
    text = re.split(r"[、，,;；/]", text, maxsplit=1)[0]
    index = text.find(_CITY_MARK)
    if index <= 1:
        return ""
    head = text[:index]
    cut_at, cut_len = -1, 0
    for token in _ADDRESS_CUTS:
        position = head.rfind(token)
        if position > cut_at or (position == cut_at and position >= 0 and len(token) > cut_len):
            cut_at, cut_len = position, len(token)
    # "昌吉州昌吉市" abbreviates an autonomous prefecture; "广州" must survive.
    zhou = head.rfind("州")
    if zhou >= 0 and zhou < len(head) - 1 and zhou > cut_at:
        cut_at, cut_len = zhou, 1
    if cut_at >= 0:
        head = head[cut_at + cut_len:]
    for prefix in ("中华人民共和国", "中国"):
        if head.startswith(prefix):
            head = head[len(prefix):]
    for name in sorted(_PROVINCE_NAMES, key=len, reverse=True):
        if head.startswith(name) and len(head) > len(name):
            head = head[len(name):]
            break
    head = re.sub(r"[^\u4e00-\u9fa5]", "", head)
    if 2 <= len(head) <= 4 and not any(token in head for token in ("省", "自治区", "自治州")):
        return head
    return ""


def _leading_locality(text: str) -> str:
    """Place name for addresses that never spell out ``xx市``.

    "江苏省徐州经济技术开发区…" -> 徐州; "珠海横琴新区…" -> 珠海横琴, which the
    caller trims against the gazetteer.
    """
    text = _strip_admin_prefix(text)
    match = re.match(r"[\u4e00-\u9fa5]+", text)
    if not match:
        return ""
    head = match.group(0)
    cut = min(
        (head.find(token) for token in _BOUNDARY_TOKENS if head.find(token) >= 2),
        default=-1,
    )
    if cut > 0:
        head = head[:cut]
    head = _trim_locality(head)
    if 2 <= len(head) <= 4 and not head.endswith(_REJECTED_CITY_SUFFIXES):
        return head
    return ""


def build_city_gazetteer(rows: list[dict]) -> dict[str, str]:
    """Derive city -> province pairs from addresses that spell out ``xx市``.

    Many registered addresses skip the 市 suffix ("沈阳经济技术开发区…"), so a
    second pass can recognise those cities using the names seen elsewhere in the
    same dataset. No external gazetteer file is needed.
    """
    gazetteer: dict[str, str] = {}
    for row in rows:
        province = str(row.get("PROVINCE") or "").strip()
        city = _city_before_mark(str(row.get("REG_ADDRESS") or ""))
        if city and province:
            gazetteer.setdefault(city, province)
    return gazetteer


def city_for(province: str, address: str, gazetteer: dict[str, str] | None = None) -> str:
    """Best-effort city name from a registered address, without the 市 suffix."""
    province = (province or "").strip()
    text = (address or "").strip()
    city = _city_before_mark(text)
    if city:
        return province if city in MUNICIPALITIES else city
    if province in MUNICIPALITIES:
        return province
    known = {name: owner for name, owner in (gazetteer or {}).items() if owner == province}
    candidate = _leading_locality(text)
    if candidate:
        # "珠海横琴" -> 珠海: trim the zone suffix using a city seen elsewhere.
        prefixes = [name for name in known if candidate.startswith(name)]
        if prefixes:
            return max(prefixes, key=len)
        if 2 <= len(candidate) <= 4 and candidate != province:
            return candidate
    if known:
        hits = [(text.find(name), name) for name in known if name in text]
        if hits:
            # Prefer the longest name, then the leftmost, so a street such as
            # "中山东路" does not beat "石家庄" in the same address.
            hits.sort(key=lambda pair: (-len(pair[1]), pair[0]))
            return hits[0][1]
    return ""


def is_a_share(row: dict) -> bool:
    """True for A shares on SSE/SZSE/BSE, using Eastmoney report fields."""
    code = str(row.get("SECURITY_CODE") or "").strip()
    security_type = str(row.get("SECURITY_TYPE") or "")
    if not re.fullmatch(r"\d{6}", code):
        return False
    if "A股" not in security_type:
        return False
    return not any(token in security_type for token in _EXCLUDED_TYPE_TOKENS)


def industry_for(board_name_level: str, csrc: str) -> str:
    """Prefer Eastmoney's level-1 industry, falling back to the CSRC category."""
    for raw in (board_name_level, csrc):
        head = (raw or "").split("-")[0].strip()
        if head:
            return _INDUSTRY_ALIASES.get(head, head)
    return "未分类"


def build_record(row: dict, gazetteer: dict[str, str] | None = None) -> CompanyMeta:
    code = str(row["SECURITY_CODE"]).strip()
    name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
    security_type = str(row.get("SECURITY_TYPE") or "")
    province = str(row.get("PROVINCE") or "").strip()
    return CompanyMeta(
        stock_code=code,
        company_name=name,
        board=board_for(code, security_type),
        industry=industry_for(row.get("BOARD_NAME_LEVEL"), row.get("INDUSTRYCSRC1")),
        province=province,
        city=city_for(province, row.get("REG_ADDRESS"), gazetteer),
        status=status_for(name, security_type),
    )


def load_snapshot(path: Path | None = None) -> list[CompanyMeta]:
    """Read the committed snapshot, raising ``MetaSnapshotError`` on bad data."""
    target = Path(path) if path is not None else SNAPSHOT_PATH
    if not target.exists():
        raise MetaSnapshotError(
            "META_SNAPSHOT_MISSING",
            "分类元数据快照不存在，请运行 python scripts/refresh_company_meta.py 生成。",
        )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetaSnapshotError("META_SNAPSHOT_UNREADABLE", "分类元数据快照无法读取。") from exc
    fields = payload.get("fields") or []
    if tuple(fields) != FIELDS:
        raise MetaSnapshotError(
            "META_SNAPSHOT_SCHEMA",
            "分类元数据快照字段与当前程序不一致，请重新运行刷新脚本。",
        )
    items = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, list) or len(raw) != len(FIELDS):
            raise MetaSnapshotError("META_SNAPSHOT_SCHEMA", "分类元数据快照存在结构错误的记录。")
        items.append(CompanyMeta(*(str(value) for value in raw)))
    return items


def filter_companies(
    items: list[CompanyMeta],
    *,
    boards: tuple[str, ...] | list[str] = (),
    provinces: tuple[str, ...] | list[str] = (),
    cities: tuple[str, ...] | list[str] = (),
    industries: tuple[str, ...] | list[str] = (),
    statuses: tuple[str, ...] | list[str] = (),
    query: str = "",
) -> list[CompanyMeta]:
    """Apply the picker filters; an empty collection means "no constraint"."""
    board_set, province_set, city_set = set(boards), set(provinces), set(cities)
    industry_set, status_set = set(industries), set(statuses)
    keyword = re.sub(r"\s+", "", query).casefold()
    result = []
    for item in items:
        if board_set and item.board not in board_set:
            continue
        if province_set and item.province not in province_set:
            continue
        if city_set and item.city not in city_set:
            continue
        if industry_set and item.industry not in industry_set:
            continue
        if status_set and item.status not in status_set:
            continue
        if keyword:
            haystack = f"{item.company_name}{item.stock_code}".casefold()
            if keyword not in haystack:
                continue
        result.append(item)
    return result


def city_options(items: list[CompanyMeta], provinces: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Cities matching the selected provinces, used for cascading the filter."""
    chosen = set(provinces)
    return sorted({item.city for item in items if item.city and (not chosen or item.province in chosen)})


def option_counts(items: list[CompanyMeta], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = getattr(item, field, "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))
