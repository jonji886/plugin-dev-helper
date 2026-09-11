const defaultFrame = IDP.Miniapp.view.defaultFrame;
defaultFrame.onMessageReceive((event) => {
  if (event.data.action === "vmRequest") return;
});
defaultFrame.postMessage({ action: "vmResponse", data: "bad" }, "*");
