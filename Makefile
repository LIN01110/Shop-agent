.PHONY: help install test eval build run docker-build docker-run clean

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## 安装依赖
	cd server && pip install -r requirements.txt

test: ## 运行 pytest
	cd server && pytest tests/ -v

eval: ## 运行检索参数评估
	cd server && python -m scripts.eval_retrieval_params

eval-e2e: ## 运行端到端评估
	cd server && python -m scripts.run_eval

build: ## 构建生产镜像
	docker build -t shop-agent:latest .

run: ## 用 docker-compose 启动
	docker-compose up -d

stop: ## 停止 docker-compose
	docker-compose down

clean: ## 清理 Docker 缓存和运行时数据
	docker-compose down -v
	docker system prune -f

lint: ## 代码检查（如安装 ruff/flake8）
	cd server && python -m py_compile $$(find . -name "*.py" | grep -v __pycache__)
