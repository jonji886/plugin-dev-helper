
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
