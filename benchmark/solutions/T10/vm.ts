export function placeWindow(): void {
  IDP.Miniapp.view.setContainerOptions(IDP.Miniapp.view.defaultFrame, {
    windowMode: "windowed",
    position: { x: 120, y: 120 },
    minimizable: true,
  });
}
