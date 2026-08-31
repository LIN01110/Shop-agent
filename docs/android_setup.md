# Android 构建与联调

## 环境要求

- JDK 17 或以上
- Android Studio
- Android SDK 35
- Gradle Wrapper 或系统 Gradle

可先运行：

```powershell
cd D:\RAG
.\scripts\check_android_env.ps1
```

## Android Studio 打开方式

打开目录：

```text
D:\RAG\client
```

等待 Gradle Sync 完成后，选择真机运行。真机推荐使用 USB 反向代理：

```text
http://127.0.0.1:8000
```

该地址已写在 `ChatSseClient` 的默认配置里。模拟器访问电脑本机时再改为 `http://10.0.2.2:8000`。

## 联调步骤

1. 启动后端：

```powershell
cd D:\RAG
.\scripts\run_server.ps1
```

2. 另开一个 PowerShell，开启 USB 反向代理：

```powershell
D:\Android\Sdk\platform-tools\adb.exe reverse tcp:8000 tcp:8000
```

3. 在 Android Studio 运行 App。
4. 输入：`推荐一款适合油皮的洗面奶，预算100以内`
5. 观察是否出现流式回复和商品卡片。

## 常见问题

- 物理手机不能使用 `10.0.2.2`，需要改成电脑局域网 IP。
- Windows 防火墙可能阻止手机访问后端端口。
- 如果 Gradle Sync 报 SDK 缺失，在 Android Studio SDK Manager 安装 `Android SDK Platform 35`。
