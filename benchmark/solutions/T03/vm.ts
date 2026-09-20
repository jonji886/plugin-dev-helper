export function isWardrobe(): boolean {
  return IDP.Custom.Common.getCurrentToolType() === IDP.Custom.Common.ToolType.Wardrobe;
}
