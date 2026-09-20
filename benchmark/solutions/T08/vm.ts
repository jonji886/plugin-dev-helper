export async function upload(miniappId: string, payload: string): Promise<void> {
  await IDP.Miniapp.uploadDataAsync({ miniappId, data: payload });
}
