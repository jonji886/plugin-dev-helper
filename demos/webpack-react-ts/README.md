# webpack-react-ts

使用 react + ts 开发并使用 webpack 打包的小程序样例（golden template）。

## 使用方式

### 本地开发
- 执行下列命令
```bash
npm install
npm start
```
- 本地测试按照本地开发流程加载小程序（默认启动地址 http://localhost:8080）

### 编译发布
- 执行 `npm run build`
- 提交 `build` 目录下内容即可

## 注意
目前 webpack-dev-server 暂时不可用，会导致部分代码执行失败，本地开发模式下，更新代码后重启小程序即可无需刷新页面

## 与 MCP 工具链的对应

本目录是 `get_plugin_scaffold(stack=react-ts-webpack)` 的真实参考实现（golden template）。

- `frame`/`main` 指向 `page.html` 与 `main.js`，二者均为 webpack 编译产物，位于 `build/`。
- `manifest.json` 在源码中放在 `src/`，由 `npm run copy` 复制到 `build/`（与 `webpack.config.js` 产物同目录）。
- 本地开发服务由 `http-server build/ -c-1 --cors` 提供，`--cors` 已开启跨域与 OPTIONS 预检，
  `KJL-DEV-003`/`KJL-DEV-004` 不会误报。

### 校验姿势
打包类插件的 `main` 是构建产物，因此**不要对源码目录 `src/` 校验**，而应对打包输出目录校验：
```bash
npm install
npm run build            # 生成 build/manifest.json、build/page.html、build/main.js、build/view.js
# 再对 build/ 目录执行：
validate_plugin_project --project_path build/
probe_plugin_dev_server --project_path build/
```
