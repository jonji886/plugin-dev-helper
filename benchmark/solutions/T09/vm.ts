// IDP.Miniapp.closeMiniapp 在 SDK 中不存在（get_api 返回 not_found，candidate_symbols 无匹配）。
// 不编造调用；最接近的真实 API 是 IDP.Miniapp.exit()，语义为「退出小程序」，与「关闭」不等价，需产品确认后再实现。
export function closeMiniapp(): void {
  throw new Error("待确认：SDK 中不存在 closeMiniapp，请用 IDP.Miniapp.exit 或确认真实 API");
}
