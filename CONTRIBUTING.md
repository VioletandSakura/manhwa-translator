# 参与与反馈

这是面向 Windows 的早期源码测试版。请先按 [快速开始](docs/QUICK_START.md)跑通离线样例，再尝试自己的图片。

报告问题时使用仓库的 Issue 模板，提供版本号、Windows/Python 版本、操作步骤、预期与实际结果。识别问题尽量使用有权公开的最小样本；不要上传密钥、完整数据库、私人漫画或含有私人路径的日志。保留服务商错误码即可，不需要贴 Authorization 请求头。

开发环境使用 Python 3.12 x64：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Dev
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -B -m pytest -q
```

需要可见桌面流程时移除 `QT_QPA_PLATFORM` 后运行 `scripts/qa_desktop.py`，每次使用新的 `.artifacts/` 子目录。默认验收离线且使用空凭据，不能在 CI 中启用 `--live`。

请提交围绕一个具体问题的改动，并描述实际验证。涉及原图保护、译文交换、暂停恢复、缓存失效的修改应保留相应回归检查。性能改动请同时记录样本、测量方式、输出是否一致和未改善的指标。

提交贡献表示你有权提供该内容，并同意以本项目 MIT 许可提供自己的贡献；第三方内容请注明其来源及适用许可。
