const defaultFrame = IDP.Miniapp.view.defaultFrame;
defaultFrame.onMessageReceive((event) => {
  if (event.data.action === "noop") return;
});
