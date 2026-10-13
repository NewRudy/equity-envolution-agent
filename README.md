# 股权投资估值预审智能体

一个面向比赛演示的精简单智能体：把被投企业财务数据转成有来源的预审底稿，运行少量可解释校验，并展示 DCF 与 EV/EBITDA 两种方法的敏感性区间。它不是正式资产评估系统，也不替人作出投资决定。

## 初版边界

- 一次处理一家企业；以标准化财务表为估值输入，支持 XLSX、XLSM 和 CSV。
- Excel 保留工作表和单元格来源；CSV 保留行号，便于回到原材料复核。
- PDF 有文本层时用 PyMuPDF 本地提取；扫描件可选接 PaddleOCR 官方托管 API。PDF 识别文本只作为证据，不直接覆盖财务表。
- 单一编排器按文件类型调用解析、校验、估值和底稿导出工具；估值公式是普通 Python 确定性计算，不让语言模型编造数值。
- DCF 使用使用者可编辑的增长率、FCF 率、WACC 和永续增长率；可比法只使用使用者输入的 EV/EBITDA 倍数，不自动抓取或声称验证市场数据。
- 导出 Excel：摘要、假设、财务数据来源、估值情景、异常待办和 PDF 证据。
- 本项目代码不持久化上传文件；运行期间的缓存/容器行为仍受部署环境影响。不要把未脱敏的真实被投企业材料上传到比赛演示环境。
- 缺少关键字段、单位不清或估值假设无效时，智能体提示复核，不应把空值当成零。

第一版刻意不包含多智能体、RAGFlow、Dify、实时行情、可比公司自动检索、投资建议和正式评估报告。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

打开 Streamlit 显示的本机地址。页面默认可用合成科技公司样例演示，也可以下载模板后填入自己的结构化数据。所有金额归一为人民币亿元；列名显式标注亿元、万元、百万元或元时自动换算。未标单位的金额会被标记为高优先级复核事项，在确认并修正前不应采用估值区间。

## PaddleOCR 官方托管 API（可选）

扫描件 PDF 使用官方 Python SDK 调用托管文档解析服务，不在本机或云服务器加载 OCR 大模型。当前默认模型为 `PaddleOCR-VL-1.6`，可用 `PADDLEOCR_MODEL` 切换到 `PP-StructureV3` 等 SDK 支持的文档解析模型。模型和接口以官方文档为准：

- [PaddleOCR 官方 API Python SDK](https://www.paddleocr.ai/main/version3.x/inference_deployment/serving/paddleocr_official_api/python.html)
- [API 配额规则与错误码](https://ai.baidu.com/ai-doc/AISTUDIO/Xmjclapam)
- [文档解析 API 价格和免费测试资源说明](https://ai.baidu.com/ai-doc/OCR/Imtti0n62)

1. 在 PaddleOCR / AI Studio 官方渠道创建访问 Token，并先查看账号当前 API 配额、计费和数据处理条款。
2. 安装可选 SDK：`pip install -r requirements-ocr.txt`。Docker 部署时将 `INSTALL_PADDLEOCR_API=1` 设为构建参数并重新构建。
3. 在运行环境设置 `PADDLEOCR_ACCESS_TOKEN`，不要把 Token 放到代码、仓库、截图或浏览器端。
4. 文本层已可读的 PDF 仅在本机解析；扫描件只有在 Token 已配置、可选 API 客户端已安装、且用户在本次操作明确勾选授权后，才发送到 PaddleOCR 官方托管服务。每个 PDF 超过 1000 页时不发送。

示例：

```bash
export PADDLEOCR_ACCESS_TOKEN='从官方控制台安全取得的 Token'
export PADDLEOCR_MODEL='PaddleOCR-VL-1.6'
pip install -r requirements-ocr.txt
streamlit run app.py
```

官方 SDK 的 Token 与文件会用于调用远端托管服务。请勿把未获授权的企业材料传给第三方服务；本项目没有替你确认 PaddleOCR 账号的当前免费额度、付费边界或数据保留条款。提交前查看[官方配额说明](https://ai.baidu.com/ai-doc/AISTUDIO/Xmjclapam)及[当前价格与测试额度](https://ai.baidu.com/ai-doc/OCR/Imtti0n62)。

## Docker 部署

```bash
docker compose up -d --build
docker compose logs -f equity-agent
```

Compose 默认只把服务绑定到服务器 `127.0.0.1:8501`，不会占用公网端口或覆盖现有网站。你可以通过 SSH 隧道使用：

```bash
ssh -L 8501:127.0.0.1:8501 root@<服务器地址>
```

然后打开 `http://127.0.0.1:8501`。若需公开给评委访问，应先配置 HTTPS 域名、访问口令和反向代理；不要直接把无口令的文件上传应用暴露到公网。设置 `APP_PASSWORD` 可启用页面口令保护。配置 PaddleOCR Token 时，应通过服务器秘密管理方式注入，不要提交 `.env`。

如服务器不适合拉取 Docker 基础镜像，也可使用 `deploy/equity-envolution-agent.service`：在服务器项目目录创建 Python 3.12 虚拟环境并安装 `requirements.txt`，将该 unit 安装到 systemd，再启用服务。该 unit 使用动态非特权用户、只读系统目录和独立状态目录；示例绑定 `172.18.0.1:8501`（Docker 网关），由现有反向代理转发，并将 Streamlit 子路径设为 `equity_envolution`。公开访问前应通过 root-only 的 `/etc/equity-envolution-agent.auth.env` 设置 `APP_PASSWORD`；systemd 从 `/etc/equity-envolution-agent.env` 读取运行时 Token。不要提交 `.env` 或服务器凭据到 Git。

当前服务器由 Caddy 接管公网 80/443，项目路径为 `https://rudy.cc.cd/equity_envolution/`；代理配置需保留该路径并转发到上述 Streamlit 端口。

## 计算口径与人工边界

- 自由现金流：经营活动现金流 - `abs(资本开支)`。
- DCF：按收入增长和预测 FCF 率构造显性期现金流，折现显性期及永续终值，股权价值 = 企业价值 - 净债务。
- EV/EBITDA：最新年度 EBITDA × 使用者录入倍数；股权价值 = 企业价值 - 净债务。
- Bear/Base/Bull 只是对预测收入增长、FCF 率和 WACC 做机械扰动的敏感性情景，不是统计置信区间。
- 内置异常规则是提醒队列而非审计意见。请复核口径、单位、合并范围、一次性损益、营运资金、债务、假设和倍数来源。
- 系统不出具正式资产评估报告、不判断是否投资、不承诺估值结果。
