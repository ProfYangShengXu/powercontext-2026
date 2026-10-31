# 端侧推理笔记

WebGPU 后端使用 fp16 权重；初始化前要 `requestAdapter()` 并检查 `shader-f16` 特性，
只判断 `navigator.gpu` 是否存在是不够的。设备不支持或初始化失败时，自动降级到
WASM + uint8 权重，并在界面徽标上明确显示当前后端。

推理放在 Web Worker 里，主线程只负责 UI。Worker 把 Float32Array 的 buffer 作为
Transferable 传回，避免结构化克隆复制几十万个浮点数。

模型文件同源托管在 /models/ 下，ORT 的 wasm 同源托管在 /ort/ 下，CSP 只允许
本站与 127.0.0.1:8787，所以任何云端推理请求都会被浏览器拦掉。
