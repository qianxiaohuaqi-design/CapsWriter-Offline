# 静态产品网站

在仓库根运行 `python -m http.server 8080 --directory website`，访问 `http://127.0.0.1:8080`。无构建工具、第三方脚本、账户或跟踪器；所有视觉文件来自项目既有资产。官网源码不会自动部署。

`assets/control-center-preview.png` 是原仓库既有截图，页面明确标注它不是本次 V1 实机测试证据。正式发行前应替换为对应代码/产物 SHA 的 Windows 真实截图，不能用 HTML 原型或 AI 图片代替。

## 下载门禁

`release-manifest.json` 默认 `candidate`，没有下载资产。`release.js` 仅在版本、架构、文件名、SHA-256、实际大小、签名状态、对应代码 SHA、Windows 验收和公开核验全部满足时启用按钮。文件 URL 只接受本项目 `github.com/.../releases/download/v版本/匹配文件名`。

`tools/build_v1.py` 生成初始产物元数据。它不标记 Windows 验收或公共下载通过；这些必须在用户授权后，以真实验证结果更新。发布流程详见 `packaging/README.md`。无效 JSON、获取失败、缺失摘要或只有标题的 Release 都保持按钮禁用。

验收：桌面与手机视口、键盘焦点与 FAQ、断网/损坏清单禁用、官方链接、截图来源、版本/哈希/签名一致性。源码与 Node 逻辑测试不替代真实浏览器视觉验收。
