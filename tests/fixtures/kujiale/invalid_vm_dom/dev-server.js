const http = require("http");
const fs = require("fs");
const path = require("path");
const root = __dirname; const port = Number(process.env.PORT || 8082);
const origin = "https://miniapp-1258830046.file.myqcloud.com";
function cors() { return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"}; }
http.createServer((request, response) => { const headers = cors(); if (request.method === "OPTIONS") { response.writeHead(204, headers); response.end(); return; } const file = path.resolve(root, decodeURIComponent((request.url || "/").split("?")[0]).replace(/^\/+/, "")); fs.readFile(file, (error, body) => { if (error) { response.writeHead(404, headers); response.end(); return; } response.writeHead(200, {...headers, "Content-Type": "text/plain"}); response.end(body); }); }).listen(port, "127.0.0.1");
