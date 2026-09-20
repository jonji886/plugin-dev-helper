// 小程序主代码，可以调用IDP相关接口

IDP.Miniapp.view.defaultFrame.mount(IDP.Miniapp.view.mountPoints.main);
IDP.Miniapp.view.defaultFrame.onMessageReceive(data => {
    if (data.action === 'getDesignId') {
        IDP.Miniapp.view.defaultFrame.postMessage({ action: 'designId', value: IDP.Design.getDesignId() })
    } else if (data.action === 'getUserId') {
        IDP.Miniapp.view.defaultFrame.postMessage({ action: 'userId', value: IDP.User.getUserId() })
    }
});
