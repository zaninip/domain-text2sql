---
license: apache-2.0
base_model: Qwen/Qwen3-1.7B
library_name: peft
pipeline_tag: text-generation
language:
  - fr
  - it
tags:
  - text-to-sql
  - sql
  - duckdb
  - lora
  - qlora
  - energy
  - eco2mix
---

# Qwen3-1.7B éCO2mix SQL (LoRA adapter)

A LoRA adapter that turns **French and Italian questions about the French electricity system**
into **DuckDB SQL** over one table of RTE's éCO2mix open data (regional consumption and
production, 30-minute steps, 2013 to June 2026). It is the fine-tuned half of a portfolio
project that compares a small model before and after fine-tuning, side by side:
[github.com/zaninip/domain-text2sql](https://github.com/zaninip/domain-text2sql).

The model writes the SQL only. An application runs it on the data and shows the result; the
numbers a user sees come from the database, never from the model.

- **Base model:** [Qwen/Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) (Apache 2.0), with
  its thinking mode off.
- **CPU version:** the adapter merged and quantized to GGUF Q4_K_M, with the base model
  quantized the same way for comparison:
  [zaninip/qwen3-1.7b-eco2mix-sql-GGUF](https://huggingface.co/zaninip/qwen3-1.7b-eco2mix-sql-GGUF).

## Results

Test split of 490 questions from 7 question templates that never appear in training (the
split is made by template, not by example). Execution accuracy: the query is right when its
result equals the result of the reference query (rows compared as multisets, numbers to a
relative 1e-4).

| Model | Execution accuracy | Valid SQL |
|---|---:|---:|
| Base, zero-shot | 14.3 % | 86.9 % |
| Base, few-shot (9 train examples; mean of 3 draws, range 34.1-43.1 %) | 37.1 % | 79.0-90.0 % |
| **Fine-tuned (this adapter, float16)** | **64.1 %** | 76.9 % |
| Fine-tuned, GGUF Q4_K_M (CPU, llama.cpp) | 62.0 % | 75.9 % |
| Base, GGUF Q4_K_M | 11.6 % | 73.3 % |

The fine-tuned model beats zero-shot on 246 questions against 2, and every few-shot draw
(p < 1e-12, paired McNemar). By language: French 70.2 %, Italian 58.0 %. Quantization to
Q4_K_M costs 2 points, not significant (p = 0.36).

What fine-tuning taught is the domain's conventions, written in the prompt but ignored by the
base model: power in MW turned into energy (MWh = sum of MW x 0.5 h), "renewable" and
"low-carbon" as defined sources, peak hours and seasons, regional aliases ("PACA", "Alta
Francia" for Hauts-de-France), running sums, national totals over the 12 regions. Of 21 sampled
zero-shot errors, 16 are fixed; invented filters and unit slips disappear.

## How to use it

The model expects **the exact system prompt it was trained with**: instructions, the table
schema and the glossary of conventions, 4,528 tokens. Without it the model does not
know the table. The prompt is built by the project's code:

```bash
git clone https://github.com/zaninip/domain-text2sql && cd domain-text2sql
uv sync --extra model
```

```python
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from t2sql.eval.extract import extract_sql
from t2sql.eval.run import format_prompt
from t2sql.prompts import system_prompt

tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-1.7B")
model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen3-1.7B", dtype=torch.float16, device_map="auto"
)
model = PeftModel.from_pretrained(model, "zaninip/qwen3-1.7b-eco2mix-sql").merge_and_unload()

system = system_prompt(Path("domains/eco2mix"))
prompt = format_prompt(tokenizer, system, "Production éolienne en Bretagne en 2023, en GWh ?")
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
output = model.generate(**inputs, do_sample=False, max_new_tokens=256)
answer_ids = output[0, inputs["input_ids"].shape[1] :]  # the generated part only
print(extract_sql(tokenizer.decode(answer_ids, skip_special_tokens=True)))
```

`format_prompt` applies Qwen3's chat template with `enable_thinking=False`, as in training.
Decoding is greedy. Run the SQL on the éCO2mix DuckDB file built by `make data`; the project's
`t2sql.db` executes model output read-only, one `SELECT` only, with a timeout.

## Training

- **Data:** 61 hand-written question templates in 9 families (aggregation, average,
  comparison, extremes, ranking, rate, time series, classification, multiple conditions), each
  with French and Italian wordings and a reference SQL. 45 are split by template, 70/15/15,
  into train (31), validation (7) and test (7); 16 more are used for training only, so that
  every convention of a validation or test template is taught by at least two training
  templates. Every reference query was run on the real data; empty, all-NULL or all-zero
  results were dropped. Training set: 3,218 (question, SQL) pairs from 47 templates, half French,
  half Italian; validation 402, test 490.
- **Method:** QLoRA with [Unsloth](https://github.com/unslothai/unsloth) on Unsloth's 4-bit copy
  of the base model (`unsloth/Qwen3-1.7B-unsloth-bnb-4bit`); the adapter is evaluated and
  published on the original float16 weights. LoRA r = 16, alpha = 32, dropout 0, on all linear
  layers (17.4 M trainable parameters). Learning rate 2e-4, cosine schedule, 5 % warmup, batch
  1 x 16 gradient accumulation, AdamW 8-bit, fp16. Loss on the SQL answer only.
- **Checkpoint:** step 100 of 202 (half an epoch), chosen on validation by rules fixed before
  the results (learning rate and seed variants, paired McNemar).
- **Compute:** one free Kaggle T4; about 4 hours for the full epoch, peak memory 5.5 GB.

## Training it further

This adapter can be the starting point of more training, for instance on questions it gets
wrong. Three conditions matter:

1. **Same format.** New examples must go through the same system prompt, the same chat template
   (thinking off) and the same loss mask (loss on the SQL and the end-of-turn token only).
   `t2sql.train.sft.prepare_example` does exactly this for a chat record
   `{"messages": [system, user, assistant]}`; `t2sql.prompts.chat_messages` builds such a
   record.
2. **Mix old and new examples.** Training on new examples alone makes the model forget part of
   what it learned. Rebuild the original training data, identical, with `make data` and
   `make dataset`, and add the new examples to it.
3. **Load the adapter as trainable.** The adapter holds weights only, no optimizer state:
   training restarts from these weights with a fresh optimizer.

```python
import torch
from peft import PeftModel, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, BitsAndBytesConfig

quantization = BitsAndBytesConfig(
    load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16
)
base = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen3-1.7B", quantization_config=quantization, device_map="auto"
)
base = prepare_model_for_kbit_training(base)
model = PeftModel.from_pretrained(base, "zaninip/qwen3-1.7b-eco2mix-sql", is_trainable=True)
# then: examples from prepare_example, and a Trainer (or TRL's SFTTrainer, packing off)
```

With a larger dataset, a fresh run from the base model with the project's
`python -m t2sql.train.sft` (about 4 hours on a T4) is often cleaner and easier to evaluate
honestly. Either way, keep a test set of templates the model never trained on.

## Limitations

- **Relative dates are not learned.** "Last month", "the same month a year earlier": 0 of 70 on
  the test template. The project's demo is designed to work around it: it turns relative dates
  into explicit ones against today's date before the model sees the question, and refuses
  periods beyond the end of the data. The model itself still fails on them.
- **Invalid SQL on the least covered patterns.** On some templates the model writes queries
  that do not run (an aggregate in `WHERE`, `LIMIT` without the `GROUP BY` it needs), hence a
  valid-SQL rate below the base model's.
- **Italian below French** (58.0 % vs 70.2 %).
- **Sensitive to small changes.** A different seed or the Q4_K_M quantization moves many single
  answers, mostly on a few fragile templates, with a small net effect (about 2 points for
  quantization, up to 6 between seeds on validation). Judge it on totals, not on one question.
- **One table, one schema.** It knows this éCO2mix view and its conventions only. Questions
  outside it (other countries, prices, forecasts) get a query, not a refusal.
- **Synthetic questions.** Questions come from templates; real users phrase things more
  freely.

## Intended use

A demonstration of what fine-tuning a small model brings to a narrow text-to-SQL task, and a
starting point for similar work. Model-generated SQL is untrusted: run it read-only, with a
single-statement check and a timeout, as the project does.

## Data and licences

- **Data:** RTE éCO2mix regional consolidated and definitive data, from
  [ODRÉ](https://odre.opendatasoft.com/explore/dataset/eco2mix-regional-cons-def/), under the
  [Licence Ouverte v2.0 (Etalab)](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).
  Source: RTE / ODRÉ.
- **Base model:** Qwen3-1.7B by the Qwen team, Apache 2.0.
- **This adapter:** Apache 2.0.
