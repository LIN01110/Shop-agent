"""
Purpose: 用 LoRA 微调 BGE embedding（sentence-transformers + peft），
         数据为 ESCI 人工标注对 + ABO 中文标题-描述对，Loss 为 MultipleNegativesRankingLoss。

设计要点（面试可讲）:
  - LoRA rank=16 / alpha=32，只挂在 attention 的 query/key/value 投影上，
    冻结主干，可训练参数约占 0.3%，CPU 也能跑；
  - in-batch negatives：同 batch 内其他样本的正例即负例，batch 越大负例越多；
  - 训练后 merge_and_unload 合并回主干，推理零额外开销，输出标准 ST 模型目录。

用法（hh_neuron 环境，CPU 约 3-5 分钟/100 步）:
  HF_ENDPOINT=https://hf-mirror.com python scripts/finetune_st_lora.py --max-steps 100
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

DEFAULT_TRAIN = ROOT_DIR / "data" / "benchmarks" / "st_train" / "train_pairs.jsonl"
DEFAULT_OUTPUT = ROOT_DIR / "models" / "bge-small-zh-v1.5-lora-merged"


def main() -> int:
    parser = argparse.ArgumentParser(description="LoRA fine-tune a BGE sentence-transformers model.")
    parser.add_argument("--base-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--train", default=str(DEFAULT_TRAIN))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=96)
    parser.add_argument("--merge-only", action="store_true", help="只做 adapter 合并输出，不训练")
    args = parser.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from sentence_transformers.trainer import SentenceTransformerTrainer
    from sentence_transformers.training_args import SentenceTransformerTrainingArguments

    torch.set_num_threads(max(1, (torch.get_num_threads() or 4)))

    pairs = [
        json.loads(line)
        for line in Path(args.train).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"train_pairs={len(pairs)} base={args.base_model} steps={args.max_steps} "
          f"batch={args.batch_size} rank={args.lora_rank}", flush=True)

    from peft import PeftModel

    adapter_dir = Path(args.output) / "adapter"
    model = SentenceTransformer(args.base_model)
    model.max_seq_length = args.max_length

    # ST >= 5.x：auto_model 是只读 property，真实权重在 .model 上
    if adapter_dir.exists():
        # 续训：加载已有 adapter（每轮优化器状态重置，短训可接受）
        model[0].model = PeftModel.from_pretrained(model[0].model, str(adapter_dir), is_trainable=True)
        print(f"resume adapter from {adapter_dir}", flush=True)
    else:
        lora_config = LoraConfig(
            r=args.lora_rank,
            lora_alpha=args.lora_alpha,
            lora_dropout=0.05,
            bias="none",
            target_modules=["query", "key", "value"],
        )
        model[0].model = get_peft_model(model[0].model, lora_config)
    model[0].model.print_trainable_parameters()

    if args.merge_only:
        # 最终合并：加载 adapter → merge 回主干 → 输出标准 ST 模型目录
        model[0].model = model[0].model.merge_and_unload()
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        model.save(str(output))
        print(f"saved merged model to {output}", flush=True)
        return 0

    dataset = Dataset.from_list(
        [{"anchor": p["query"], "positive": p["positive"]} for p in pairs]
    )
    loss = MultipleNegativesRankingLoss(model)

    training_args = SentenceTransformerTrainingArguments(
        output_dir=str(Path(args.output) / "trainer_logs"),
        num_train_epochs=1,
        max_steps=args.max_steps,
        per_device_train_batch_size=args.batch_size,
        learning_rate=args.lr,
        warmup_ratio=0.1,
        logging_steps=10,
        save_strategy="no",
        report_to=[],
        dataloader_num_workers=0,
        seed=42,
    )
    trainer = SentenceTransformerTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        loss=loss,
    )
    trainer.train()

    # 每轮结束只保存 adapter 权重，下一轮从此续训（规避 HF Trainer checkpoint 结构不兼容）
    adapter_dir.mkdir(parents=True, exist_ok=True)
    model[0].model.save_pretrained(str(adapter_dir))
    print(f"saved adapter to {adapter_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
