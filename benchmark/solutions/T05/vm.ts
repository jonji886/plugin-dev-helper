export function listenUiMessages(handler: (data: unknown) => void): void {
  IDP.Miniapp.view.defaultFrame.onMessageReceive((data: unknown) => handler(data));
}
