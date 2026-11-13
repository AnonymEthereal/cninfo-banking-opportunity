# 上市公司商机雷达

当前页面默认使用 Mock Agent。程序会优先读取系统环境变量，其次读取项目根目录 `.env`，最后读取 `.env.example`。生产环境请使用 `.env` 或系统环境变量，不要把真实密钥提交到版本库。

启动页面：

```powershell
python -m streamlit run app.py
```

上传包含 `stock_code`、`company_name` 两列的 XLSX，查看校验结果后启动扫描。结果保存到 `data/`，页面提供 XLSX 和 HTML 下载。

扫描在后台线程执行，运行中的任务结果区每 2 秒自动刷新；同一 `data/` 目录只允许一个任务运行。当前版本按内部单实例设计，没有账号体系，历史任务对所有使用者可见。运行、锁与恢复细节见 `OPERATIONS.md`。

依赖使用 Pydantic 2，已通过仓库内 `.venv`（Python 3.11）安装与验证。
