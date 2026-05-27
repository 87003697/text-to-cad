import { handleHostedCadApi } from "../../viewer/src/server/vercelApi.mjs";

export default async function handler(req, res) {
  await handleHostedCadApi(req, res, { cadPath: "/__cad/reveal" });
}
