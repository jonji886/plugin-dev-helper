# native-html

使用原生 HTML/JS 开发的小程序样例（golden template），与酷家乐官方 miniapp-template 一致。

## 使用方式

### 本地开发
- 执行下列命令
```bash
npm install
npm start
```
- 本地测试按照本地开发流程加载小程序（默认启动地址 http://localhost:8080）

## 校验
- 本地服务由 `http-server --cors -c-1` 提供，`--cors` 已开启跨域与 OPTIONS 预检，
  `KJL-DEV-003`/`KJL-DEV-004` 不会误报。
- 原生 HTML 插件无需打包，`manifest/frame/main` 直接在根目录可访问，
  直接对插件根目录执行 `validate_plugin_project` 与 `probe_plugin_dev_server` 即可。

## 注意
- UI（page.html + page.js）运行在 iframe 内，不得调用 IDP 等接口；
  所有 IDP API 调用只能在 VM（vm.js）中进行。
- UI 与 VM 仅通过 postMessage 通信：UI 发送用 `type` 字段，VM 回应用 `action` 字段。

## 与 MCP 工具链的对应

本目录是 `get_plugin_scaffold(stack=vanilla)` 的真实参考实现（golden template）。

- `frame`/`main` 指向 `page.html` 与 `vm.js`，均为根目录静态文件，无需构建。
- 本地开发服务由 `http-server --cors -c-1` 提供，`--cors` 已开启跨域与 OPTIONS 预检。

### 校验姿势
原生 HTML 插件不打包，直接对根目录校验：
```bash
npm install
npm start                 # 启动 http-server
validate_plugin_project --project_path ./
probe_plugin_dev_server  --project_path ./
```
