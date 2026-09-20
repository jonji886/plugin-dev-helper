export function sendToUi(data: unknown): void {
  IDP.Miniapp.view.defaultFrame.postMessage(data, "*");
}
