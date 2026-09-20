export async function upload(miniappId: string, data: string): Promise<void> {
  await IDP.Miniapp.uploadDataAsync({ miniappId, data });
}
