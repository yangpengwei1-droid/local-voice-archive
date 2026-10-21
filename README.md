# Local Voice Archive · 本地语音档案助手

**0.1.0-alpha · 实验版 · MIT**

面向本地 Excel 工作副本的合同检索与状态登记工具。macOS 原生中文语音 + 本机 Python 服务；也可以在普通浏览器用文字操作。没有在线 WPS 同步，不包含真实合同或公司资料。

*An experimental, local-first macOS voice-assisted Excel workbench. Human selection and explicit save remain required. All bundled sample data is fictional.*

## 能做什么

- 先查项目，再用服务、公司、金额缩小范围；相关候选标出匹配/未匹配词，保留真实名称与主补合同区别，必须人工选择。
- “回退上一句”按钮和同名明确语音指令恢复检索范围。最多30步、30分钟；刷新、下一份、绑定切换或明确查找新项目后重新开始。存在未保存编辑/预览时先处理草稿，不擅自丢弃；回退不撤销已保存业务内容。
- 选合同后显示编号和字段；“已询价”等生成预览，按分项或全部保存按钮才直接写本地 xlsx，实际文件回读后显示保存结果。
- 支持文件编号、招采、返还、结案、签订日期及有冲突检查的撤销；保存前备份，检测外部编辑，并记录恢复事务。

## 环境与启动

目标环境为 **macOS 13+、Python 3.9+、Xcode Command Line Tools**。编译使用当前机器架构，非公证发行包；系统/芯片版本未做完整兼容性认证。完整语音体验依赖 Apple 设备端中文识别模型及系统权限。Windows 未支持；Linux 上的部分 Python 测试不等于产品跨平台支持。

```sh
python3 --version
xcode-select --install  # 已安装时无需重复
python3 -m venv .venv
source .venv/bin/activate
# 无第三方 pip 运行依赖
./build_macos.command
./start_macos.command
```

也可运行 `python3 r3_app.py`，在浏览器打开 `http://127.0.0.1:8877`。普通浏览器没有原生语音和选文件桥；可在服务停止时运行 `python3 -m tools.bind_workfile var/my-working-copy.xlsx` 明确绑定副本，再启动服务；也可使用原生客户端。服务独立运行，关窗口不会关后台；前台启动的服务用 Ctrl+C 停止。启动器记录 PID 到 `var/runtime/service.json`，停止分离后台前请核对进程身份；不要按旧 PID 随意结束别的进程。

## 先体验虚构示例

`examples/demo.xlsx` 仅有虚构数据，项目/公司不代表真实主体。请复制后使用，不能直接绑定被保护的示例源文件：

```sh
mkdir -p var
cp examples/demo.xlsx var/my-working-copy.xlsx
```

打开原生客户端 → “当前工作文件与编号规则” → “选择本地工作 xlsx”，选择这个副本。先查“第五届星桥创意节”，再说“安保服务”；补充“酒店驻地”缩到一份。错误补充后点击“回退上一句”，再说正确条件。点击具体合同后说“已询价”，核对旧值→新值再保存。语音默认关闭，需手动打开并同意系统权限。

## 自己的表格和字段

当前写入引擎仍使用固定模板，**不能直接在任意列布局上写入**：

| 字段 | 列 |
|---|---|
| 登记日期 | A |
| 合同编号 / 文件编号 / 招采状态 | E / F / G |
| 项目 / 合同名称 / 金额 / 对应方 | I / J / K / M |
| 合同返还 / 结案 / 签订日期 | U / V / X |

优先读取“总表”，否则第一张表；要求 E/F/G/I/J/M/V/X 表头符合受支持名称，U必须为“返还情况”才启用；请以自带示例为准。招采/返还/结案选项受支持的下拉及程序规则约束，不是任意状态编辑器。

可复制并修改 `examples/field-map.json`，把自己的列映射到支持模板，生成**新的标准工作副本**：

```sh
python3 -m tools.prepare_workbook input.xlsx var/converted.xlsx --mapping examples/field-map.json
```

配置 `sheet`、`header_row` 和 `columns` 中的逻辑字段→Excel列字母。转换只提取值（公式取现有缓存，不重算），不保留原布局/样式/其他sheet，不做同步或反向合并；日期请先转为明确的 ISO 文本并核对。转换器拒绝合并单元格和宏。它与工作台直接保存的保真路径不同：对已经符合模板的工作副本保存时，非目标ZIP条目保持，并检查公式、样式、验证及其他sheet的支持边界；复杂/未知格式可能拒绝。

编号规则有业务前提：A年早于2024按登记年；2024及以后按办理当天北京时间年份；该年最大合法号加一，至少三位序号，已有编号不改。A年缺失/异常须人工明确，不由项目名推测。不是通用可定制编号引擎，也不具备跨电脑全局发号能力。结案语义默认“明确这份是结案报告”生成“结案通过”待保存，**假设交付的结案报告已经审批通过**；不符合此流程时请手工选择状态，不使用该快捷语句。

## 数据、限制与测试

数据只保存在选定工作文件、本机 `var/runtime/` 及浏览器本地存储；日志可能含查询文本，详见 [安全说明](SECURITY.md)。程序不保存麦克风录音，不主动上传；未完成保存事务时不要删除运行目录。仅可信单用户本机使用。

不支持在线WPS、多人并发编辑、任意Excel模板、加密/宏工作簿、公式重算、通用合并单元格展开、说话人验证。未提供真人识别准确率或端到端时延保证；文本/脚本测试不证明真人语音质量。

```sh
python3 -m unittest discover -s tests -v
# 可选：Node.js 18+，实际网页脚本在模拟DOM连接虚构数据HTTP服务
python3 -m tests.run_ui
```

测试仅使用生成的虚构Excel及临时目录，包括保存回读/撤销、冲突/恢复、检索硬条件、部分字段候选、回退和草稿保护。`./build_macos.command` 可另行检查原生编译，不会开启麦克风。首次系统权限、实体GUI、真人语音需在自己的设备上验收。

[MIT许可证](LICENSE) · [依赖与来源](THIRD_PARTY_NOTICES.md) · [安全说明](SECURITY.md)
