# 模型体积实测（2026-10-31T12:20:34.538Z）

模型：Xenova/bge-small-zh-v1.5；口径：权重 ONNX + config.json + tokenizer.json + tokenizer_config.json + vocab.txt。

| dtype | 权重文件 | ONNX MiB | 含 tokenizer 总 MiB | 压缩比（fp32/本行） |
|---|---|---|---|---|
| fp32 | model.onnx | 90.46 | 90.98 | 1 |
| fp16 | model_fp16.onnx | 45.3 | 45.82 | 1.99 |
| int8 | model_int8.onnx | 22.8 | 23.32 | 3.9 |
| uint8 | model_uint8.onnx | 22.8 | 23.32 | 3.9 |
| q8 | model_quantized.onnx | 22.9 | 23.42 | 3.88 |


> 数字来自 `fs.statSync().size`，不是文档抄录；可用 `npm run measure-models` 复测。
