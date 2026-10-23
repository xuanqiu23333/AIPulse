# 锁文件依赖许可清单（本地元数据核对）

2026-10-22 生成。Python 包范围来自 uv.lock，许可证来自本机已安装发行包 metadata；若安装版本与锁定版本不同，元数据仅作线索，发布前须核对锁定发行包。npm 范围与版本来自 package-lock.json，优先读取锁文件 license，缺失时查看本机 package.json。

此表覆盖开发/平台可选依赖，不表示全部都会进入前端 bundle。它不是完整许可证正文，也不是法律结论。安装版本差异和实际分发 bundle 的声明必须在公开发布前审阅；不要用根 MIT 替代第三方条款。此前未安装项的声明链接保留作元数据依据；本轮锁定只移除旧 Python 分支，不安装或升级 Python 包。没有调用新闻或模型服务。

## 2026-10-23 发布审查补充

Python >=3.11 重新锁定后，当前 uv.lock 有 98 个 package 记录（含本项目与平台/版本分支），package-lock 有 226 个依赖路径记录；不是 226 个独立前端运行依赖。前端锁文件全部具有 license 元数据：MIT 187、MIT-0 2、ISC 10、Apache-2.0 4、BSD-2-Clause 3、BlueOak-1.0.0 5、MPL-2.0 12、CC0-1.0 1、BSD-3-Clause 2。锁文件的声明与已安装元数据仍不等于已逐一审查全部发行包 LICENSE / NOTICE 正文。

未发现主项目采用 MIT 与这些依赖组合之间的静态必然冲突；分发依赖、修改 MPL 文件或发布 bundle 时仍须分别满足声明、适用 NOTICE 和源代码提供等义务，不能将第三方内容重新标为 AIPulse MIT。见 [MPL 官方 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/) 与 [Apache-2.0 正文](https://www.apache.org/licenses/LICENSE-2.0)。此轮未进行完整发行包法务审核，亦未进行联网漏洞数据库审计。正式二进制/bundle 发行前仍需按实际产物核对许可证材料。

## Python

| 包 | 锁定版本 | 本机声明 / 待确认 |
| --- | --- | --- |
| aiohappyeyeballs | 2.6.1 | Python Software Foundation License |
| aiohttp | 3.14.3 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache Software License |
| alembic | 1.20.0 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.14.2 | MIT |
| apscheduler | 3.11.3 | MIT License |
| asyncmy | 0.2.16 | Apache-2.0 |
| attrs | 25.3.0 | MIT |
| beautifulsoup4 | 4.13.4 | MIT License |
| certifi | 2025.8.3 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.4.2 | MIT License |
| click | 8.2.1 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| cryptography | 46.0.7 | Apache-2.0 OR BSD-3-Clause |
| dataclasses-json | 0.6.7 | MIT License |
| distro | 1.9.0 | Apache Software License |
| duckduckgo-search | 8.1.1 | MIT License |
| fastapi | 0.143.0 | MIT |
| frozenlist | 1.7.0 | Apache-2.0 |
| greenlet | 3.2.3 | MIT AND Python-2.0 |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| httpx-sse | 0.4.1 | MIT |
| idna | 3.15 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| jiter | 0.10.0 | MIT License |
| jsonpatch | 1.33 | BSD License |
| jsonpointer | 3.0.0 | BSD License |
| langchain-classic | 1.0.3 | MIT License |
| langchain-community | 0.4.1 | MIT |
| langchain-core | 1.6.9 | MIT License |
| langchain-ollama | 1.0.1 | MIT |
| langchain-openai | 1.1.14 | MIT License |
| langchain-protocol | 0.0.19 | MIT License |
| langchain-text-splitters | 1.1.2 | MIT License |
| langgraph | 1.2.13 | MIT |
| langgraph-checkpoint | 4.1.1 | MIT |
| langgraph-prebuilt | 1.1.0 | MIT |
| langgraph-sdk | 0.4.4 | MIT |
| langsmith | 0.7.31 | MIT |
| lxml | 6.1.0 | BSD-3-Clause |
| mako | 1.4.3 | MIT |
| markdownify | 1.1.0 | MIT License |
| markupsafe | 3.0.4 | BSD-3-Clause |
| marshmallow | 3.26.2 | MIT License |
| multidict | 6.6.3 | Apache License 2.0 |
| mypy | 1.17.1 | MIT（[锁定版本 PyPI 声明](https://pypi.org/pypi/mypy/1.17.1/json)；本机未安装） |
| mypy-extensions | 1.1.0 | MIT |
| numpy | 2.3.2 | BSD License |
| ollama | 0.6.1 | MIT |
| openai | 2.32.0 | Apache Software License |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| orjson | 3.11.7 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| ormsgpack | 1.12.2 | Apache-2.0 OR MIT |
| packaging | 25.0 | Apache Software License / BSD License |
| pathspec | 0.12.1 | MPL-2.0（[锁定版本 PyPI 声明](https://pypi.org/pypi/pathspec/0.12.1/json)；本机未安装） |
| pluggy | 1.6.0 | MIT License |
| primp | 0.15.0 | MIT License |
| propcache | 0.3.2 | Apache Software License |
| pycparser | 2.22 | BSD License |
| pydantic | 2.11.7 | MIT |
| pydantic-core | 2.33.2 | MIT License |
| pydantic-settings | 2.10.1 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| pytest-asyncio | 1.4.0 | Apache-2.0 |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| pyyaml | 6.0.2 | MIT License |
| regex | 2025.7.34 | Apache-2.0 AND CNRI-Python |
| requests | 2.33.1 | Apache Software License |
| requests-toolbelt | 1.0.0 | Apache Software License |
| ruff | 0.15.11 | MIT（[锁定版本 PyPI 声明](https://pypi.org/pypi/ruff/0.15.11/json)；本机未安装） |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License / Apache Software License |
| soupsieve | 2.9 | MIT |
| sqlalchemy | 2.0.42 | MIT |
| starlette | 1.8.0 | BSD-3-Clause |
| tavily-python | 0.7.23 | MIT License |
| tenacity | 9.1.2 | Apache Software License |
| tiktoken | 0.10.0 | MIT License |
| tqdm | 4.67.1 | MIT License / Mozilla Public License 2.0 (MPL 2.0) |
| typing-extensions | 4.14.1 | PSF-2.0 |
| typing-inspect | 0.9.0 | MIT License |
| typing-inspection | 0.4.2 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| tzlocal | 5.4.4 | MIT |
| urllib3 | 2.8.0 | MIT |
| uuid-utils | 0.14.1 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| websockets | 16.1.1 | BSD-3-Clause |
| xxhash | 3.5.0 | BSD License |
| yarl | 1.20.1 | Apache Software License |
| zstandard | 0.23.0 | BSD License |
## 前端（包括开发与可选平台包）

| 包 | 锁定版本 | 声明 |
| --- | --- | --- |
| @acemir/cssom | 0.9.32 | MIT |
| @asamuzakjp/css-color | 4.1.2 | MIT |
| @asamuzakjp/dom-selector | 6.8.1 | MIT |
| @asamuzakjp/nwsapi | 2.3.9 | MIT |
| @babel/helper-string-parser | 7.29.7 | MIT |
| @babel/helper-validator-identifier | 7.29.7 | MIT |
| @babel/parser | 7.29.9 | MIT |
| @babel/types | 7.29.8 | MIT |
| @csstools/color-helpers | 6.1.2 | MIT-0 |
| @csstools/css-calc | 3.4.3 | MIT |
| @csstools/css-color-parser | 4.2.6 | MIT |
| @csstools/css-parser-algorithms | 4.0.2 | MIT |
| @csstools/css-syntax-patches-for-csstree | 1.1.15 | MIT-0 |
| @csstools/css-tokenizer | 4.0.2 | MIT |
| @esbuild/aix-ppc64 | 0.28.2 | MIT |
| @esbuild/android-arm | 0.28.2 | MIT |
| @esbuild/android-arm64 | 0.28.2 | MIT |
| @esbuild/android-x64 | 0.28.2 | MIT |
| @esbuild/darwin-arm64 | 0.28.2 | MIT |
| @esbuild/darwin-x64 | 0.28.2 | MIT |
| @esbuild/freebsd-arm64 | 0.28.2 | MIT |
| @esbuild/freebsd-x64 | 0.28.2 | MIT |
| @esbuild/linux-arm | 0.28.2 | MIT |
| @esbuild/linux-arm64 | 0.28.2 | MIT |
| @esbuild/linux-ia32 | 0.28.2 | MIT |
| @esbuild/linux-loong64 | 0.28.2 | MIT |
| @esbuild/linux-mips64el | 0.28.2 | MIT |
| @esbuild/linux-ppc64 | 0.28.2 | MIT |
| @esbuild/linux-riscv64 | 0.28.2 | MIT |
| @esbuild/linux-s390x | 0.28.2 | MIT |
| @esbuild/linux-x64 | 0.28.2 | MIT |
| @esbuild/netbsd-arm64 | 0.28.2 | MIT |
| @esbuild/netbsd-x64 | 0.28.2 | MIT |
| @esbuild/openbsd-arm64 | 0.28.2 | MIT |
| @esbuild/openbsd-x64 | 0.28.2 | MIT |
| @esbuild/openharmony-arm64 | 0.28.2 | MIT |
| @esbuild/sunos-x64 | 0.28.2 | MIT |
| @esbuild/win32-arm64 | 0.28.2 | MIT |
| @esbuild/win32-ia32 | 0.28.2 | MIT |
| @esbuild/win32-x64 | 0.28.2 | MIT |
| @exodus/bytes | 1.16.0 | MIT |
| @jridgewell/gen-mapping | 0.3.13 | MIT |
| @jridgewell/remapping | 2.3.5 | MIT |
| @jridgewell/resolve-uri | 3.1.2 | MIT |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT |
| @jridgewell/trace-mapping | 0.3.31 | MIT |
| @lucide/vue | 1.61.0 | ISC |
| @napi-rs/lzma-linux-x64-gnu | 1.5.1 | MIT |
| @one-ini/wasm | 0.2.1 | MIT |
| @rolldown/pluginutils | 1.0.1 | MIT |
| @rollup/rollup-android-arm-eabi | 4.65.0 | MIT |
| @rollup/rollup-android-arm64 | 4.65.0 | MIT |
| @rollup/rollup-darwin-arm64 | 4.65.0 | MIT |
| @rollup/rollup-darwin-x64 | 4.65.0 | MIT |
| @rollup/rollup-freebsd-arm64 | 4.65.0 | MIT |
| @rollup/rollup-freebsd-x64 | 4.65.0 | MIT |
| @rollup/rollup-linux-arm-gnueabihf | 4.65.0 | MIT |
| @rollup/rollup-linux-arm-musleabihf | 4.65.0 | MIT |
| @rollup/rollup-linux-arm64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-arm64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-loong64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-loong64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-ppc64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-ppc64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-riscv64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-riscv64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-s390x-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-x64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-x64-musl | 4.65.0 | MIT |
| @rollup/rollup-openbsd-x64 | 4.65.0 | MIT |
| @rollup/rollup-openharmony-arm64 | 4.65.0 | MIT |
| @rollup/rollup-win32-arm64-msvc | 4.65.0 | MIT |
| @rollup/rollup-win32-ia32-msvc | 4.65.0 | MIT |
| @rollup/rollup-win32-x64-gnu | 4.65.0 | MIT |
| @rollup/rollup-win32-x64-msvc | 4.65.0 | MIT |
| @standard-schema/spec | 1.1.0 | MIT |
| @tailwindcss/node | 4.3.3 | MIT |
| @tailwindcss/oxide | 4.3.3 | MIT |
| @tailwindcss/oxide-android-arm64 | 4.3.3 | MIT |
| @tailwindcss/oxide-darwin-arm64 | 4.3.3 | MIT |
| @tailwindcss/oxide-darwin-x64 | 4.3.3 | MIT |
| @tailwindcss/oxide-freebsd-x64 | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-arm-gnueabihf | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-arm64-gnu | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-arm64-musl | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-x64-gnu | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-x64-musl | 4.3.3 | MIT |
| @tailwindcss/oxide-wasm32-wasi | 4.3.3 | MIT |
| @tailwindcss/oxide-win32-arm64-msvc | 4.3.3 | MIT |
| @tailwindcss/oxide-win32-x64-msvc | 4.3.3 | MIT |
| @tailwindcss/vite | 4.3.3 | MIT |
| @types/chai | 5.2.3 | MIT |
| @types/deep-eql | 4.0.2 | MIT |
| @types/estree | 1.0.9 | MIT |
| @types/node | 24.19.2 | MIT |
| @vitejs/plugin-vue | 6.0.10 | MIT |
| @vitest/expect | 4.1.12 | MIT |
| @vitest/mocker | 4.1.12 | MIT |
| estree-walker | 3.0.3 | MIT |
| @vitest/pretty-format | 4.1.12 | MIT |
| @vitest/runner | 4.1.12 | MIT |
| @vitest/snapshot | 4.1.12 | MIT |
| @vitest/spy | 4.1.12 | MIT |
| @vitest/utils | 4.1.12 | MIT |
| @volar/language-core | 2.4.28 | MIT |
| @volar/source-map | 2.4.28 | MIT |
| @volar/typescript | 2.4.28 | MIT |
| @vue/compiler-core | 3.5.44 | MIT |
| @vue/compiler-dom | 3.5.44 | MIT |
| @vue/compiler-sfc | 3.5.44 | MIT |
| @vue/compiler-ssr | 3.5.44 | MIT |
| @vue/devtools-api | 6.6.4 | MIT |
| @vue/language-core | 3.3.13 | MIT |
| @vue/reactivity | 3.5.44 | MIT |
| @vue/runtime-core | 3.5.44 | MIT |
| @vue/runtime-dom | 3.5.44 | MIT |
| @vue/server-renderer | 3.5.44 | MIT |
| @vue/shared | 3.5.44 | MIT |
| @vue/test-utils | 2.5.1 | MIT |
| abbrev | 5.0.0 | ISC |
| agent-base | 7.1.4 | MIT |
| alien-signals | 3.2.1 | MIT |
| assertion-error | 2.0.1 | MIT |
| balanced-match | 4.0.4 | MIT |
| bidi-js | 1.1.0 | MIT |
| brace-expansion | 5.0.12 | MIT |
| chai | 6.3.0 | MIT |
| commander | 14.0.3 | MIT |
| config-chain | 1.1.13 | MIT |
| convert-source-map | 2.0.0 | MIT |
| css-tree | 3.2.1 | MIT |
| cssstyle | 5.3.7 | MIT |
| csstype | 3.2.3 | MIT |
| data-urls | 6.0.1 | MIT |
| whatwg-mimetype | 5.0.0 | MIT |
| debug | 4.4.3 | MIT |
| decimal.js | 10.6.0 | MIT |
| detect-libc | 2.1.2 | Apache-2.0 |
| editorconfig | 3.0.2 | MIT |
| enhanced-resolve | 5.26.0 | MIT |
| entities | 7.0.1 | BSD-2-Clause |
| es-module-lexer | 2.3.2 | MIT |
| esbuild | 0.28.2 | MIT |
| estree-walker | 2.0.2 | MIT |
| expect-type | 1.4.0 | Apache-2.0 |
| fdir | 6.5.0 | MIT |
| fsevents | 2.3.3 | MIT |
| glob | 13.0.6 | BlueOak-1.0.0 |
| graceful-fs | 4.2.11 | ISC |
| html-encoding-sniffer | 6.0.0 | MIT |
| http-proxy-agent | 7.0.2 | MIT |
| https-proxy-agent | 7.0.6 | MIT |
| ini | 1.3.8 | ISC |
| is-potential-custom-element-name | 1.0.1 | MIT |
| jiti | 2.7.0 | MIT |
| js-beautify | 2.0.3 | MIT |
| js-cookie | 3.0.8 | MIT |
| jsdom | 27.4.0 | MIT |
| lightningcss | 1.32.0 | MPL-2.0 |
| lightningcss-android-arm64 | 1.32.0 | MPL-2.0 |
| lightningcss-darwin-arm64 | 1.32.0 | MPL-2.0 |
| lightningcss-darwin-x64 | 1.32.0 | MPL-2.0 |
| lightningcss-freebsd-x64 | 1.32.0 | MPL-2.0 |
| lightningcss-linux-arm-gnueabihf | 1.32.0 | MPL-2.0 |
| lightningcss-linux-arm64-gnu | 1.32.0 | MPL-2.0 |
| lightningcss-linux-arm64-musl | 1.32.0 | MPL-2.0 |
| lightningcss-linux-x64-gnu | 1.32.0 | MPL-2.0 |
| lightningcss-linux-x64-musl | 1.32.0 | MPL-2.0 |
| lightningcss-win32-arm64-msvc | 1.32.0 | MPL-2.0 |
| lightningcss-win32-x64-msvc | 1.32.0 | MPL-2.0 |
| lru-cache | 11.5.3 | BlueOak-1.0.0 |
| magic-string | 0.30.21 | MIT |
| mdn-data | 2.27.1 | CC0-1.0 |
| minimatch | 10.2.6 | BlueOak-1.0.0 |
| minipass | 7.1.3 | BlueOak-1.0.0 |
| ms | 2.1.3 | MIT |
| muggle-string | 0.4.1 | MIT |
| nanoid | 3.3.20 | MIT |
| nopt | 10.0.1 | ISC |
| obug | 2.2.1 | MIT |
| parse5 | 8.0.1 | MIT |
| entities | 8.1.0 | BSD-2-Clause |
| path-browserify | 1.0.1 | MIT |
| path-scurry | 2.0.2 | BlueOak-1.0.0 |
| pathe | 2.0.3 | MIT |
| picocolors | 1.1.1 | ISC |
| picomatch | 4.0.7 | MIT |
| postcss | 8.5.31 | MIT |
| proto-list | 1.2.4 | ISC |
| punycode | 2.3.1 | MIT |
| require-from-string | 2.0.2 | MIT |
| rollup | 4.65.0 | MIT |
| saxes | 6.0.0 | ISC |
| semver | 7.8.5 | ISC |
| siginfo | 2.0.0 | ISC |
| source-map-js | 1.2.2 | BSD-3-Clause |
| stackback | 0.0.2 | MIT |
| std-env | 4.3.0 | MIT |
| symbol-tree | 3.2.4 | MIT |
| tailwindcss | 4.3.3 | MIT |
| tapable | 2.3.3 | MIT |
| tinybench | 2.9.0 | MIT |
| tinyexec | 1.3.1 | MIT |
| tinyglobby | 0.2.17 | MIT |
| tinyrainbow | 3.2.0 | MIT |
| tldts | 7.4.19 | MIT |
| tldts-core | 7.4.19 | MIT |
| tough-cookie | 6.0.2 | BSD-3-Clause |
| tr46 | 6.0.0 | MIT |
| typescript | 5.9.3 | Apache-2.0 |
| undici-types | 7.24.6 | MIT |
| vite | 7.3.7 | MIT |
| vitest | 4.1.12 | MIT |
| vscode-uri | 3.2.0 | MIT |
| vue | 3.5.44 | MIT |
| vue-component-type-helpers | 3.3.13 | MIT |
| vue-router | 4.6.4 | MIT |
| vue-tsc | 3.3.13 | MIT |
| w3c-xmlserializer | 5.0.0 | MIT |
| webidl-conversions | 8.0.1 | BSD-2-Clause |
| whatwg-mimetype | 4.0.0 | MIT |
| whatwg-url | 15.1.0 | MIT |
| why-is-node-running | 2.3.0 | MIT |
| ws | 8.22.0 | MIT |
| xml-name-validator | 5.0.0 | Apache-2.0 |
| xmlchars | 2.2.0 | MIT |
