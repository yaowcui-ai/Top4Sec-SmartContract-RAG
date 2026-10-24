# 第三方组件与来源

- **Academic_Smart_Contract_Papers**，hzysvilla 及贡献者，来源 https://github.com/hzysvilla/Academic_Smart_Contract_Papers ，固定 commit `aede99b97104f2f1dd447922d9b54902a89c81d9`。目录快照及其衍生结构化目录依上游 GPL-3.0 保留来源；上游 LICENSE 位于 `data/source/catalog.LICENSE`。目录许可不代表论文全文的许可。
- **LightRAG**，HKUDS，https://github.com/HKUDS/LightRAG ，MIT，使用固定容器镜像摘要，未将整个上游源码复制到本仓库。
- **PyMuPDF**，Artifex，https://pymupdf.readthedocs.io/ ，用于解析 PDF，依其 AGPL/商业双许可提供；本项目选择 AGPL 开源使用方式，项目代码以 AGPL-3.0 发布。
- **Marked**，MIT，和 **DOMPurify**，Apache-2.0 或 MPL-2.0。浏览器本地组件的版本、校验和和许可原文保留在 `app/web/vendor/`。DOMPurify 选择 Apache-2.0 许可。
- **BGE-M3 / BGE Reranker** 通过模型供应商接口调用；使用需遵守供应商服务条款及相应模型许可，本仓库不分发模型权重。
- FastAPI、SQLAlchemy、httpx、Uvicorn、pwdlib、PyMySQL 等依赖在安装时由各自项目提供，版本见 `requirements-lock.txt`。Docker MySQL 与基础镜像按各自许可使用，不纳入本项目代码授权。

论文作者及出版方保有论文、图表和其他内容的权利。PDF 和全文索引仅在本地按公开可访问来源获取；请依具体来源许可使用。GitHub 仓库不包含下载的 PDF、完整正文、数据库备份、模型密钥或会话凭据。
