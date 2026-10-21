# 代码与依赖来源

本仓库是作者授权发布的本地工作台代码的独立整理版，采用 MIT。未导入旧仓库历史、真实业务数据、内部报告、截图、录音或第三方应用资源。

- Python 代码仅使用标准库，没有第三方 pip 运行依赖。Python 本身按 Python Software Foundation 许可证分发；本仓库不重新分发 Python。
- macOS 客户端使用系统 Cocoa、WebKit、Speech、AVFoundation、CoreAudio 和 AudioToolbox。它们由 Apple 提供，适用 Apple 系统/SDK 条款，不因本仓库 MIT 许可而重新授权。本仓库仅包含调用这些框架的自有 Swift 源码。
- 网页无 CDN、第三方脚本、字体下载或打包的字体资源，使用系统字体。
- 示例 Excel 由本仓库标准库生成器创建，内容明确虚构，无第三方模板、图片或公司表格素材。
- CI 调用 GitHub Actions 官方 actions/checkout、actions/setup-python 与 actions/setup-node；这些工具不打包进软件，适用各自上游许可证和 GitHub 服务条款。

这是源码/依赖清单审计，不是对所有潜在权利问题的法律鉴定。若发现来源或许可问题，请使用仓库安全报告入口联系维护者。

CI工具上游许可核对：[checkout v4](https://github.com/actions/checkout/blob/v4/LICENSE)、[setup-python v5](https://github.com/actions/setup-python/blob/v5/LICENSE)、[setup-node v4](https://github.com/actions/setup-node/blob/v4/LICENSE)。
