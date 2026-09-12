const http = require("http");
const fs = require("fs");
const path = require("path");
const root = __dirname;
const port = Number(process.env.PORT || 8082);
const origin = process.env.KUJIALE_ALLOWED_ORIGIN || "https://miniapp-1258830046.file.myqcloud.com";
const types = {".html": "text/html", ".js": "application/javascript", ".json": "application/json"};
function cors() { return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"}; }
http.createServer((request, response) => {
  const headers = cors();
  if (request.method === "OPTIONS") { response.writeHead(204, headers); response.end(); return; }
  const relative = path.posix.normalize(decodeURIComponent((request.url || "/").split("?")[0])).replace(/^\/+/, "");
  const file = path.resolve(root, relative);
  if (file !== root && !file.startsWith(`${root}${path.sep}`)) { response.writeHead(403, headers); response.end(); return; }
  fs.readFile(file, (error, body) => {
    if (error) { response.writeHead(404, headers); response.end(); return; }
    response.writeHead(200, {...headers, "Content-Type": types[path.extname(file)] || "application/octet-stream"});
    response.end(body);
  });
}).listen(port, "127.0.0.1");
