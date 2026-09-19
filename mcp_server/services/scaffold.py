"""最小酷家乐工具插件骨架。

支持两种技术栈：
- ``vanilla``：原生 HTML/JS（page.html + page.js + vm.js），默认，与酷家乐官方
  miniapp-template 样例一致，本地服务由 http-server 提供。
- ``react-ts-webpack``：React 17 + TypeScript + Webpack 5 + @manycore/idp-sdk，
  与酷家乐官方 webpack-react-ts 样例一致。
"""

from __future__ import annotations

import json
from typing import Any

from mcp_server.rules import KujialeRuleEngine

SUPPORTED_STACKS = ("vanilla", "react-ts-webpack")


class KujialeScaffoldService:
    """返回可供 Coding Agent 继续扩展的最小合法骨架，不生成业务实现。"""

    def __init__(self, rules: KujialeRuleEngine):
        self.rules = rules

    def build(self, task: str = "", stack: str = "vanilla") -> dict[str, Any]:
        normalized = (stack or "vanilla").strip().lower()
        if normalized not in SUPPORTED_STACKS:
            return {
                "success": False,
                "status": "unsupported_stack",
                "message": f"不支持的技术栈 `{stack}`，可选值：{', '.join(SUPPORTED_STACKS)}。",
                "data": {},
            }
        if normalized == "react-ts-webpack":
            return self._build_react_ts(task)
        return self._build_vanilla(task)

    # ------------------------------------------------------------------
    # vanilla：原生 JS 骨架
    # ------------------------------------------------------------------

    def _build_vanilla(self, task: str) -> dict[str, Any]:
        constraints = [rule.as_constraint() for rule in self.rules.query("all", task, limit=8)]
        return {
            "platform": "kujiale",
            "plugin_type": "tool_plugin",
            "stack": "vanilla",
            "task": task,
            "description": (
                "基于原生 HTML/JS 的酷家乐工具插件骨架，与酷家乐官方 miniapp-template 样例一致。"
                "UI 视图层为 page.html + page.js（iframe），VM 入口为 vm.js，"
                "本地服务由 http-server --cors 提供。"
            ),
            "files": {
                "manifest.json": json.dumps(
                    {"name": "example", "version": "1.0.0", "frame": "page.html", "main": "vm.js"},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "page.html": """<!DOCTYPE html>
<html>

<head>
    <meta charset="utf-8" />
</head>

<body>
    <h3>演示1：调用获取当前账号信息接口</h3>
    <button onclick="getUserName()">getUserName</button>
    <div id="result1">...</div>
    </br>
    <hr size="10" color="black">
    <h3>演示2：小程序UI线程和VM线程的数据通信</h3>
    <input type="text" id="thing" placeholder="输入任意信息" />
    <button onclick="send()">send</button>
    <div id="result2">...</div>
    <!-- <button onclick="getDesignId()">getDesignId</button> -->

    </br>
    <hr size="10" color="black">
    <h3>演示3：小程序iframe窗口设置 </h3>
    <button onclick="resize() ">修改小程序iframe尺寸为600X600</button>
    <button onclick="position()">修改小程序iframe左上角的位置坐标为{x:0,y:0}</button>
    <button onclick="modal()">修改小程序iframe为模态框显示(居中显示,存在遮罩)</button>
    <button onclick="fullscreen()">修改小程序iframe尺寸为全屏</button>

</body>
<script src="page.js"></script>

</html>
""",
                "page.js": """'use strict';
// 小程序UI逻辑代码，此处无法调用酷家乐提供的接口！

function resize() {
    window.parent.postMessage({ type: 'resize' }, '*')
}

function fullscreen() {
    window.parent.postMessage({ type: 'fullscreen' }, '*')
}
function modal() {
    window.parent.postMessage({ type: 'modal' }, '*')
}

function position() {
    window.parent.postMessage({ type: 'position' }, '*')
}

function getUserName() {
    window.parent.postMessage({ type: 'getUserName' }, '*')
}

function send() {
    const input = document.getElementById('thing');
    window.parent.postMessage({ type: 'send', input: input.value }, '*'); // 发送消息至小程序VM代码
}

window.addEventListener('message', event => {
    if(event.data.action == 'getUserName'){
        document.getElementById('result1').innerText = event.data.value;
    }
    if(event.data.action == 'send'){
        document.getElementById('result2').innerText = event.data.value;
    }
    if(event.data.action == 'vmLog'){
        console.log('[vmLog]', event.data.value)
    }
});
""",
                "vm.js": """
IDP.Miniapp.view.defaultFrame.mount(IDP.Miniapp.view.mountPoints.main); //挂载小程序UI至右侧挂载点
IDP.Miniapp.view.setContainerOptions(IDP.Miniapp.view.defaultFrame, {  minimizable: true });//窗口是否支持最小化(就像浏览器右上角最小化按钮)
// 接收来自UI页面的消息
IDP.Miniapp.view.defaultFrame.onMessageReceive(data => {
    if (data.type === 'getUserName') {
        IDP.User.getUserDetailsAsync().then(result => {//调用获取当前账号信息接口
            let userName = result.userName;
            let words = `你好,【${userName}】`
            IDP.Miniapp.view.defaultFrame.postMessage({ action: 'getUserName', value: words });//发送给UI
        }).catch(e => {//因为线上VM环境 无法在浏览器中console日志和报错信息，不便于排查问题，所以建议将必要的日志发送给UI
            IDP.Miniapp.view.defaultFrame.postMessage({ action: 'vmLog', value: 'IDP.User.getUserDetailsAsync err:' + e.message });
        })
    }else if(data.type === 'send'){
        let words = `小程序VM收到了UI输入的信息【${data.input}】，并发送给UI`
        IDP.Miniapp.view.defaultFrame.postMessage({ action: 'send', value: words });//发送给UI
    }
    else if(data.type === 'resize'){
        IDP.Miniapp.view.setContainerOptions(IDP.Miniapp.view.defaultFrame,{windowMode:'windowed'});//文档说明：https://manual.kujiale.com/idp-sdk/latest/apis/idp-sdk.unexported.mainmountpointoptions.windowmode
        IDP.Miniapp.view.defaultFrame.resize(600, 600);//文档说明：https://manual.kujiale.com/idp-sdk/latest/apis/idp-sdk.unexported.framehost.resize
    }else if(data.type === 'fullscreen'){
        IDP.Miniapp.view.setContainerOptions(IDP.Miniapp.view.defaultFrame,{windowMode:'fullscreen'});//文档说明：https://manual.kujiale.com/idp-sdk/latest/apis/idp-sdk.unexported.mainmountpointoptions.windowmode
    }else if(data.type === 'modal'){
        IDP.Miniapp.view.setContainerOptions(IDP.Miniapp.view.defaultFrame,{windowMode:'modal'});
    }else if(data.type === 'position'){
        IDP.Miniapp.view.setContainerOptions(IDP.Miniapp.view.defaultFrame,{windowMode:'windowed'});
        IDP.Miniapp.view.setContainerOptions(IDP.Miniapp.view.defaultFrame,{position:{x:0,y:0}}); //文档说明：https://manual.kujiale.com/idp-sdk/latest/apis/idp-sdk.unexported.mainmountpointoptions.position
    }



});
""",
                "package.json": json.dumps(
                    {
                        "name": "template",
                        "version": "1.0.0",
                        "description": "template",
                        "homepage": "",
                        "license": "ISC",
                        "files": [],
                        "scripts": {"start": "http-server --cors -c-1"},
                        "devDependencies": {
                            "@manycore/idp-sdk": "^1.0.1",
                            "http-server": "^0.12.3",
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "README.md": """# 原生 HTML 酷家乐工具插件样例（vanilla）

基于原生 HTML/JS 的酷家乐工具插件骨架，与酷家乐官方 miniapp-template 样例一致。

## 使用方式
### 本地开发
- 执行下列命令
```bash
npm install
npm start
```
- 本地测试按照本地开发流程加载小程序（默认启动地址 http://localhost:8080）

## 校验
- 本地服务由 `http-server --cors -c-1` 提供，`--cors` 已开启跨域与 OPTIONS 预检；
  运行 `npm start` 启动本地服务后，运行期的 manifest/frame/main、CORS 与 OPTIONS 预检请自行验证。
- 原生 HTML 插件无需打包，`manifest/frame/main` 直接在根目录可访问，
  声明可运行前请自行用浏览器或 HTTP 客户端验证本地 HTTP Server 的 manifest/frame/main、CORS 与 OPTIONS 预检。

## 注意
- UI（page.html + page.js）运行在 iframe 内，不得调用 IDP 等接口；
  所有 IDP API 调用只能在 VM（vm.js）中进行。
- UI 与 VM 仅通过 postMessage 通信：UI 发送用 `type` 字段，VM 回应用 `action` 字段。
""",
            },
            "responsibilities": {
                "UI": ["page.html + page.js", "DOM", "用户交互", "window.parent.postMessage({ type })", "window.addEventListener('message')"],
                "VM": ["IDP API", "平台能力调用", "defaultFrame.mount/onMessageReceive/postMessage({ action })"],
                "manifest": ["frame=page.html", "main=vm.js"],
                "dev_server": ["package.json scripts.start = http-server --cors -c-1", "http-server --cors 提供 CORS 与 OPTIONS 预检", "manifest/frame/main 在根目录可访问"],
            },
            "communication_pattern": {
                "ui_to_vm": 'window.parent.postMessage({ type: "..." }, "*")',
                "vm_receive": "IDP.Miniapp.view.defaultFrame.onMessageReceive(data => { if (data.type === ...) })",
                "vm_to_ui": 'IDP.Miniapp.view.defaultFrame.postMessage({ action: "..." }, "*")',
                "ui_receive": 'window.addEventListener("message", event => { if (event.data.action === ...) })',
                "note": "UI 运行在 iframe 内，VM 运行在 IDP 沙箱；UI 不得直接调用 IDP，二者仅通过 postMessage 通信。约定：UI 发消息用 type 字段，VM 回应用 action 字段。",
            },
            "guidance": [
                "VM 中调用的 IDP API（如本骨架中的 IDP.User.getUserDetailsAsync()、"
                "IDP.Miniapp.view.defaultFrame.*、IDP.Miniapp.view.setContainerOptions）"
                "在使用前应先调用 `get_api` 核实其是否存在于 @manycore/idp-sdk；"
                "若知识库中无该接口，`get_api` 未命中时不应直接调用，否则可能违反 `KJL-API-001`（unknown_api）。",
                "推荐开发顺序：先 `get_plugin_constraints` 确认约束 → 用 `get_api` 核实所用 IDP 接口 → 编码。",
            ],
            "constraints": constraints,
        }

    # ------------------------------------------------------------------
    # react-ts-webpack：React 17 + TypeScript + Webpack 5 骨架
    # ------------------------------------------------------------------

    def _build_react_ts(self, task: str) -> dict[str, Any]:
        constraints = [rule.as_constraint() for rule in self.rules.query("all", task, limit=8)]
        # 打包类插件的「main 指向构建产物、对 build/ 校验」约束是 medium，
        # 在默认 8 条限量下易被 critical 规则挤出，这里显式追加保证可见。
        bundler_rule = self.rules.get("KJL-MANIFEST-007")
        if bundler_rule is not None and bundler_rule.id not in {item["rule_id"] for item in constraints}:
            constraints.append(bundler_rule.as_constraint())
        return {
            "platform": "kujiale",
            "plugin_type": "tool_plugin",
            "stack": "react-ts-webpack",
            "task": task,
            "description": (
                "基于 React 17 + TypeScript + Webpack 5 的酷家乐工具插件骨架，"
                "使用 @manycore/idp-sdk。UI 视图层为 React 组件（iframe），"
                "VM 入口通过 webpack 编译为 main.js，dev server 由 http-server + webpack 提供。"
            ),
            "files": {
                "manifest.json": json.dumps(
                    {"name": "webpack-react-ts", "version": "1.0.0", "frame": "page.html", "main": "main.js"},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "src/main.ts": """// 小程序主代码（VM），可以调用 IDP 相关接口

IDP.Miniapp.view.defaultFrame.mount(IDP.Miniapp.view.mountPoints.main);
IDP.Miniapp.view.defaultFrame.onMessageReceive(data => {
    if (data.action === 'getDesignId') {
        IDP.Miniapp.view.defaultFrame.postMessage({ action: 'designId', value: IDP.Design.getDesignId() })
    } else if (data.action === 'getUserId') {
        IDP.Miniapp.view.defaultFrame.postMessage({ action: 'userId', value: IDP.User.getUserId() })
    }
});
""",
                "src/view.tsx": """// 页面逻辑代码（UI），无法调用 IDP 等接口，但可以使用 react 等 UI 框架或库
import * as React from 'react';
import * as ReactDOM from 'react-dom';

interface State {
    userId: string;
    designId?: string;
}

class MyComponent extends React.PureComponent<{}, State> {
    state: State = {
        userId: '',
        designId: undefined
    };

    componentDidMount() {
        window.addEventListener('message', this.onmessage);
    }

    componentWillUnmount() {
        window.removeEventListener('message', this.onmessage);
    }

    private getDesignId = () => {
        window.parent.postMessage({ action: 'getDesignId' }, '*')
    }

    private getUserId = () => {
        window.parent.postMessage({ action: 'getUserId' }, '*')
    }

    private onmessage = (event: MessageEvent) => {
        if (event.data.action === 'userId') {
            this.setState({ userId: event.data.value });
        } else if (event.data.action === 'designId') {
            this.setState({ designId: event.data.value });
        }
    }

    render() {
        const { designId, userId } = this.state;
        return (
            <>
                <div>
                    <button onClick={this.getDesignId}>
                        getDesignId
                    </button>
                    <span style={{ marginLeft: "10px" }}>designId: {designId}</span>
                </div>
                <div style={{ marginTop: "10px" }}>
                    <button onClick={this.getUserId}>
                        getUserId
                    </button>
                    <span style={{ marginLeft: "10px" }}>userId: {userId}</span>
                </div>
            </>
        );
    }
}

ReactDOM.render(<MyComponent />, document.getElementById('react-container')!)
""",
                "src/page.html": """<!DOCTYPE html>
<html>

<head>
    <meta charset="utf-8">
</head>

<body>
    <div id="react-container"></div>
    <script src="./view.js"></script>
</body>

</html>
""",
                "webpack.config.js": """const path = require('path');

const mode = process.env.NODE_ENV === 'production' ? 'production' : 'development';

module.exports = [
    {
        mode,
        target: 'web',
        entry: {
            main: ['./src/main.ts'],
            view: ['./src/view.tsx']
        },
        output: {
            path: path.resolve(__dirname, 'build'),
            filename: '[name].js'
        },
        resolve: {
            modules: ['node_modules'],
            extensions: ['.js', '.jsx', '.ts', '.tsx'],
        },
        devtool: mode === 'production' ? false : 'source-map',
        module: {
            rules: [
                {
                    test: /\\.tsx?$/,
                    include: [
                        path.resolve(__dirname, 'src')
                    ],
                    exclude: [
                        /node_modules/,
                    ],
                    use: [
                        {
                            loader: 'ts-loader',
                        }
                    ]
                }
            ]
        }
    }
];
""",
                "tsconfig.json": json.dumps(
                    {
                        "compilerOptions": {
                            "allowJs": True,
                            "strict": True,
                            "target": "ES6",
                            "module": "ES6",
                            "moduleResolution": "node",
                            "sourceMap": True,
                            "jsx": "react",
                            "noUnusedLocals": True,
                            "allowUnreachableCode": False,
                            "typeRoots": ["./node_modules", "./node_modules/@types"],
                            "outDir": "build",
                            "types": ["@manycore/idp-sdk"],
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "package.json": json.dumps(
                    {
                        "name": "webpack-react-ts",
                        "version": "1.0.0",
                        "license": "ISC",
                        "private": True,
                        "scripts": {
                            "copy": "copyfiles -f src/manifest.json src/page.html build/",
                            "start": (
                                'rimraf ./build && npm run copy && concurrently "webpack --watch" '
                                '"http-server build/ -c-1 --cors"'
                            ),
                            "build": "rimraf ./build && npm run copy && cross-env NODE_ENV=production webpack",
                        },
                        "devDependencies": {
                            "@manycore/idp-sdk": "^1.0.1",
                            "@types/react": "^17.0.11",
                            "@types/react-dom": "^17.0.7",
                            "concurrently": "^6.2.0",
                            "copyfiles": "^2.4.1",
                            "cross-env": "^7.0.3",
                            "http-server": "^0.12.3",
                            "rimraf": "^3.0.2",
                            "ts-loader": "^9.2.3",
                            "typescript": "~4.3.3",
                            "webpack": "^5.39.0",
                            "webpack-cli": "^4.7.2",
                        },
                        "dependencies": {
                            "react": "^17.0.2",
                            "react-dom": "^17.0.2",
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "README.md": """# webpack-react-ts
使用 react + ts 开发并使用 webpack 打包的酷家乐工具插件样例。

## 使用方式
### 本地开发
- 执行下列命令
```bash
npm install
npm start
```
- 本地测试按照本地开发流程加载插件（默认启动地址 http://localhost:8080）

### 编译发布
- 执行 `npm run build`
- 提交 `build` 目录下内容即可

## 校验
- 本地服务由 `http-server build/ --cors` 提供，`--cors` 已开启跨域与 OPTIONS 预检；
  运行 `npm start` 启动本地服务后，运行期的 manifest/frame/main、CORS 与 OPTIONS 预检请自行验证。
- `manifest.main` 指向的是 webpack 编译产物 `build/main.js`，因此请先 `npm run build`
  或 `npm start`，声明可运行前请自行用浏览器或 HTTP 客户端验证 `build/` 下的 manifest/frame/main、CORS 与 OPTIONS 预检。

## 注意
目前 webpack-dev-server 暂时不可用，会导致部分代码执行失败，本地开发模式下，更新代码后重启插件即可无需刷新页面
""",
            },
            "responsibilities": {
                "UI": ["React 组件", "用户交互", "window.parent.postMessage", "window.addEventListener('message')"],
                "VM": ["IDP API", "平台能力调用", "defaultFrame.mount/onMessageReceive/postMessage"],
                "manifest": ["frame=page.html", "main=main.js（webpack 编译产物）"],
                "build": ["webpack 双入口 main/view", "ts-loader 编译 TS/TSX"],
                "dev_server": [
                    "package.json scripts.start = webpack --watch + http-server build/ --cors",
                    "http-server --cors 提供 CORS 与 OPTIONS 预检",
                    "manifest/frame/main 在 build/ 下可访问",
                ],
            },
            "communication_pattern": {
                "ui_to_vm": 'window.parent.postMessage({ action: "..." }, "*")',
                "vm_receive": "IDP.Miniapp.view.defaultFrame.onMessageReceive(...) ",
                "vm_to_ui": 'IDP.Miniapp.view.defaultFrame.postMessage({ action: "..." }, "*")',
                "ui_receive": 'window.addEventListener("message", handler)',
                "note": "UI 运行在 iframe 内（React），VM 运行在 IDP 沙箱；二者仅通过 postMessage 通信，UI 不得直接调用 IDP。",
            },
            "guidance": [
                "VM 中调用的 IDP API（如本骨架中的 IDP.Design.getDesignId()、IDP.User.getUserId()）"
                "在使用前应先调用 `get_api` 核实其是否存在于 @manycore/idp-sdk；"
                "若知识库中无该接口，`get_api` 未命中时不应直接调用，否则可能违反 `KJL-API-001`（unknown_api）。",
                "推荐开发顺序：先 `get_plugin_constraints` 确认约束 → 用 `get_api` 核实所用 IDP 接口 → 编码。",
            ],
            "constraints": constraints,
        }
