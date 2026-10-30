# 五分钟跑通第一张图

适用于 `0.1.0-alpha.1` 源码测试版：Windows 10/11、Python 3.12 x64。第一次安装需要联网下载依赖与 OCR 模型，时长取决于网络；无需 GPU。

1. 从 [Releases](https://github.com/VioletandSakura/manhwa-translator/releases) 下载带版本号的 `source.zip`，解压到自己的可写文件夹。请不要直接覆盖旧项目；旧的 `data/` 和 `outputs/` 是你的记录与结果。
2. 安装 Python 3.12 x64，在解压后的项目目录打开 PowerShell，运行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
   ```

3. 双击“启动个人漫画翻译器.vbs”。选择 `docs/samples/` 中的示例图，**取消勾选“调用AI翻译”**，填写批次名称后开始。
4. 选中完成页，点击“编辑选中页”，选择一个文字框并填写中文译文。如果标红，先选择“确认处理”。点击“保存并重绘译图”。
5. 回到翻译工作台，从“导出”菜单导出译图 ZIP。你已经完成从输入到结果的完整流程。

没有密钥也能完成以上步骤。需要在线翻译时，到“翻译服务”填写服务商的地址、模型和密钥，安全保存并测试；启用后会产生服务商费用。

点击窗口右上角关闭按钮会隐藏到托盘；托盘菜单中的“退出”会等待当前处理步骤结束再关闭。需要切回窗口时点击托盘图标。

安装失败时保留错误码，在 [Issues](https://github.com/VioletandSakura/manhwa-translator/issues) 选择“使用问题”反馈。更详细的输入格式、暂停恢复、字体和数据位置见 [README](../README.md)。
