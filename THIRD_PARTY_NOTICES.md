# 第三方组件与资源

核对日期：2026-10-31。本项目自身源码采用 [MIT](LICENSE)。第三方组件保留各自的许可；项目的 MIT 许可不改变它们的条款。

首版发行物为源码 ZIP，不包含 Python、第三方 wheel、DLL、模型权重、字体文件或用户漫画。安装脚本从包索引安装依赖，各包的许可证、声明与第三方通知保留在安装目录中。将来若制作包含依赖的安装包，需要对实际打包的组件重新整理相应声明、源码获取与替换方式；本文件不把源码包检查扩展成二进制分发验收。

## 默认运行依赖

下表版本对应本轮验证环境；具体安装约束见 `constraints-windows-py312.txt`。

| 组件 | 验证版本 | 上游许可与来源 |
| --- | --- | --- |
| PySide6-Essentials / Shiboken6 | 6.11.1 | 上游提供 LGPLv3/GPL/商业许可选项；参见 [Qt for Python 许可](https://doc.qt.io/qtforpython-6/licenses.html)及实际 wheel 声明。本项目使用 Essentials 中的 QtCore、QtGui、QtWidgets、SVG 插件和测试用 QtTest，并未修改或内嵌 Qt 源码。不安装不使用的 Addons 元包。 |
| NumPy | 2.5.1 | [源码与许可](https://github.com/numpy/numpy/blob/main/LICENSE.txt)，含 BSD 及 wheel 内第三方声明 |
| OpenCV / opencv-python | 5.0.0.93 | [打包项目许可](https://github.com/opencv/opencv-python/blob/master/LICENSE.txt)及 wheel 的 `LICENSE-3RD-PARTY.txt`；OpenCV 采用 Apache-2.0 |
| Pillow | 12.3.0 | [MIT-CMU 与第三方声明](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| HTTPX | 0.28.1 | [BSD-3-Clause](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| keyring | 25.7.0 | [MIT](https://github.com/jaraco/keyring/blob/main/LICENSE) |
| RapidOCR | 3.9.2 | [Apache-2.0](https://github.com/RapidAI/RapidOCR/blob/main/LICENSE) |
| ONNX Runtime | 1.28.0 | [MIT 与第三方通知](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |

依赖还会安装各自的传递依赖；完整已验证版本由约束文件和验收环境记录列出。测试工具不随源码包附带其可执行程序，安装 `-Dev` 时由包索引获取。

## 模型

- RapidOCR 默认模型由 RapidOCR 的模型配置选择/下载，基于 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)（[Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)）。验证过的文件名和 SHA256 记录在 [模型清单](models/manifest.json)。模型文件不放进此仓库或源码 ZIP。
- 可选 MangaOCR 代码：[kha-white/manga-ocr](https://github.com/kha-white/manga-ocr)，Apache-2.0；权重来源：[kha-white/manga-ocr-base](https://huggingface.co/kha-white/manga-ocr-base)，模型卡标注 Apache-2.0。首版未安装、未下载、未验收此可选路径。
- 可选 LaMa 上游研究代码：[advimman/lama](https://github.com/advimman/lama)。本项目只提供 ONNX 输入适配，不分发或自动下载任何 `lama.onnx`。不同导出文件可能有不同来源，不能仅凭文件名推断权重许可；请同时保留实际导出仓库、模型版本、SHA256 与许可。本轮未验收 LaMa 权重。

## 字体、图标与示例

- 字体从用户选择的文件或 Windows 已安装字体读取，不将系统字体复制到发行包。使用其他字体时应查看其自身许可。
- UI SVG 图标由本项目的路径数据生成；仓库中的小型复选标记与箭头为本项目资源，随源码采用 MIT。
- README 界面截图使用本项目生成的测试图；日文测试样本由 `scripts/qa_japanese_samples.py` 生成，人物、气泡、背景和短句均为验收夹具，随源码采用 MIT。它们不是第三方漫画，也不能代表复杂商业漫画的整体质量。
- 没有把用户漫画、API 密钥、输出、数据库或日志放入公开示例。

本次检查覆盖仓库文件、Git 历史中的文件清单、依赖元数据和上游声明。未发现作为项目源码目录整包引入的第三方实现；这不构成对未知历史来源的法律保证。发现遗漏来源时，请通过仓库反馈并提供具体文件位置。
