const defaultFrame = IDP.Miniapp.view.defaultFrame;

defaultFrame.onMessageReceive((event) => {
  if (event.data.action === "getDesignJson") {
    defaultFrame.postMessage({ action: "designJsonResult", data: "TODO" }, "*");
  }
});
