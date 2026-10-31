import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';

const root = dirname(fileURLToPath(import.meta.url));
const ORT_DIR = join(root, 'node_modules', '@huggingface', 'transformers', 'dist');
const ORT_FILES = ['ort-wasm-simd-threaded.jsep.mjs', 'ort-wasm-simd-threaded.jsep.wasm'];

/**
 * 同源托管 ORT 运行时。
 *
 * 为什么不放 web/public/ort：Vite 7 明确禁止从源码 import /public 里的文件，
 * 而 ORT 会 `import()` wasm loader（.mjs），会直接 500。
 * 所以 dev 由中间件从 node_modules 直出，build 时 emit 成 dist/ort/*。
 */
function ortRuntime(): Plugin {
  return {
    name: 'ort-runtime',
    configureServer(server) {
      server.middlewares.use('/ort/', (req, res, next) => {
        const raw = (req.url ?? '').split('?')[0].replace(/^\/+/, '');
        const file = join(ORT_DIR, raw);
        if (!file.startsWith(ORT_DIR) || !existsSync(file) || !statSync(file).isFile()) {
          next();
          return;
        }
        res.setHeader(
          'content-type',
          file.endsWith('.wasm') ? 'application/wasm' : 'text/javascript; charset=utf-8',
        );
        res.setHeader('cache-control', 'no-cache');
        createReadStream(file).pipe(res);
      });
    },
    generateBundle() {
      for (const name of ORT_FILES) {
        const file = join(ORT_DIR, name);
        if (!existsSync(file)) {
          this.error(`缺少 ORT 运行时文件 ${name}；先 npm install`);
        }
        this.emitFile({ type: 'asset', fileName: `ort/${name}`, source: readFileSync(file) });
      }
    },
  };
}

export default defineConfig({
  root: 'web',
  publicDir: 'public',
  plugins: [ortRuntime()],
  // 模型运行时体积很大；显式预打包，避免 Worker 首次请求撞上 504（Outdated Optimize Dep）。
  optimizeDeps: {
    include: ['@huggingface/transformers'],
    esbuildOptions: { target: 'es2022' },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // WSL 的 Windows 盘挂载（9p）收不到 inotify 事件，HMR 需要 polling。
    watch: { usePolling: true, interval: 800 },
  },
  worker: { format: 'es' },
  build: {
    outDir: '../web-dist',
    emptyOutDir: true,
    target: 'es2022',
  },
});
