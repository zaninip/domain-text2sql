---
license: apache-2.0
base_model: zaninip/qwen3-1.7b-eco2mix-sql
base_model_relation: quantized
pipeline_tag: text-generation
language:
  - fr
  - it
tags:
  - gguf
  - llama.cpp
  - text-to-sql
  - sql
  - duckdb
  - energy
  - eco2mix
---

# Qwen3-1.7B éCO2mix SQL (GGUF)

CPU versions of [zaninip/qwen3-1.7b-eco2mix-sql](https://huggingface.co/zaninip/qwen3-1.7b-eco2mix-sql),
a model that turns French and Italian questions about the French electricity system (RTE
éCO2mix open data) into DuckDB SQL. They are the two models of the project's demo, which
answers each question with both side by side:
[github.com/zaninip/domain-text2sql](https://github.com/zaninip/domain-text2sql).

| File | Model | Size | Test execution accuracy |
|---|---|---:|---:|
| `qwen3-1.7b-eco2mix-sql-Q4_K_M.gguf` | fine-tuned (the adapter merged into Qwen3-1.7B) | 1.11 GB | **62.0 %** |
| `qwen3-1.7b-base-Q4_K_M.gguf` | Qwen3-1.7B as released, for comparison | 1.11 GB | 11.6 % |

Test split: 490 questions from 7 templates never seen in training. In float16 the same two
models score 64.1 % and 14.3 %: quantization costs about 2 points, not significant for the
fine-tuned model (p = 0.36, paired McNemar). Results, training and limitations are in the
[adapter's model card](https://huggingface.co/zaninip/qwen3-1.7b-eco2mix-sql).

## How they were made

Both files come from the same pipeline (`python -m t2sql.export.gguf` in the project), so that
the comparison is fair:

1. Qwen3-1.7B loaded in float16; for the fine-tuned file, the LoRA adapter (step 100) merged in.
2. Saved with `save_pretrained`, which drops the base repo's extra copy of the output head
   (Qwen3 ties it to the embeddings); both files hold the same 310 tensors.
3. Converted to a float16 GGUF and quantized to Q4_K_M with llama.cpp build b11669 (release
   v0.7.0), plain quantization, no importance matrix.

The float16 GGUF of the fine-tuned model answered exactly like the evaluated float16 model on
every test question checked. Q4_K_M then changes about half of the fine-tuned model's answers
word for word, with a small net effect on accuracy: judge the model on totals, not on a single
question.

## How to use them

The models expect the exact system prompt of training (instructions, table schema and
glossary of conventions, 4,528 tokens), built by the project's code. Pass the whole prompt as
text, rather than letting llama.cpp apply its own chat template, so that it is the evaluated
one byte for byte:

```python
from pathlib import Path

from huggingface_hub import hf_hub_download
from llama_cpp import Llama
from transformers import AutoTokenizer

from t2sql.eval.extract import extract_sql
from t2sql.eval.run import format_prompt
from t2sql.prompts import system_prompt

path = hf_hub_download("zaninip/qwen3-1.7b-eco2mix-sql-GGUF", "qwen3-1.7b-eco2mix-sql-Q4_K_M.gguf")
llm = Llama(path, n_ctx=8192, n_threads=4, verbose=False)
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-1.7B")  # builds the prompt text only

system = system_prompt(Path("domains/eco2mix"))  # in a clone of the project
prompt = format_prompt(tokenizer, system, "Production éolienne en Bretagne en 2023, en GWh ?")
answer = llm(prompt, max_tokens=256, temperature=0.0, repeat_penalty=1.0)
print(extract_sql(answer["choices"][0]["text"]))
```

**Speed on a CPU** (2 threads, laptop, Q4_K_M): the first question takes about 100 s, most of
it spent reading the 4,528-token prompt. llama-cpp-python then reuses that prompt from its
cache for the next questions, which take about 1.5 s to the first token and 15 s for a whole
answer. A demo should process the system prompt once at startup.

## Limitations

Those of the adapter, plus the small quantization loss. In short: relative dates ("last
month") are not learned, some queries do not run on the least covered patterns, Italian is
below French, and the model knows one table only. Model-generated SQL is untrusted: run it
read-only, one `SELECT` at a time, with a timeout.

## Licences

- **Data:** RTE éCO2mix, via [ODRÉ](https://odre.opendatasoft.com/explore/dataset/eco2mix-regional-cons-def/),
  [Licence Ouverte v2.0 (Etalab)](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).
- **Models:** derived from Qwen3-1.7B by the Qwen team, Apache 2.0; these files are Apache 2.0 too.
