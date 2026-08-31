# Purpose: 下载ESCI Small数据集脚本：从Amazon Science仓库克隆数据。

param(
    [string]$TargetDir = "D:\RAG\data_external\esci",
    [string]$RepoUrl = "https://github.com/amazon-science/esci-data.git"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoDir = Join-Path $TargetDir "esci-data"
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is required but was not found in PATH."
}

if (-not (Get-Command git-lfs -ErrorAction SilentlyContinue)) {
    throw "git-lfs is required but was not found in PATH."
}

if (-not (Test-Path $repoDir)) {
    $env:GIT_LFS_SKIP_SMUDGE = "1"
    git clone --depth 1 --filter=blob:none --sparse $RepoUrl $repoDir
}

Push-Location $repoDir
try {
    git sparse-checkout set shopping_queries_dataset
    git lfs pull --include="shopping_queries_dataset/shopping_queries_dataset_examples.parquet,shopping_queries_dataset/shopping_queries_dataset_products.parquet,shopping_queries_dataset/shopping_queries_dataset_sources.csv" --exclude=""

    $examples = Join-Path $repoDir "shopping_queries_dataset\shopping_queries_dataset_examples.parquet"
    $products = Join-Path $repoDir "shopping_queries_dataset\shopping_queries_dataset_products.parquet"
    if (-not (Test-Path $examples)) {
        throw "Missing examples parquet: $examples"
    }
    if (-not (Test-Path $products)) {
        throw "Missing products parquet: $products"
    }

    Write-Host "ESCI files ready:"
    Get-ChildItem -LiteralPath (Join-Path $repoDir "shopping_queries_dataset") |
        Select-Object Name,@{Name="SizeMB";Expression={[math]::Round($_.Length / 1MB, 2)}} |
        Format-Table -AutoSize
}
finally {
    Pop-Location
}
