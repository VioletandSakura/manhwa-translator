# 个人漫画翻译器

仅供个人使用的Windows桌面漫画翻译软件。默认简体中文、本地处理优先，翻译阶段优先支持DeepSeek的OpenAI兼容API。

## 当前阶段

第一阶段MVP已经具备：

- PySide6简体中文桌面界面；
- 单张PNG、JPG/JPEG、WEBP、BMP导入；
- RapidOCR + PP-OCRv6本地文字检测与OCR；
- 可替换的检测、OCR、翻译、擦字和渲染接口；
- DeepSeek/OpenAI Compatible文本翻译；
- OpenCV本地擦字；
- Pillow中文横排、竖排、自动换行和字号缩放；
- 原图、修复图、译图、OCR/翻译JSON分别保存；
- SQLite配置、任务、页面检查点和翻译缓存；
- Windows凭据管理器保存API Key；
- 分阶段进度、原图/译图预览和脱敏日志。

批量图片、文件夹、ZIP、EPUB、暂停恢复、多API通道和完整可视化编辑器属于第二阶段。

## 项目与数据位置

```text
项目：D:\Chatgpt\01_Projects\manga-translator-personal
默认输出：D:\Chatgpt\04_Documents\manga-translator-results
临时目录：D:\Chatgpt\99_Temp\manga-translator-personal
数据库：D:\Chatgpt\01_Projects\manga-translator-personal\data\app.db
日志：D:\Chatgpt\01_Projects\manga-translator-personal\logs\app.log
```

API Key不写入数据库、JSON、日志或Git文件，而是保存到Windows凭据管理器，服务标识为`MangaTranslatorPersonal`。

## 启动

双击：

```text
启动个人漫画翻译器.bat
```

或在PowerShell中运行：

```powershell
cd D:\Chatgpt\01_Projects\manga-translator-personal
.\.venv\Scripts\python.exe run.py
```

## DeepSeek配置

进入“API管理”，填写：

```text
服务名称：DeepSeek
Base URL：https://api.deepseek.com/v1
模型：deepseek-chat
API Key：你的密钥
```

点击“安全保存”，再点击“测试连通性”。软件只向DeepSeek发送OCR得到的文字，不向纯文本模型发送原图。

## 从零安装依赖

项目默认使用Python 3.11以上。运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

安装脚本把临时文件和pip缓存指向D盘，不会自动安装LaMa、超分、AI上色等大型模型。

## 测试

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖率：

```powershell
$env:COVERAGE_FILE='D:\Chatgpt\99_Temp\manga-translator-personal\.coverage'
.\.venv\Scripts\python.exe -m pytest -q -p pytest_cov --cov=app --cov-report=term-missing
```

## 已知限制

- 第一阶段只处理单张图片；
- 当前默认OCR为PP-OCRv6通用中英文模型，日文漫画专用OCR将在第二阶段加入；
- OpenCV擦字对网点、复杂背景和背景线条的恢复能力不如LaMa；
- 当前渲染支持基础横排/竖排，复杂日文竖排标点和高级编辑器属于后续阶段；
- DeepSeek价格、配额和地区政策以服务商实时信息为准；翻译缓存会减少重复请求。

