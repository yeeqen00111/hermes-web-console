# Hermes-MCP — Conda 环境手册

> 面向本机：Windows 11 · conda 25.5.1（`/e/Andaconda/Anaconda`）· 项目 `hermes-mcp`
> 目的：为本项目的 Python 3.10 依赖建一个独立、可复现、不污染系统 Python 的环境。

---

## 1. 为什么用 conda 独立环境

- 本机系统 Python 是 **3.13.5**，但本项目依赖链（`mcp` SDK、`SQLAlchemy` 2.x、各 DB 驱动）与项目约定（沿用 pytest / py310 风格）更稳的是 **3.10**。独立环境互不干扰。
- 后续要给不同库/不同方言装驱动，装坏也不影响系统 Python。

---

## 2. 先确认 conda 可用

```bash
conda --version
```
> 应在 PATH 中。若 `conda` 找不到，用绝对路径：
> `/e/Andaconda/Anaconda/Scripts/conda --version`

---

## 3. 创建环境（推荐：python 3.10，名字 `hermes-mcp`）

```bash
conda create -n hermes-mcp python=3.10 -y
```

> conda 会在 `C:\Users\66399\Anaconda\envs\hermes-mcp\`（若你的 conda 装在 C 盘）或安装根下 `envs\hermes-mcp\` 建好一套独立 python。

**查看环境**：
```bash
conda env list               # 列出所有环境，确认 hermes-mcp 在列
```

---

## 4. 激活 / 退出环境

```bash
conda activate hermes-mcp    # 进入
conda deactivate             # 退出（回到 base）
```

> 在 Claude Code 会话里，想让你这边的命令也用这个环境，可在输入用 `!` 前缀：
> `! conda activate hermes-mcp && 你的命令`

---

## 5. 安装本项目依赖

先写 `requirements.txt`（本项目已提供，见仓库根部）：
```
mcp>=1.0
SQLAlchemy>=2.0
sqlparse
PyYAML
pydantic>=2
cryptography
httpx
pymysql          # 后接 MySQL 时
psycopg[binary]  # 后接 PostgreSQL 时
pyodbc           # 后接 SQL Server 时（需本机 ODBC 驱动）
oracledb         # 后接 Oracle 时
pytest           # 跑单测
```

安装（在项目根 `hermes-mcp/` 下执行）：
```bash
conda activate hermes-mcp
pip install -r requirements.txt
```

> 只装 M0 基础集（不含 DB 驱动）也可：`pip install mcp SQLAlchemy sqlparse PyYAML pydantic cryptography httpx pytest`。
> 各 DB 驱动在 M1 真正接真库时再装，先不增加安装失败面。

---

## 6. 验证装好了

```bash
conda activate hermes-mcp
python -c "import mcp, sqlalchemy, sqlparse, yaml, pydantic, cryptography, httpx; print('mcp/', __import__('mcp').__version__ if hasattr(__import__('mcp'),'__version__') else 'ok'); print('sqlalchemy', sqlalchemy.__version__)"
```

**务必用对解释器**：在环境里运行 `which python`，应指向 `.../envs/hermes-mcp/python.exe`，**不是**系统 3.13。运行本项目的 mcp-server 统一用激活后的 `python`（即该环境）。

---

## 7. 本环境怎么跑 / 怎么用

激活后直接用 `python` 即可。若某处脚本必须给绝对路径：
- Windows：`C:\Users\66399\Anaconda\envs\hermes-mcp\python.exe`
- 或你的 conda 安装根：`/e/Andaconda/Anaconda/envs/hermes-mcp/python.exe`

（准确路径以 `conda env list` / `conda env list` 里的 env 目录为准。）

---

## 8. 常见问题（踩坑）

| 现象 | 原因 | 处理 |
|---|---|---|
| `python` 还是 3.13 | 没激活对，或在 Git Bash/新终端丢了激活 | 每次新终端先 `conda activate hermes-mcp` |
| conda 命令找不到 | 未进 conda 的 shell 初始化 | 用绝对路径 `…/Anaconda/Scripts/conda …`，或 `conda init bash` 后重开终端 |
| `import mcp` 报错 | 少装/装错解释器 | 确认激活环境 + 在该环境 `pip install -r requirements.txt` |
| SQLAlchemy 连 MySQL 报驱动错 | 忘了装 `pymysql` | `pip install pymysql`，确认 DSN 用 `mysql+pymysql://` |
| pyodbc 装不上 | 缺 MS ODBC Driver | 官网装 ODBC Driver 18/17 后再 `pip install pyodbc` |
| 装坏了想重置 | 环境崩溃 | 见 §9 重建 |

---

## 9. 删除 / 重建环境

```bash
conda deactivate
conda env remove -n hermes-mcp -y   # 整环境删除
conda create -n hermes-mcp python=3.10 -y   # 重建
pip install -r requirements.txt
```

---

## 10. 记住

1. **每次新终端先激活** `conda activate hermes-mcp`。
2. 依赖统一从 `requirements.txt` 装，保持可复现。
3. 本项目**不建 `.venv`**，统一走 conda 环境。
4. `conda env list` 是排查「用错解释器」的入口。