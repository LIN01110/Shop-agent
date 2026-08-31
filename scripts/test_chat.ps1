# Purpose: 聊天接口测试脚本：发送测试消息到/chat，验证SSE返回。

param(
    [string]$Message = "",
    [string]$SessionId = "manual-test",
    [string]$Url = "http://127.0.0.1:8000/chat"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Message)) {
    $encodedDefaultMessage = "5o6o6I2Q5LiA5qy+5L+d5rm/55y86Zyc77yM6aKE566XMjUw5Lul5YaF"
    $Message = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedDefaultMessage))
}

$body = @{
    session_id = $SessionId
    message = $Message
} | ConvertTo-Json -Compress

Invoke-WebRequest `
    -UseBasicParsing `
    -Method Post `
    -Uri $Url `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) |
    Select-Object -ExpandProperty Content
