# Purpose: Android环境检查脚本：检查JDK、Android SDK、Gradle等环境是否就绪。

$ErrorActionPreference = "Continue"

Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "Android environment check"
Write-Host "========================="

function Get-FirstExistingPath($paths) {
    foreach ($path in $paths) {
        if ($path -and (Test-Path $path)) {
            return $path
        }
    }
    return $null
}

$java = Get-Command java -ErrorAction SilentlyContinue
if ($java) {
    Write-Host "[OK] java:" $java.Source
    java -version
} else {
    $studioJava = Get-FirstExistingPath @(
        "D:\Android\Studio\jbr\bin\java.exe",
        "C:\Program Files\Android\Android Studio\jbr\bin\java.exe"
    )
    if ($studioJava) {
        Write-Host "[OK] Android Studio bundled java:" $studioJava
        & $studioJava -version
    } else {
        Write-Host "[MISS] java was not found in PATH"
    }
}

if (Test-Path ".\client\gradlew.bat") {
    Write-Host "[OK] Gradle wrapper: client\gradlew.bat"
} else {
    $gradle = Get-Command gradle -ErrorAction SilentlyContinue
    if ($gradle) {
        Write-Host "[OK] system gradle:" $gradle.Source
        gradle --version
    } else {
        Write-Host "[MISS] Gradle wrapper/system Gradle was not found"
    }
}

$sdkCandidates = @(
    $env:ANDROID_HOME,
    $env:ANDROID_SDK_ROOT,
    [Environment]::GetEnvironmentVariable("ANDROID_HOME", "User"),
    [Environment]::GetEnvironmentVariable("ANDROID_SDK_ROOT", "User"),
    "D:\Android\Sdk",
    "$env:LOCALAPPDATA\Android\Sdk"
) |
    Where-Object { $_ -and $_.Trim() -ne "" } |
    Select-Object -Unique

$sdkFound = $false
foreach ($candidate in $sdkCandidates) {
    if (Test-Path $candidate) {
        Write-Host "[OK] Android SDK:" $candidate
        $sdkFound = $true
    }
}

if (-not $sdkFound) {
    Write-Host "[MISS] Android SDK was not found. Install Android Studio or set ANDROID_HOME."
} else {
    $adb = Get-FirstExistingPath @(
        "$env:ANDROID_HOME\platform-tools\adb.exe",
        "$([Environment]::GetEnvironmentVariable("ANDROID_HOME", "User"))\platform-tools\adb.exe",
        "D:\Android\Sdk\platform-tools\adb.exe"
    )
    if ($adb) {
        Write-Host "[OK] adb:" $adb
        & $adb version | Select-Object -First 2
    } else {
        Write-Host "[MISS] adb was not found under the SDK platform-tools directory"
    }

    if (Test-Path "D:\Android\Sdk\platforms\android-35") {
        Write-Host "[OK] Android SDK Platform 35"
    } else {
        Write-Host "[MISS] Android SDK Platform 35"
    }

    if (Test-Path "D:\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat") {
        Write-Host "[OK] Android SDK Command-line Tools"
    } else {
        Write-Host "[MISS] Android SDK Command-line Tools"
    }
}

if (Test-Path ".\client\app\build.gradle.kts") {
    Write-Host "[OK] Android project files are present"
}
