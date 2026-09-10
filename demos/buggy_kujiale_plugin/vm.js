const defaultFrame = IDP.Miniapp.view.defaultFrame;

defaultFrame.onMessageReceive((event) => {
  if (event.data.action === "wrongAction") {
    // 错误：VM 运行环境不支持 DOM、网络请求和定时器。
    document.title = "bad";
    fetch("https://example.com");
    setTimeout(() => {}, 100);
    console.error("核心结果依赖 console");
    IDP.Miniapp.exit().then(() => {
      defaultFrame.postMessage({ action: "unhandledResult" }, "*");
    });
  }
});
