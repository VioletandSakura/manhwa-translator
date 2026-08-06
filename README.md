# 个人漫画翻译器

仅供个人使用的Windows桌面漫画翻译软件。默认简体中文、本地处理优先，翻译阶段优先支持DeepSeek的OpenAI兼容API。

## 当前阶段

第二阶段个人可用版已经具备：

- PySide6简体中文桌面界面；
- 多张PNG、JPG/JPEG、WEBP、BMP、文件夹、ZIP和EPUB导入；
- EPUB2/EPUB3容器、OPF、Manifest和Spine页序解析；
- ZIP/EPUB路径穿越、异常压缩比、条目数和解压体积保护；
- RapidOCR + PP-OCRv6本地文字检测与OCR；
- 可替换的检测、OCR、翻译、擦字和渲染接口；
- DeepSeek/OpenAI Compatible文本翻译；
- OpenCV本地擦字；
- Pillow中文横排、竖排、自动换行和字号缩放；
- 原图、修复图、译图、OCR/翻译JSON分别保存；
- SQLite配置、任务、页面检查点和翻译缓存；
- Windows凭据管理器保存API Key；
- 可恢复任务队列、暂停、继续、取消和单页失败重试；
- 多API通道、优先级、同级轮询、RPM限制、最大并发与故障切换；
- 术语表、提示词、替换规则、可保存/应用预设和任务历史恢复入口；
- 可选MangaOCR日漫OCR适配器；
- 可选LaMa ONNX擦字及OpenCV自动降级；
- TXT/JSON人工翻译交换、导入后本地重绘和译图ZIP导出；
- 单页可视化编辑器：文本框移动、数值缩放/旋转、文本与样式、复制删除、撤销重做、蒙版画笔和橡皮擦；
- 分阶段进度、逐页即时预览和脱敏日志。

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
模型：deepseek-v4-flash
API Key：你的密钥
```

粘贴API Key后点击“安全保存”，再点击“获取可用模型”，可从该账号实际可用的模型名单中选择；模型框仍支持手工输入，兼容不提供模型列表的自定义服务。最后点击“测试连通性”。获取名单和连通性测试都只读取模型列表，最长约15秒，不执行翻译、不会消耗翻译Token。软件翻译时只向DeepSeek发送OCR得到的文字，不向纯文本模型发送原图。

可以创建多个通道。通道只有在“测试连通性”成功后才参与翻译；主通道失败时会自动尝试下一个已验证通道。

## 批量、ZIP和EPUB

在“开始翻译”中使用“选择文件”可多选图片、ZIP和EPUB，或使用“选择文件夹”。页面按自然文件名或EPUB Spine顺序进入队列。已完成页面保存在SQLite检查点中，重启软件后可在“任务历史”选择任务并点击“恢复选中任务”；已完成页会被跳过，不会重复调用翻译API。ZIP/EPUB提取的图片在不发生重名冲突时保留原文件名。

多图、文件夹、ZIP和EPUB任务需要填写“批次名称”。结果会统一保存到“输出目录/批次名称”下，并按 `original`、`inpainted`、`translated`、`metadata` 分类；单张图片未填写批次名称时仍直接使用所选输出目录。为避免覆盖旧结果，已存在且非空的同名批次目录不会被复用。

每页完成后立即显示译图。队列表格支持查看页面状态、暂停、继续、取消和选择失败页重试。

## 可选MangaOCR与LaMa

软件不会自动下载大型模型：

```text
MangaOCR模型：models\manga-ocr-base（约400MB）
LaMa ONNX模型：models\lama.onnx（常见约100-250MB，取决于导出版本）
```

MangaOCR还需要PyTorch、Transformers等依赖，CPU安装通常额外占用1GB以上，CUDA版本更大。需要时手动运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mangaocr.txt
```

模型未安装时继续使用RapidOCR和OpenCV，不影响普通翻译。

## 从零安装依赖

项目默认使用Python 3.11以上。运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

安装脚本把临时文件和pip缓存指向D盘，不会自动安装LaMa、超分、AI上色等大型模型。

## 测试

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖率：

```powershell
$env:COVERAGE_FILE='D:\Chatgpt\99_Temp\manga-translator-personal\.coverage'
.\.venv\Scripts\python.exe -m pytest -q -p pytest_cov --cov=app --cov-report=term-missing
```

## 已知限制

- 为保证本地OCR稳定，页面流水线当前按页顺序执行；API通道内部会执行RPM与并发限制；
- MangaOCR模型和LaMa ONNX模型尚未下载，需要用户确认后手动安装；
- LaMa ONNX导出格式并不统一，适配器支持常见单输入四通道和图像/蒙版双输入格式，其他导出可能自动降级OpenCV；
- 编辑器支持核心文本框与蒙版操作，拖拽缩放手柄、全文查找替换和更精细的日文竖排标点仍会继续增强；
- DeepSeek价格、配额和地区政策以服务商实时信息为准；翻译缓存会减少重复请求。
