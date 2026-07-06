# Codex ESP8266 状态屏

这个项目用 ESP8266 驱动 240x320 SPI TFT 屏幕，显示 Codex 当前状态和 Codex 用量。

## 文件说明

- `codex_usage.py`：异步用量获取模块。
- `pc_client.py`：电脑端异步守护进程和 Codex hook 通知客户端。
- `pc_client_config.example.json`：电脑端配置示例，复制为 `pc_client_config.json` 后填写 ESP IP。
- `esp/`：需要上传到 ESP8266 MicroPython 文件系统的代码。
- `tests/`：`codex_usage.py` 的 pytest 测试。

## 电脑端设置

1. 复制 `pc_client_config.example.json` 为 `pc_client_config.json`。
2. 把 `esp_host` 改成 ESP 串口输出里的 IP 地址。
3. 启动守护进程：

```powershell
python .\pc_client.py daemon
```

Codex hooks 会调用异步 hook 通知命令：

```powershell
python .\pc_client.py hook <EventName>
```

如果守护进程没有运行，hook 命令会快速退出，不会拖慢 Codex。

守护进程会直接 import `codex_usage.py`，不会把用量脚本作为子进程或命令行程序运行。

用量刷新规则：

- 守护进程启动时主动刷新一次。
- 收到 `Stop` hook 时主动刷新一次。
- 周期任务每 60 秒检查一次；只有最近 60 秒内没有主动刷新请求时，才会触发周期刷新。

## ESP8266 设置

1. 编辑 `esp/config.py`，填写 WiFi 名称和密码。
2. 默认先用 `TFT_DRIVER = "st7789"`。
3. 如果屏幕黑屏或花屏，把 `TFT_DRIVER` 改成 `"ili9341"` 再试。
4. 把 `esp/config.py`、`esp/tft_display.py`、`esp/main.py` 上传到 ESP8266。
5. 重启 ESP，从串口输出读取 IP 地址。

默认 NodeMCU 接线见 `esp/README.md`。

## 状态映射

- `UserPromptSubmit`、`PreToolUse`、`PostToolUse`：`working`，红色区域闪烁。
- `PermissionRequest`：`waiting`，黄色区域常亮。
- `Stop`：`idle`，绿色区域常亮，并刷新用量。

## 测试

`tests/test_codex_usage.py` 会访问真实 Codex 用量接口，需要本机已经登录 Codex，并且网络可以访问 `chatgpt.com`。

```powershell
python -m pytest
```
