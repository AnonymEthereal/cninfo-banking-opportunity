import csv
from hashlib import sha256
from io import StringIO
import streamlit as st
from .company_import import company_template, preview_company_import
from .company_directory import merge_companies


def render_excel_import(directory):
    st.caption("读取活动工作表，首行为列名。支持中文或英文列名；代码、名称可只填一项。名称冲突需确认，错误行可跳过。")
    st.download_button("下载 Excel 模板", company_template(), file_name="企业名单模板.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    uploaded = st.file_uploader("上传企业名单", type=["xlsx", "xlsm"], help="股票代码 / 公司名称，或 stock_code / company_name。支持大名单，自动按代码去重。")
    if uploaded is None:
        return
    content = uploaded.getvalue()
    digest = sha256(content).hexdigest()
    # Recompute against current directory and selection so preview cannot become stale.
    try:
        preview = preview_company_import(content, directory, st.session_state.selected_companies)
    except ValueError as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error("无法读取文件，请确认是有效且未加密的 Excel 工作簿。")
        return
    rows = preview["rows"]
    if not rows:
        st.warning("工作表中没有可导入的数据。")
        return
    labels = {"valid": "有效", "pending": "待确认", "duplicate": "重复", "error": "错误"}
    st.write(" · ".join(f"{label} {sum(row['status'] == status for row in rows)} 行" for status, label in labels.items()))
    st.dataframe([{"行号": row["row"], "输入代码": row["input_code"], "输入名称": row["input_name"], "状态": labels[row["status"]], "说明": row["message"]} for row in rows], hide_index=True)
    additions = [row["company"] for row in rows if row["status"] == "valid"]
    for row in rows:
        if row["status"] != "pending":
            continue
        candidates = {item["stock_code"]: item for item in row["candidates"]}
        code = st.selectbox(f"第 {row['row']} 行：{row['input_code']} {row['input_name']} · 确认企业", [None] + list(candidates), format_func=lambda value, choices=candidates: "跳过此行" if value is None else f"{choices[value]['company_name']} · {value}", key=f"import_{digest}_{row['row']}")
        if code:
            additions.append(candidates[code])
    issues = [row for row in rows if row["status"] != "valid"]
    if issues:
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["行号", "输入代码", "输入名称", "状态", "说明"])
        for row in issues:
            values = [row["row"], row["input_code"], row["input_name"], labels[row["status"]], row["message"]]
            writer.writerow(["'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value for value in values])
        st.download_button("下载问题明细", output.getvalue().encode("utf-8-sig"), file_name="导入问题明细.csv", mime="text/csv")
    current = st.session_state.selected_companies
    unique = {item["stock_code"]: item for item in additions if item["stock_code"] not in {company["stock_code"] for company in current}}
    st.caption(f"本次可追加 {len(unique)} 家。只添加有效及已确认企业，其余行跳过；已有名单不变。")
    if st.button(f"确认添加 {len(unique)} 家企业", disabled=not unique):
        st.session_state.selected_companies = merge_companies(current, list(unique.values()))
        st.rerun()
