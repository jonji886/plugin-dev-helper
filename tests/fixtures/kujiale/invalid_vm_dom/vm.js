const defaultFrame = IDP.Miniapp.view.defaultFrame;
defaultFrame.onMessageReceive((event) => {
  if (event.data.action === "run") {
    document.title = "bad";
    fetch("https://example.com");
    setTimeout(() => {}, 10);
    console.log("bad");
    IDP.Miniapp.exit().then(() => {});
    const result = await IDP.Miniapp.exit();
    result.value = "bad";
  }
});
