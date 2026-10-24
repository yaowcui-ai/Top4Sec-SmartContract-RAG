# 验证记录

2026-10-24，本机独立构建与验证。以下是工程验收，**不是系统化 RAG 准确率评测，也没有证明 mix 优于 naive**。

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| Python 后端 | 58 passed | 目录、PDF、切片、KG、认证、权限、幂等、配置、首次克隆及增量/回滚/限次 |
| 临时浏览器场景 | 20 checks passed | 合成数据与临时 SQLite，不调用模型，见 browser.json |
| 真四会议召回 | 4/4 取得目标论文片段 | 原始探测结果本地留档；脱敏统计见 runtime.json |
| 真答案生成 | naive / mix 各 1 条完成 | 引用 ID、论文、页码范围、PDF 返回、历史与同键复用通过 |
| 真页面 | 通过 | 登录、已有答案、引用展开、历史定位、刷新与退出，不额外提问 |
| 启停与持久化 | 通过 | PowerShell 后端、全 Docker、全部停止再启动、读取同一已保存结果，见 lifecycle.json |
| 数据库备份 | 通过 | 新数据库独立导出，UTF-8 可解码且含实际问答；私有 SQL 不公开 |
| GitHub Linux CI | 通过 | [backend-tests](https://github.com/yaowcui-ai/Top4Sec-SmartContract-RAG/actions/workflows/tests.yml) |
| 公开仓库重新克隆 | 5 passed | 首次配置与缺失资料回归；没有本机索引时显示 0 篇可问答 |

定时更新新增验证：已注册当前用户的每日09:00任务，并真实触发一次，结果no_change、LastTaskResult=0，无模型调用或停机。固定LightRAG SDK在隔离副本中复用126篇旧论文，0篇重新导入、0次embedding flush，4349个片段保持不变，测试副本未发布；见 [update_sdk.json](update_sdk.json)。新增论文、跨轮次全文去重、相对链接跨提交限次、候选构建失败、发布后启动失败、进程中断恢复等由临时数据覆盖。当前源站没有新增，不把合成新增场景冒充真实新论文入库。

## 索引验收

索引回执 [index_receipt.json](../data/index_receipt.json)：126 篇全文、4,349 个片段、4,490 个结构图节点、4,601 条边，BGE-M3 1024 维。126 份逐论文回执与存储实际数量核对后才移除构建锁。没有将尚未解析或未取得的论文计入全文库。

## 有限真实探测

| 会议 | 提问对象 | 模式 | 返回片段中属于目标论文 |
| --- | --- | --- | --- |
| IEEE S&P | SmartPulse | naive | 33 / 44 |
| ACM CCS | Securify | naive | 18 / 44 |
| USENIX Security | TXSPECTOR | mix | 32 / 35 |
| NDSS | Sereum | mix | 29 / 34 |

其中 mix 两次探测返回了实体和关系信息，确认调用的链路包含图检索。生成验证使用 SmartPulse 与 Sereum 两题；分别保留 7、8 个片段，未发生重排降级。这些值只说明这几个具体请求的运行情况，不能泛化为检索召回率或答案准确率。

首次 SmartPulse 生成因桌面代理 TLS 传输中断失败，失败记录保留。通过不触发生成的无效模型请求对照，定位到代理 POST 异常；显式直连后才重新生成。后续复用已有四会议召回结果，未盲目重复整批检索或覆盖旧失败。

引用 ID 与页码合法不等于每个结论都被正确理解；需要继续人工核对。结构图不包含经逐段语义抽取和人工审核的完整漏洞关系。PDF 图片和复杂公式的视觉理解未纳入当前验证。

## 复查

运行 `python -m pytest -q` 和 `npm run test:browser` 可复查无模型单元/交互检查。`scripts/verify_runtime.py` 默认仅预览；加 `--execute` 会运行四次检索与两次真实回答，可能产生供应商费用。实际问答原文和详细响应保留在忽略提交的 `verification/private/`，公开报告移除了账号凭据、操作编号、完整证据与私有路径。
