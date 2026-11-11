# 上市公司商机雷达

当前页面默认使用 Mock Agent。程序会优先读取系统环境变量，其次读取项目根目录 `.env`，最后读取 `.env.example`。生产环境请使用 `.env` 或系统环境变量，不要把真实密钥提交到版本库。

启动页面：

```powershell
python -m streamlit run app.py
```

上传包含 `stock_code`、`company_name` 两列的 XLSX，查看校验结果后启动扫描。结果保存到 `data/`，页面提供 XLSX 和 HTML 下载。

当前扫描同步执行，尚未实现后台任务与自动轮询。依赖声明使用 Pydantic 2，但现有模型仍有旧版校验器兼容问题；当前验证环境为 Pydantic 1，不代表全新环境安装已经验证。
